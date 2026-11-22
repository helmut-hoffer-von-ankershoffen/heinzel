"""The update service's install: one `uv tool install` of hz and its plugins, converged and verified."""

import importlib.metadata
import json
import re
import shlex
import tempfile
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from heinzel import HeinzelError
from heinzel.kernel import (
    CorePin,
    DaemonHandoverPlan,
    DaemonMutationScope,
    subprocess_environment,
)
from heinzel.modules.system._models import PluginUpdate, UpdateResult
from heinzel.modules.system.plugin._sources import dependents, git_credentials, required_by
from heinzel.modules.system.updater._announced import (
    Announcement,
    environment_versions,
    installed_commit,
    plugin_commit,
)
from heinzel.modules.system.updater._update_heads import UpdateHeads
from heinzel.modules.system.updater._update_rules import (
    AGREED,
    Installed,
    Mode,
    blocking_plugins,
    index_preferred_release,
    install_message,
    is_conflict,
    pinned_ref,
    stamp,
    uv_reason,
    verify_core_selection,
    with_summary,
)


class UpdateInstall(UpdateHeads):
    """Installs hz and its plugins in one resolver step and verifies the result."""

    def _install_announced(
        self,
        requirements: list[str],
        policy: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
        expected: dict[str, str],
        announced: Announcement,
    ) -> str:
        """Run uv with every version held, then read the new metadata; the generation installed.

        Raises:
            HeinzelError: If uv fails, or the metadata names other versions or another commit than announced.
        """
        guards, sources, constraints = policy
        command = self._installation.tool_install_command(requirements[0], requirements[1:], reinstall=False)
        moving = (["heinzel"] if announced.core else []) + list(announced.plugins)
        for name in moving:  # only what was announced is asked again and reinstalled; the rest stays as it is
            command += ["--refresh-package", name, "--reinstall-package", name]
        if announced.exclude_newer is not None:
            command += ["--exclude-newer", stamp(announced.exclude_newer)]
        if announced.exclude_newer_core is not None:
            command += ["--exclude-newer-package", f"heinzel={stamp(announced.exclude_newer_core)}"]
        # uv records both in the receipt (`[tool.options]`); `added_options` names them so they are taken out again.
        self._installation.run(
            command,
            "uv couldn't install exactly the announced versions",
            f"Installing hz {expected['heinzel']}",
            git_credentials(subprocess_environment()),
            additional_constraints=(*guards, *sources),
        )
        generation = self._installation.startup_fingerprint()
        self._installation.verify_installed_requirements(
            requirements, expected_version=expected["heinzel"], constraints=constraints
        )
        held = dict(constraint.split("==", 1) for constraint in guards)
        found = environment_versions(self._installation.prefix.resolve(strict=True))
        if differ := [
            f"{name} {found.get(name, 'nothing')} instead of {version}"
            for name, version in held.items()
            if found.get(name) != version
        ]:
            msg = f"uv installed {', '.join(differ)}"
            raise HeinzelError(msg)
        installed = installed_commit(self._installation.prefix.resolve(strict=True))
        commit = requirements[0].rsplit("@", 1)[1] if " @ git+" in requirements[0] else None
        if commit is not None and installed != commit:
            msg = f"hz came from commit {(installed or 'none')[:12]}, not {commit[:12]}"
            raise HeinzelError(msg)
        for name, wanted in announced.plugin_commits.items():  # a tagged plugin: exactly the commit announced
            if (came := plugin_commit(self._installation.prefix.resolve(strict=True), name)) != wanted:
                msg = f"{name} came from commit {(came or 'none')[:12]}, not {wanted[:12]}"
                raise HeinzelError(msg)
        return generation

    def _install(  # noqa: PLR0913 - preserve one validated target for update and recovery
        self,
        core: str,
        pin: CorePin,
        plugins: list[CorePin],
        *,
        current: str,
        latest: str | None,
        target: str | None,
        channel: str | None = None,
        automatic: bool = True,
        generation: str | None = None,
    ) -> UpdateResult:
        with self._interpreter_for(self._target_release(pin, current, latest, target)), self._mutation() as scope:
            plan = scope.capture() if scope is not None else None
            with self._installation.tool_transaction():
                self._verify_selection(pin, plugins, scope)
                if automatic:
                    self._verify_automatic_generation(current, generation, scope)
                result, candidate = self._install_scoped(
                    core,
                    pin,
                    plugins,
                    current=current,
                    latest=latest,
                    target=target,
                    channel=channel,
                    scope=scope,
                    explicit_source=not automatic,
                )
                if scope is not None and plan is not None and candidate is not None:
                    result = self._finish_mutation(scope, plan, candidate, result)
                return result

    def _install_scoped(  # noqa: PLR0913 - what is installed, from where, with which plugins, and what for
        self,
        core: str,
        pin: CorePin,
        plugins: list[CorePin],
        *,
        current: str,
        latest: str | None,
        target: str | None,
        channel: str | None = None,
        scope: DaemonMutationScope | None = None,
        explicit_source: bool = False,
    ) -> tuple[UpdateResult, str | None]:
        """Install `core` with every plugin in one run; a plugin that blocks it is kept or taken out, never blocking.

        Raises:
            HeinzelError: If hz can't be installed even without its plugins, or doesn't start after it.
        """
        self._start_step("install")
        before = self._plugin_versions()
        activity = f"Updating Heinzelmännchen{f' to {target}' if target else ''}"
        environment = git_credentials(subprocess_environment())
        original = self._original_generation()
        with self._install_constraints(
            core, current, self._constraint_candidates(core, plugins, before), enabled=not explicit_source, scope=scope
        ) as (constraints, captured_constraints):
            preferred = index_preferred_release(pin, current, latest, captured_constraints) if target is None else None
            modes, reasons, failure, minimum = self._converge(
                core,
                plugins,
                before,
                activity,
                environment=environment,
                may_drop=True,
                constraints=constraints,
                preferred=preferred,
            )
        if modes is None:
            constraint_hint = (
                " Inspect existing constraints; to change them intentionally, choose an explicit uv command."
                if explicit_source and constraints is not None
                else ""
            )
            if scope is not None:
                unchanged = self._abort_or_leave_pending(scope)
                detail = (
                    "The original installation remains unchanged."
                    if unchanged
                    else "The installation is uncertain; explicit recovery is required."
                )
                msg = f"Could not update hz {current}: {failure}\n{detail}{constraint_hint}"
                raise HeinzelError(msg)
            failure_message = self._failure(
                pin, plugins, current, target, failure, environment=environment, original=original
            )
            msg = f"{failure_message}{constraint_hint}"
            raise HeinzelError(msg)
        candidate = self._installation.startup_fingerprint() if scope is not None else None
        selected = self._selected_requirements(core, plugins, modes, before)
        metadata = self._installation.verify_installed_requirements(
            selected,
            expected_version=target.removeprefix("v")
            if target and re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.+-]*", target)
            else None,
            forbidden=tuple(plugin.name for plugin in plugins if modes[canonicalize_name(plugin.name)] == "drop"),
            constraints=captured_constraints,
        )
        verify_core_selection(metadata["heinzel"], current, minimum, explicit_source=explicit_source)
        try:
            after = self._version()
        except HeinzelError as exc:
            installed = f"uv installed {target}" if target else "uv upgraded hz"
            recovery = (
                "The installation is uncertain; explicit recovery is required."
                if scope is not None
                else self._still_working(pin, plugins, current, environment, original)
            )
            msg = f"{installed}, but hz doesn't start.\n{recovery}"
            raise HeinzelError(msg) from exc
        if Version(after) != Version(metadata["heinzel"]):
            msg = "The running hz version does not match its inspected metadata."
            raise HeinzelError(msg)
        now = self._installation.core_pin()
        message = install_message(
            current,
            after,
            latest,
            target=target,
            channel=channel,
            explicit_source=explicit_source,
            index_lookup_failed=pin.kind == "index" and channel is None and not explicit_source and latest is None,
        )
        after_plugins = self._plugin_versions(strict=True)
        self._verify_plugin_versions(metadata, after_plugins)
        updates = self._plugin_updates(plugins, modes, reasons, before, after_plugins, core)
        message = with_summary(message, updates)
        if candidate is not None and self._installation.startup_fingerprint() != candidate:
            msg = "The installation changed during post-update verification."
            raise HeinzelError(msg)
        return UpdateResult(
            before=current,
            after=after,
            latest=latest,
            pinned_to=now.ref if now else None,
            updated=after != current or target is not None or any(u.status == "upgraded" for u in updates),
            message=message,
            next=[],
            plugins=updates,
        ), candidate

    def _with_plugins(
        self, result: UpdateResult, pin: CorePin, plugins: list[CorePin], generation: str | None
    ) -> UpdateResult:
        """`result` of an update that leaves hz as it is, with the plugins asked again.

        The ones that follow a branch or an index may have a newer version. hz stays where it is, so a plugin that
        can't move is kept, never dropped.
        """
        # A core that follows a branch would move with the reinstall, which is `--source`'s job, not this one's.
        if pin.kind not in ("release", "commit", "local", "url") or not (
            AGREED.get().moves or any(p.kind in ("index", "default", "ref") for p in plugins)
        ):
            return result
        with self._mutation() as scope:
            plan = scope.capture() if scope is not None else None
            return self._refresh_plugins(result, pin, plugins, (scope, plan), generation)

    def _refresh_plugins(
        self,
        result: UpdateResult,
        pin: CorePin,
        plugins: list[CorePin],
        lifecycle: tuple[DaemonMutationScope | None, DaemonHandoverPlan | None],
        generation: str | None,
    ) -> UpdateResult:
        scope, plan = lifecycle
        self._start_step("install")
        environment = git_credentials(subprocess_environment())
        with self._installation.tool_transaction():
            self._verify_selection(pin, plugins, scope)
            self._verify_automatic_generation(result.before, generation, scope)
            before = self._plugin_versions()
            original = self._original_generation()
            candidates = self._constraint_candidates(pin.requirement, plugins, before)
            with self._install_constraints(pin.requirement, result.before, candidates, enabled=False, scope=scope) as (
                constraints,
                captured_constraints,
            ):
                modes, reasons, failure, _ = self._converge(
                    pin.requirement,
                    plugins,
                    before,
                    "Updating the plugins",
                    environment=environment,
                    may_drop=False,
                    constraints=constraints,
                )
            if modes is None:
                if scope is not None:
                    unchanged = self._abort_or_leave_pending(scope)
                    if not unchanged:
                        msg = "The plugin update was uncertain; explicit recovery is required."
                        raise HeinzelError(msg)
                    recovery = "The original installation remains unchanged."
                else:
                    recovery = self._still_working(pin, plugins, result.before, environment, original)
                names = blocking_plugins([p.requirement for p in plugins], failure) or [
                    canonicalize_name(p.name) for p in plugins
                ]
                reason = f"Not updated: {uv_reason(failure)}"
                updates = [
                    self._unchanged(p, before, reason if canonicalize_name(p.name) in names else None) for p in plugins
                ]
                return result.model_copy(
                    update={"plugins": updates, "message": f"{with_summary(result.message, updates)}\n{recovery}"}
                )
            candidate = self._installation.startup_fingerprint() if scope is not None else None
            selected = self._selected_requirements(pin.requirement, plugins, modes, before)
            metadata = self._installation.verify_installed_requirements(
                selected, expected_version=pin.version, constraints=captured_constraints
            )
            if Version(metadata["heinzel"]) < Version(result.before):
                msg = (
                    "The resolved automatic update is older than the installed core; "
                    "no changed code was started and explicit recovery is required."
                )
                raise HeinzelError(msg)
            if Version(self._version()) != Version(metadata["heinzel"]):
                msg = "The running hz version does not match its inspected metadata."
                raise HeinzelError(msg)
            after = self._plugin_versions(strict=True)
            self._verify_plugin_versions(metadata, after)
            updates = self._plugin_updates(plugins, modes, reasons, before, after, pin.requirement)
            refreshed = result.model_copy(
                update={
                    "plugins": updates,
                    "message": with_summary(result.message, updates),
                    "updated": result.updated or any(u.status == "upgraded" for u in updates),
                }
            )
            if candidate is not None:
                if self._installation.startup_fingerprint() != candidate:
                    msg = "The installation changed during post-update verification."
                    raise HeinzelError(msg)
                if scope is not None and plan is not None:
                    if not refreshed.updated and self._abort_or_leave_pending(scope):
                        return refreshed  # nothing changed, so the daemon is not handed over
                    refreshed = self._finish_mutation(scope, plan, candidate, refreshed)
            return refreshed

    def _selected_requirements(
        self, core: str, plugins: list[CorePin], modes: dict[str, Mode], before: dict[str, Installed]
    ) -> list[str]:
        return [
            self._installation.core_requirement(core),
            *(
                self._spec(
                    plugin, modes[canonicalize_name(plugin.name)], core, before.get(canonicalize_name(plugin.name))
                )
                for plugin in plugins
                if modes[canonicalize_name(plugin.name)] != "drop"
            ),
        ]

    @contextmanager
    def _install_constraints(
        self, core: str, current: str, candidates: list[str], *, enabled: bool, scope: DaemonMutationScope | None
    ) -> Generator[tuple[Path | None, tuple[str, ...]]]:
        """Preserve prior constraints and add an automatic index floor where needed.

        Yields:
            A private constraints file when needed and the exact captured policy.

        Raises:
            HeinzelError: If existing constraints or the selected source cannot be preserved safely.
            OSError: If the private constraints file cannot be written or removed.
        """
        requirement = Requirement(self._installation.core_requirement(core))
        pinned = any(
            item.operator in {"==", "==="} and not item.version.endswith(".*") for item in requirement.specifier
        )
        try:
            constraints = self._installation.tool_constraints()
            constraints = self._constraint_wheel_hashes(requirement, candidates, constraints)
            if enabled and requirement.url is None and not pinned:
                constraints.append(f"heinzel>={Version(current)}")
            captured = tuple(constraints)
            # Candidates include new and keep alternatives for each plugin, not one simultaneous selection.
            for candidate in candidates:
                self._installation.validate_install_constraints([candidate], captured)
            if not constraints:
                yield None, captured
                return
            with tempfile.TemporaryDirectory(prefix="heinzel-update-") as directory:
                path = Path(directory) / "constraints.txt"
                path.write_text("\n".join(constraints) + "\n", encoding="utf-8")
                yield path, captured
        except (HeinzelError, OSError):
            if scope is not None:
                self._abort_or_leave_pending(scope)
            raise

    def _constraint_wheel_hashes(self, core: Requirement, candidates: list[str], constraints: list[str]) -> list[str]:
        """Retain hash identities when uv's receipt-derived local constraints would erase them.

        Hashes strengthen only an existing source constraint. uv serializes these
        transient hash fragments back to the same original path entries, so no
        receipt constraint is added, replaced or silently removed.
        """
        parsed = [Requirement(item) for item in constraints]
        selected = {canonicalize_name(Requirement(item).name) for item in candidates if Requirement(item).url is None}
        local = {
            canonicalize_name(item.name)
            for item in parsed
            if item.url is not None and urlsplit(item.url).scheme == "file" and urlsplit(item.url).path.endswith(".whl")
        }
        preserved = self._installation.unchanged_wheel_sources(sorted(str(name) for name in selected & local), ())
        sources = {canonicalize_name(Requirement(item).name): Requirement(item) for item in preserved}
        source = urlsplit(core.url or "")
        if (
            source.scheme == "file"
            and not source.netloc
            and not source.query
            and re.fullmatch(r"sha256=[a-f0-9]{64}", source.fragment)
        ):
            sources[canonicalize_name(core.name)] = core
        result: list[str] = []
        for item in parsed:
            hashed = sources.get(canonicalize_name(item.name))
            if item.url is not None and hashed is not None:
                self._installation.validate_install_constraints([str(hashed)], (str(item),))
                item.url = hashed.url
            result.append(str(item))
        return result

    def _constraint_candidates(self, core: str, plugins: list[CorePin], before: dict[str, Installed]) -> list[str]:
        return [
            self._installation.core_requirement(core),
            *(
                self._spec(plugin, mode, core, before.get(canonicalize_name(plugin.name)))
                for plugin in plugins
                for mode in ("new", "keep")
            ),
        ]

    def _converge(  # noqa: PLR0913 - same credentials are used for the operation and recovery
        self,
        core: str,
        plugins: list[CorePin],
        before: dict[str, Installed],
        activity: str,
        *,
        environment: Mapping[str, str],
        may_drop: bool,
        constraints: Path | None = None,
        preferred: str | None = None,
    ) -> tuple[dict[str, Mode] | None, dict[str, str], str, str | None]:
        """Install `core` and every plugin; each plugin that blocks it is stepped down until the install works.

        A plugin goes in from its spec asked again (`new`); one uv names in a failed install is tried at the version it
        has (`keep`), and with `may_drop` left out (`drop`) if that doesn't resolve either. If uv names no one, every
        plugin is tried as it is. Returns the mode of each plugin, why those stepped down did, and the last failure
        (the modes are None if nothing worked), and the preferred minimum if the resolver used it.
        """
        names = [canonicalize_name(p.name) for p in plugins]
        modes: dict[str, Mode] = dict.fromkeys(names, "new")
        reasons: dict[str, str] = {}
        output = ""
        graph = self._requirers()
        for _ in range(2 * len(plugins) + 2 + int(preferred is not None)):
            specs = [
                self._spec(p, modes[n], core, before.get(n))
                for p, n in zip(plugins, names, strict=True)
                if modes[n] != "drop"
            ]
            command = self._converge_command(core, specs, constraints, preferred)
            try:
                result = self._installation.attempt(command, activity, environment)
            except HeinzelError as exc:
                return None, reasons, str(exc), None
            if result.returncode == 0:
                return modes, reasons, "", preferred
            output = result.stderr.strip() or result.stdout.strip()
            if not is_conflict(output):  # a network, credential or timeout failure is not the plugins' fault
                return None, reasons, output, None
            if preferred is not None:
                # A preference for the newest release must not force plugin removal.
                preferred = None
                continue
            blocking = [n for n in blocking_plugins(specs, output) if n in modes and modes[n] != "drop"]
            suspects = (
                blocking
                or [n for n in names if modes[n] == "new"]
                or ([] if not may_drop else [n for n in names if modes[n] == "keep"])
            )
            if not suspects:
                break
            for name in suspects:
                if modes[name] == "keep" and not may_drop:
                    return None, reasons, output, None
                modes[name] = "keep" if modes[name] == "new" else "drop"
                reasons[name] = uv_reason(output)
            _drop_dependents(modes, reasons, graph)
        return None, reasons, output, None

    def _converge_command(
        self, core: str, specs: list[str], constraints: Path | None, preferred: str | None
    ) -> list[str]:
        command = self._installation.tool_install_command(core, specs, refresh=True)
        if preferred is not None:
            command.extend(["--upgrade-package", f"heinzel>={preferred}"])
        if constraints is not None:
            command.extend(["--constraints", str(constraints)])
        return command

    def _requirers(self) -> dict[str, set[str]]:
        """Which installed distributions require each one, read from the tool folder's metadata; never run."""
        paths = [str(path) for path in self._installation.prefix.glob("lib/python*/site-packages")]
        return required_by(importlib.metadata.distributions(path=paths)) if paths else {}

    def _spec(self, plugin: CorePin, mode: Mode, _core: str, installed: Installed | None) -> str:
        """The requirement `plugin` goes in with: where a confirmed update moves its pin, else as the receipt has it."""
        if mode != "keep" and (moved := AGREED.get().moves.get(canonicalize_name(plugin.name))) is not None:
            return moved
        if mode == "keep" and plugin.kind == "index" and installed is not None:
            return f"{plugin.name}=={installed.version}"
        if mode == "keep":
            if installed is not None and installed.revision and plugin.git:
                fragment = f"#subdirectory={plugin.subdirectory}" if plugin.subdirectory else ""
                return f"{plugin.name} @ git+{plugin.git}@{installed.revision}{fragment}"
            return plugin.requirement
        return plugin.requirement

    def _plugin_updates(
        self,
        plugins: list[CorePin],
        modes: dict[str, Mode],
        reasons: dict[str, str],
        before: dict[str, Installed],
        after: dict[str, Installed],
        _core: str,
    ) -> list[PluginUpdate]:
        """Report each plugin: upgraded, unchanged, or failed with what became of it."""
        updates = list[PluginUpdate]()
        for plugin in plugins:
            name = canonicalize_name(plugin.name)
            old, new = before.get(name), after.get(name)
            old_version, new_version = (old.version if old else None), (new.version if new else None)
            match modes[name]:
                case "drop":
                    handle = plugin.requirement
                    add = f"hz system plugin add {shlex.quote(handle)} --confirm"
                    reason = (
                        f"{reasons[name]} It was taken out of the installation with it (disabled), and hz was updated "
                        f"without them. Once the plugin it needs is back: {add}"
                        if reasons[name].startswith(NEEDS)
                        else f"{reasons[name]} It does not allow the new hz, so it was taken out of the installation "
                        f"(disabled), and hz was updated without it. Once a release allows the new hz: {add}"
                    )
                    updates.append(
                        PluginUpdate(
                            distribution=name,
                            status="failed",
                            before=old_version,
                            after=None,
                            disabled=True,
                            reason=reason,
                            next=[add],
                        )
                    )
                case "keep":
                    reason = (
                        f"{reasons[name]} It stays at {old_version or 'its version'}, which still works with the new "
                        "hz; update it when its maintainers release a version that does."
                    )
                    updates.append(
                        PluginUpdate(
                            distribution=name, status="failed", before=old_version, after=new_version, reason=reason
                        )
                    )
                case _:
                    changed = (
                        old is not None
                        and new is not None
                        and (old.version, old.revision) != (new.version, new.revision)
                    )
                    updates.append(
                        PluginUpdate(
                            distribution=name,
                            status="upgraded" if changed else "unchanged",
                            before=old_version,
                            after=new_version,
                            pinned=None if name in AGREED.get().asked else pinned_ref(plugin),
                        )
                    )
        return updates

    @staticmethod
    def _unchanged(plugin: CorePin, before: Mapping[str, Installed], failure: str | None) -> PluginUpdate:
        name = canonicalize_name(plugin.name)
        version = before[name].version if name in before else None
        return PluginUpdate(
            distribution=name,
            status="failed" if failure else "unchanged",
            before=version,
            after=version,
            reason=failure,
            pinned=pinned_ref(plugin),
            checked=False,
        )

    def _plugin_versions(self, *, strict: bool = False) -> dict[str, Installed]:
        """Get plugin versions; ordinary pre-update reports can tolerate an unavailable runtime.

        Raises:
            HeinzelError: If strict post-update inspection cannot read the inventory.
        """
        try:
            items = json.loads(self._installation.hz("system", "plugin", "list", "--json"))
            return {
                canonicalize_name(str(i["distribution"])): Installed(str(i["version"]), i.get("revision"))
                for i in items
            }
        except (HeinzelError, ValueError, KeyError, TypeError) as exc:
            if strict:
                msg = "The updated plugin inventory could not be verified."
                raise HeinzelError(msg) from exc
            return {}

    @staticmethod
    def _verify_plugin_versions(metadata: dict[str, str], actual: dict[str, Installed]) -> None:
        for name, version in metadata.items():
            if name != "heinzel" and (name not in actual or Version(actual[name].version) != Version(version)):
                msg = "The updated plugin inventory does not match its inspected metadata."
                raise HeinzelError(msg)

    def _failure(  # noqa: PLR0913 - retain the exact environment for recovery
        self,
        pin: CorePin,
        plugins: list[CorePin],
        current: str,
        target: str | None,
        output: str,
        *,
        environment: Mapping[str, str],
        original: str | None,
    ) -> str:
        """What failed, which plugins are named in it, and that the previous hz still works or was put back."""
        to = f" to {target}" if target else ""
        blocking = blocking_plugins([p.requirement for p in plugins], output)
        named = (
            f" The plugin{'s' if len(blocking) > 1 else ''} {', '.join(blocking)} could not be kept either."
            if blocking
            else ""
        )
        headline = f"Could not update hz {current}{to}.{named}"
        recovery = self._still_working(pin, plugins, current, environment, original)
        return "\n".join(filter(None, [headline, recovery, output]))

    def _still_working(
        self, pin: CorePin, plugins: list[CorePin], current: str, environment: Mapping[str, str], original: str | None
    ) -> str:
        """Probe only the captured generation; otherwise try restoring the old receipt without executing it.

        uv replaces a tool environment only once the new one resolved and installed, so a failed `uv tool install`
        usually leaves the previous one in place. A restore is not proof that the old bytes were restored.
        """
        if self._same_generation(original):
            try:
                return f"hz {self._version()} is still installed, with every plugin."
            except HeinzelError:
                pass
        restore = self._installation.tool_install_command(
            pin.requirement, [p.requirement for p in plugins], original=True
        )
        try:
            restored = self._installation.attempt(restore, f"Restoring hz {current}", environment, recovery=True)
            if restored.returncode == 0 and self._same_generation(original):
                return f"hz {self._version()} was restored, with every plugin."
        except HeinzelError:
            pass
        return f"hz may not start; restore it with: {self._installation.tool_restore_hint(restore)}"


NEEDS = "It needs "
"""How the reason of a plugin taken out with the plugin it needs begins (`_drop_dependents`)."""


def _drop_dependents(modes: dict[str, Mode], reasons: dict[str, str], graph: dict[str, set[str]]) -> None:
    """Take out with each dropped plugin every plugin that needs it: uv would put it back as their dependency.

    Leaving a dependent in would install the dropped plugin again, and the update would then refuse its own result.
    """
    for name in [name for name, mode in modes.items() if mode == "drop"]:
        for dependent in sorted(dependents(name, graph) & modes.keys()):
            if modes[dependent] != "drop":
                modes[dependent] = "drop"
                reasons[dependent] = f"{NEEDS}{name}, which was taken out: {reasons[name]}"
