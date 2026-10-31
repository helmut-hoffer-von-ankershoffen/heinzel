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
from heinzel.modules.system.prerequisite._host import APPLE_SHIMS, ToolHost

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
        self._developer_tools: bool | None = None

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

    def _developer_tools_ready(self) -> bool:
        """Whether Apple's Command Line Tools exist, asked once; their stubs open a GUI installer otherwise."""
        if self._developer_tools is None:
            try:
                self._developer_tools = self.host.run(["xcode-select", "-p"], PROBE_TIMEOUT).returncode == 0
            except OSError:
                self._developer_tools = False
        return self._developer_tools

    def _candidates(self, let: PrerequisiteLet) -> list[str]:
        found = self.host.which(let.name, self.host.search_path)
        homebrew = [str(Path(location).parent / let.name) for location in self.host.brew_locations]
        candidates = [path for path in dict.fromkeys(([found] if found else []) + homebrew) if path.startswith("/")]
        if self.host.platform == "darwin":
            return [path for path in candidates if not path.startswith(APPLE_SHIMS) or self._developer_tools_ready()]
        return candidates

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

    def _probe(self, record: ModuleRecord, let: PrerequisiteLet, *, run: bool) -> PrerequisiteCheck:
        definition = definition_of(record, let)
        too_old: PrerequisiteCheck | None = None
        for candidate in self._candidates(let):
            if not self.host.is_executable(candidate):
                continue
            if not run:
                self.host.add_to_path(str(Path(candidate).parent))
                return PrerequisiteCheck(
                    requirement=definition,
                    status="available",
                    path=candidate,
                    detail="Found; `hz system prerequisite check` verifies its version.",
                )
            if (version := self._version(let, candidate)) is None:
                continue
            if let.minimum_version is not None and version < Version(let.minimum_version):
                too_old = too_old or PrerequisiteCheck(
                    requirement=definition,
                    status="incompatible",
                    path=candidate,
                    version=str(version),
                    detail=f"Version {let.minimum_version} or newer is needed; update {let.name} with its installer.",
                )
                continue
            self.host.add_to_path(str(Path(candidate).parent))  # a launchd or GUI process lacks Homebrew's directory
            return PrerequisiteCheck(requirement=definition, status="available", path=candidate, version=str(version))
        return too_old or self._missing(definition)

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
        self, module: str | None = None, *, context: PrerequisiteContext = "setup", probe: bool = True
    ) -> PrerequisiteReport:
        """Check applicable executables without running an installer or signing into any account.

        Raises:
            HeinzelError: If the context is unknown or the module is not installed.
        """
        if context not in PREREQUISITE_CONTEXTS:
            msg = "Choose the install, setup or feature prerequisite context."
            raise HeinzelError(msg)
        checks = [
            self._skip(record, let, context) or self._probe(record, let, run=probe)
            for record, let in self._entries(module)
        ]
        return report_of(self._without_conflicts(checks))

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
