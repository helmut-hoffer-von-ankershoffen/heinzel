"""A generic external-tool engine; every executable and package recipe comes from its owning module (ADR 0112)."""

import re
from collections.abc import Callable
from pathlib import Path

from packaging.version import InvalidVersion, Version

from heinzel import (
    HeinzelError,
    Homebrew,
    Kernel,
    ManualInstall,
    ModuleRecord,
    PrerequisiteCheck,
    PrerequisiteContext,
    PrerequisiteDefinition,
    PrerequisiteLet,
    PrerequisiteReport,
    PrerequisiteService,
    Progress,
)
from heinzel.kernel import PREREQUISITE_CONTEXTS, PREREQUISITE_READY
from heinzel.modules.system.prerequisite._host import APPLE_COMMAND_LINE_TOOLS_BIN, ToolHost

PROBE_TIMEOUT = 10.0
INSTALL_TIMEOUT = 900.0
BREW_FLAGS = (
    ("HOMEBREW_NO_AUTO_UPDATE", "1"),
    ("HOMEBREW_NO_INSTALL_UPGRADE", "1"),
    ("HOMEBREW_NO_ENV_HINTS", "1"),
)
"""A prerequisite installs what is missing; it neither refreshes the formula index nor upgrades other packages."""
HOMEBREW_URL = "https://brew.sh"
TAP_SLASHES = 2
"""A package named `owner/tap/name` makes Homebrew add that tap first, so the person is told."""
_VERSION = re.compile(r"(?<![\d.])(\d++(?:\.\d++){1,3}+)(?![\d.])")
type Entry = tuple[ModuleRecord, PrerequisiteLet]


def definition_of(record: ModuleRecord, let: PrerequisiteLet) -> PrerequisiteDefinition:
    """The portable metadata of one declaration; it carries no predicate."""
    return PrerequisiteDefinition(
        module=record.qualname,
        plugin=None if record.origin.builtin else record.origin.distribution,
        name=let.name,
        title=let.title or let.name,
        purpose=let.purpose,
        when=let.when,
        condition=let.condition,
        macos=let.macos,
        linux=let.linux,
        minimum_version=let.minimum_version,
        requires_apple_developer_tools=let.requires_apple_developer_tools,
    )


def report_of(checks: list[PrerequisiteCheck]) -> PrerequisiteReport:
    """A report whose readiness follows from its checks alone; instructions and exit codes establish nothing."""
    return PrerequisiteReport(ready=all(check.status in PREREQUISITE_READY for check in checks), checks=checks)


class ToolPrerequisites(PrerequisiteService):
    """Resolve declarations, check tools locally, and install only explicitly selected missing macOS tools."""

    def __init__(self, kernel: Kernel, progress: Progress, host: ToolHost | None = None) -> None:
        """Look at `host` (the real machine by default) on behalf of the modules `kernel` has loaded."""
        self.kernel, self.progress = kernel, progress
        self.host = host or ToolHost.system()
        self._developer_directory: str | None = None
        self._developer_directory_error: str | None = None
        self._developer_payload_identity_unknown = False
        self._developer_directory_checked = False

    # Selection -------------------------------------------------------------------------------------------------------

    def _entries(self, module: str | None) -> list[Entry]:
        if module is not None and module not in {record.qualname for record in self.kernel.modules}:
            msg = "Select an installed, enabled module (`hz system prerequisite list` shows who declares what)."
            raise HeinzelError(msg)
        return [
            (record, let)
            for record, let in self.kernel.lets(PrerequisiteLet)
            if module is None or record.qualname == module
        ]

    def definitions(self, module: str | None = None) -> list[PrerequisiteDefinition]:
        """Export stable metadata without calling predicates, version probes or package managers."""
        return [definition_of(record, let) for record, let in self._entries(module)]

    def _skip(
        self, record: ModuleRecord, let: PrerequisiteLet, context: PrerequisiteContext
    ) -> PrerequisiteCheck | None:
        """A check that says why `let` is not part of `context`, or None when it is."""
        later = PREREQUISITE_CONTEXTS.index(let.when) > PREREQUISITE_CONTEXTS.index(context)
        selected_by_predicate = let.when == "feature" and context == "setup" and let.needed is not None
        if later and not selected_by_predicate:
            detail = "Needed only for the selected feature." if let.when == "feature" else "Needed in a later step."
            return PrerequisiteCheck(requirement=definition_of(record, let), status="skipped", detail=detail)
        if let.needed is None:
            return None
        needed = self._needed(let.needed)
        if needed is None:
            return PrerequisiteCheck(
                requirement=definition_of(record, let),
                status="failed",
                detail="The owner's activation condition could not be checked.",
            )
        if not needed:
            return PrerequisiteCheck(
                requirement=definition_of(record, let), status="skipped", detail="Not needed for the current selection."
            )
        return None

    def _needed(self, predicate: Callable[..., object]) -> bool | None:
        """The owner's predicate, or None when it fails or does not answer yes or no."""
        try:
            value = self.kernel.call(predicate)
        except Exception:  # noqa: BLE001 - a broken provider is a finding, never permission to install
            return None
        return value if isinstance(value, bool) else None

    # Looking ---------------------------------------------------------------------------------------------------------

    def _developer_tool(self, name: str) -> str | None:
        """Return an installed developer-tool binary without invoking Apple's potentially prompting shim."""
        self._developer_payload_identity_unknown = False
        if not self._developer_directory_checked:
            try:
                result = self.host.run(["/usr/bin/xcode-select", "-p"], PROBE_TIMEOUT)
                if result.returncode == 0 and result.stdout.strip() and not result.truncated:
                    selected = result.stdout.strip()
                    if Path(selected).is_absolute():
                        self._developer_directory = selected
                        self._developer_directory_error = None
                    else:
                        self._developer_directory_error = (
                            "`xcode-select -p` returned a non-absolute developer directory."
                        )
                else:
                    self._developer_directory_error = "`xcode-select -p` did not confirm an active developer directory."
            except OSError:
                self._developer_directory_error = (
                    "`xcode-select -p` could not be run; the Apple executable remains unverified."
                )
            self._developer_directory_checked = True
        if self._developer_directory is None:
            return None
        self._developer_directory_error = None
        candidate = str(Path(self._developer_directory) / "usr" / "bin" / name)
        if not self.host.is_executable(candidate):
            self._developer_directory_error = f"The selected developer directory has no executable `{name}` payload."
            return None
        try:
            if self.host.same_file(candidate, f"/usr/bin/{name}"):
                self._developer_directory_error = f"The selected `{name}` payload resolves to Apple's launcher."
                return None
        except OSError:
            self._developer_directory_error = (
                f"The selected `{name}` payload's physical identity could not be verified."
            )
            self._developer_payload_identity_unknown = True
            return None
        return candidate

    def _apple_launcher_without_developer_tools(self, definition: PrerequisiteDefinition) -> PrerequisiteCheck:
        """Report the unusable Apple shim without mislabeling it as absent or running it."""
        if self._developer_payload_identity_unknown:
            return PrerequisiteCheck(
                requirement=definition,
                status="failed",
                detail=(
                    f"{definition.title} resolves to Apple's `/usr/bin/{definition.name}` launcher, but the selected "
                    "developer-tool payload's physical identity could not be verified. The launcher was not run. "
                    f"Run `hz system prerequisite check --owner {definition.module}` again after checking file access."
                ),
            )
        check = self._missing(definition)
        reason = self._developer_directory_error or (
            f"The selected developer directory has no executable `{definition.name}` payload "
            "distinct from Apple's launcher."
        )
        check.detail = (
            f"{definition.title} resolves to Apple's `/usr/bin/{definition.name}` launcher, but {reason} "
            "The launcher was not run. " + check.detail
        )
        return check

    @staticmethod
    def _apple_launcher_version_failed(definition: PrerequisiteDefinition, executable: str) -> PrerequisiteCheck:
        """Report a selected developer-tool payload whose version command failed."""
        return PrerequisiteCheck(
            requirement=definition,
            status="failed",
            detail=(
                f"The selected Apple developer-tool binary `{executable}` did not return a usable "
                f"{definition.title} version. Apple's `/usr/bin/{definition.name}` launcher was not run."
            ),
        )

    def _candidates(self, let: PrerequisiteLet) -> list[str]:
        found = self.host.which(let.name, self.host.search_path)
        homebrew = [str(Path(location).parent / let.name) for location in self.host.brew_locations]
        apple_tools = (
            [str(APPLE_COMMAND_LINE_TOOLS_BIN / let.name)]
            if self.host.platform == "darwin" and let.requires_apple_developer_tools
            else []
        )
        return [
            path for path in dict.fromkeys(([found] if found else []) + homebrew + apple_tools) if path.startswith("/")
        ]

    def _version(self, let: PrerequisiteLet, executable: str) -> Version | None:
        try:
            result = self.host.run([executable, *let.version_args], PROBE_TIMEOUT)
        except OSError:
            return None
        match = _VERSION.search(result.stdout + "\n" + result.stderr)
        if result.returncode != 0 or result.truncated or match is None:
            return None
        try:
            return Version(match.group(1))
        except InvalidVersion:
            return None

    def _apple_launcher(self, let: PrerequisiteLet, candidate: str) -> bool | None:
        """Whether this is a declared system launcher, or unknown when physical identity cannot be read."""
        if self.host.platform != "darwin" or not any(
            owner.name == let.name and owner.requires_apple_developer_tools
            for _, owner in self.kernel.lets(PrerequisiteLet)
        ):
            return False
        try:
            return self.host.same_file(candidate, f"/usr/bin/{let.name}")
        except OSError:
            return None

    def _observe(
        self,
        record: ModuleRecord,
        let: PrerequisiteLet,
        candidate: str,
        *,
        run: bool,
    ) -> PrerequisiteCheck | None:
        """Observe exactly this executable, without changing command resolution."""
        definition = definition_of(record, let)
        if not candidate.startswith("/") or not self.host.is_executable(candidate):
            return None
        apple_launcher = self._apple_launcher(let, candidate)
        developer_tool = None
        if apple_launcher and run:
            developer_tool = self._developer_tool(let.name)
            if developer_tool is None:
                return self._apple_launcher_without_developer_tools(definition)
        executable = developer_tool or candidate
        if apple_launcher is None or not run:
            if apple_launcher is None:
                detail = "The executable's filesystem identity could not be verified. "
                detail += "Check file access, then run `hz system prerequisite check` again."
            elif apple_launcher:
                detail = (
                    f"Apple's `/usr/bin/{definition.name}` launcher is not verified. Run "
                    f"`hz system prerequisite check --owner {definition.module}` to check it without installing "
                    "anything."
                )
            else:
                detail = "Found; `hz system prerequisite check` verifies its version."
            return PrerequisiteCheck(
                requirement=definition,
                status="available" if apple_launcher is False else "failed",
                path=candidate if apple_launcher is False else None,
                detail=detail,
            )
        if (version := self._version(let, executable)) is None:
            return self._apple_launcher_version_failed(definition, executable) if apple_launcher else None
        if let.minimum_version is not None and version < Version(let.minimum_version):
            return PrerequisiteCheck(
                requirement=definition,
                status="incompatible",
                path=executable,
                version=str(version),
                detail=f"Version {let.minimum_version} or newer is needed; update {let.name} with its installer.",
            )
        return PrerequisiteCheck(requirement=definition, status="available", path=executable, version=str(version))

    def _probe(self, record: ModuleRecord, let: PrerequisiteLet, *, run: bool) -> PrerequisiteCheck:
        too_old: PrerequisiteCheck | None = None
        unverified: PrerequisiteCheck | None = None
        apple_tools_missing: PrerequisiteCheck | None = None
        for candidate in self._candidates(let):
            check = self._observe(record, let, candidate, run=run)
            if check is None:
                continue
            if check.status == "failed":
                unverified = unverified or check
                continue
            if check.status == "incompatible":
                too_old = too_old or check
                continue
            if check.status == "missing":
                apple_tools_missing = apple_tools_missing or check
                continue
            found = self.host.which(let.name, self.host.search_path)
            executable = check.path or candidate
            self.host.add_to_path(str(Path(executable).parent), first=found is not None and found != executable)
            return check
        return too_old or unverified or apple_tools_missing or self._missing(definition_of(record, let))

    def _final_paths(
        self, entries: list[Entry], checks: list[PrerequisiteCheck], *, run: bool
    ) -> list[PrerequisiteCheck]:
        """Reconcile every active finding after all directory choices; one tool may shadow an earlier one."""
        for index, ((record, let), check) in enumerate(zip(entries, checks, strict=True)):
            if check.status not in {"available", "incompatible", "missing"}:
                continue
            resolved = self.host.which(let.name, self.host.search_path)
            if resolved == check.path:
                continue
            observed = self._observe(record, let, resolved, run=run) if resolved is not None else None
            final = observed or self._missing(definition_of(record, let))
            if check.status == "available" and final.status != "available":
                final.detail = "The final PATH no longer selects a compatible executable for this tool. " + final.detail
            checks[index] = final
        return checks

    def _missing(self, definition: PrerequisiteDefinition) -> PrerequisiteCheck:
        """A missing tool with the plan for it on this machine: what `install` would run, or what to do by hand."""
        recipe = definition.macos
        if self.host.platform == "linux":
            detail = definition.linux.text
        elif self.host.platform != "darwin":
            detail = f"Install {definition.name} yourself; automatic prerequisites support macOS."
        elif isinstance(recipe, Homebrew):
            if self.host.find_brew() is not None:
                kind = "--cask" if recipe.cask else "--formula"
                tap = " (from a third-party tap)" if recipe.package.count("/") == TAP_SLASHES else ""
                detail = f"Homebrew installs it: brew install {kind} {recipe.package}{tap}."
                return PrerequisiteCheck(requirement=definition, status="missing", detail=detail, automatic=True)
            detail = f"Install Homebrew first ({HOMEBREW_URL}), then run `hz system prerequisite install --confirm`."
        elif isinstance(recipe, ManualInstall):
            detail = recipe.text
        else:
            detail = f"Install {definition.name} yourself; no macOS installer is declared. {definition.linux.text}"
        return PrerequisiteCheck(requirement=definition, status="missing", detail=detail)

    def _without_conflicts(self, checks: list[PrerequisiteCheck]) -> list[PrerequisiteCheck]:
        """Fail closed when enabled modules ask Homebrew for different packages for one missing executable."""
        recipes: dict[str, set[Homebrew]] = {}
        for _, let in self.kernel.lets(PrerequisiteLet):
            if isinstance(let.macos, Homebrew):
                recipes.setdefault(let.name, set()).add(let.macos)
        conflicting = {name for name, choices in recipes.items() if len(choices) > 1}
        for check in checks:
            if check.status == "missing" and check.requirement.name in conflicting:
                check.status, check.automatic = "failed", False
                check.detail = "Modules declare conflicting Homebrew packages for this tool; none was selected."
        return checks

    def check(
        self,
        module: str | None = None,
        *,
        context: PrerequisiteContext = "setup",
        probe: bool = True,
    ) -> PrerequisiteReport:
        """Check applicable executables without running an installer or signing into any account.

        Raises:
            HeinzelError: If the context is unknown or the module is not installed.
        """
        if context not in PREREQUISITE_CONTEXTS:
            msg = "Choose the install, setup or feature prerequisite context."
            raise HeinzelError(msg)
        entries = self._entries(module)
        checks = [self._skip(record, let, context) or self._probe(record, let, run=probe) for record, let in entries]
        return report_of(self._without_conflicts(self._final_paths(entries, checks, run=probe)))

    # Installing ------------------------------------------------------------------------------------------------------

    def _homebrew_failures(self, brew: str, missing: list[PrerequisiteCheck]) -> dict[Homebrew, str]:
        """Install each distinct recipe once; the failure of each that did not install, by recipe."""
        failures: dict[Homebrew, str] = {}
        recipes = sorted(
            {check.requirement.macos for check in missing if isinstance(check.requirement.macos, Homebrew)},
            key=lambda recipe: (recipe.cask, recipe.package),
        )
        for cask in (False, True):
            packages = [recipe.package for recipe in recipes if recipe.cask == cask]
            if not packages:
                continue
            command = [brew, "install", "--cask" if cask else "--formula", *packages]
            with self.progress.task("Installing with Homebrew: " + ", ".join(packages)):
                reason = self._brew_failure(command)
            if reason:
                failures.update((Homebrew(package, cask=cask), reason) for package in packages)
        return failures

    def _brew_failure(self, command: list[str]) -> str:
        """Why the command did not succeed, or an empty string when it did."""
        try:
            result = self.host.run(command, INSTALL_TIMEOUT, BREW_FLAGS)
        except OSError:
            return "Homebrew could not be started."
        if result.timed_out:
            return "Homebrew took too long."
        return "" if result.returncode == 0 else f"Homebrew exited with status {result.returncode}."

    def install(self, module: str | None = None, *, context: PrerequisiteContext = "setup") -> PrerequisiteReport:
        """Install missing declared Homebrew packages on macOS; anything else becomes manual instructions.

        Raises:
            HeinzelError: If the context is unknown or the module is not installed.
        """
        initial = self.check(module, context=context)
        automatic = [check for check in initial.checks if check.status == "missing" and check.automatic]
        for check in initial.checks:
            if check.status == "missing" and not check.automatic:
                check.status = "manual"
        brew = self.host.find_brew() if automatic else None
        if not automatic or brew is None:
            return report_of(initial.checks)
        failures = self._homebrew_failures(brew, automatic)
        self.host.add_to_path(str(Path(brew).parent))
        return self._settled(module, context, automatic, failures)

    def _settled(
        self,
        module: str | None,
        context: PrerequisiteContext,
        attempted: list[PrerequisiteCheck],
        failures: dict[Homebrew, str],
    ) -> PrerequisiteReport:
        """Probe again: only a working executable counts, whatever the package manager said."""
        wanted = {(check.requirement.module, check.requirement.name) for check in attempted}
        after = self.check(module, context=context)
        for check in after.checks:
            recipe = check.requirement.macos
            if check.status == "missing" and (check.requirement.module, check.requirement.name) not in wanted:
                check.status = "manual"
            elif (check.requirement.module, check.requirement.name) not in wanted:
                continue
            elif check.status == "available":
                check.status = "installed"
            elif check.status == "missing" and isinstance(recipe, Homebrew):
                reason = failures.get(recipe, "Homebrew finished, but the tool is still not usable.")
                check.status = "failed"
                check.detail = f"{reason} Run `brew install {recipe.package}` yourself to see why."
        return report_of(after.checks)
