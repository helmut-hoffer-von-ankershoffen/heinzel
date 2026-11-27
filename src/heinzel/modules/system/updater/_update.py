"""`hz system update`: move the installation to the latest release or the newest commit, or say how to move it.

uv keeps a git requirement at the ref it was installed from: `uv tool upgrade` leaves `heinzel @ git+…@v0.2.0` at
v0.2.0 for good. So an installation pinned to a release tag is reinstalled from the tag of the highest release, with
every plugin as the receipt has it (ADR 0006). A branch, a commit or a local source could mean anything, so nothing is
guessed there: the result says what it is pinned to and which command moves it. An installation that follows a git
repository's default branch or the package index is upgraded by uv, which moves both; one pinned to `main`, the
default branch, is reinstalled from the same spec, which fetches its newest commit. No result suggests a release that
is older than the installed version.

Plugins are updated in the same `uv tool install` run as hz, so one resolver step keeps their dependencies consistent
with the new core. Each plugin is installed from the spec the receipt recorded (a PyPI requirement, or a git spec with
its `#subdirectory=`); a plugin from the core's own repository follows the core's ref, and a git branch or an index is
asked again, so `@main` moves to the newest commit. A plugin that blocks the install never blocks hz: it is kept at the
version it has if that still resolves, and otherwise taken out of the installation with the command that brings it back.
"""

from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Literal

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from heinzel import HeinzelError, __version__
from heinzel.kernel import (
    CorePin,
    DaemonHandoverPlan,
    DaemonMutationScope,
    HeadRefresh,
    ToolConstraintPolicy,
)
from heinzel.kernel._desktop_app import label
from heinzel.kernel._logging import step
from heinzel.modules.system._models import AnnouncedUpdate, UpdatePlan, UpdateResult
from heinzel.modules.system._service import RELEASE_TIMEOUT
from heinzel.modules.system.updater._announced import (
    Announcement,
    ToolSnapshot,
    allows_core,
    environment_versions,
    installed_commit,
    installed_identity,
    plugin_commit,
)
from heinzel.modules.system.updater._plugin_releases import PinnedPlugins
from heinzel.modules.system.updater._update_channels import UpdateChannels
from heinzel.modules.system.updater._update_heads import recorded_flavor
from heinzel.modules.system.updater._update_rules import (
    AGREED,
    Agreed,
    Before,
    Selection,
    UpdateChannel,
    added_options,
    core_line,
    log,
    plan_token,
    plugin_plan,
    refused_by_hand,
    require_untouched,
    with_heads,
)
from heinzel.modules.system.updater._update_steps import Steps, own_rows


class UpdateService(UpdateChannels):
    """Updates the uv tool environment Heinzelmännchen runs in, keeping its plugins."""

    def apply_announced(
        self,
        announced: Announcement,
        *,
        current: str = __version__,
        folder: Path | None = None,
        trial: Callable[[str], bool],
    ) -> AnnouncedUpdate:
        """Install exactly the announced versions, prove the new hz starts, or put everything back (ADR 0116).

        Every distribution in the tool folder is held at the version it has, by exact transient constraints, except
        hz at the announced version (or the running one) and each announced plugin at its own: uv installs exactly
        these or nothing. Only the announced packages are asked again (`--refresh-package`); a dependency the new
        versions need that isn't installed yet resolves only among uploads as old as the minimum ages
        (`--exclude-newer`). hz from a release tag is installed from the commit the tag named when it was announced,
        and the receipt names the tag again afterwards; so is a plugin pinned to a GitHub commit whose release tag was
        announced, and its receipt names that commit (ADR 0137). Other plugins from git, a folder or a URL can't be
        held this way, so their installation is updated by hand. Nothing installed runs to verify it: its metadata
        must name exactly these versions and commits. Then `trial` starts the new hz on throwaway folders. Any
        failure after the copy puts the tool folder back from it (`ToolSnapshot`), so its continuity record matches
        again and the daemon mutation completes with no handover pending.

        `folder` is where the copy is taken; without it, the state folder of the installation (`state`).

        Raises:
            HeinzelError: If the installation can't be updated this way, before anything was installed, or if there
                is no folder to copy into.
            OSError: If the copy can't be taken; nothing was installed.

        It holds the installation's update lock (`_across_processes`), so it never runs beside a `hz system update`.
        """
        with self._across_processes("automatic"):
            return self._apply_announced(announced, current=current, folder=folder, trial=trial)

    def _apply_announced(
        self, announced: Announcement, *, current: str, folder: Path | None, trial: Callable[[str], bool]
    ) -> AnnouncedUpdate:
        """`apply_announced`, under the update lock.

        Raises:
            HeinzelError: As `apply_announced` says.
            OSError: If the copy can't be taken; nothing was installed.
        """
        folder = folder or self._state_folder()
        target = announced.core or current
        selection = self._announced_selection(target, announced)
        if isinstance(selection, AnnouncedUpdate):
            return selection
        pin, core_requirement, plugin_pins, names = selection.pin, selection.core, selection.plugins, selection.names
        prefix = self._installation.prefix.resolve(strict=True)
        with self._interpreter_for(announced.core), self._mutation() as scope:
            plan = scope.capture() if scope is not None else None
            with self._installation.tool_transaction():
                expected, guards, policy = self._prepare_announced(
                    (pin, plugin_pins, names), announced.plugins, current, target, scope
                )
                specs = selection.specs
                original_receipt = self._installation.receipt()
                untouched = {name: installed_identity(prefix, name) for name in selection.by_hand}
                moving = (["heinzel"] if announced.core else []) + list(announced.plugins)
                try:
                    # A standard-app wheel the receipt names bare stays the verified wheel it is (ADR 0116).
                    sources = self._installation.unchanged_wheel_sources([core_requirement, *specs], moving)
                    transient = (*guards, *sources)
                    constraints = (*self._installation.tool_constraints(), *transient)
                    self._installation.validate_install_constraints([core_requirement, *specs], constraints)
                    snapshot = ToolSnapshot.take(self._installation.prefix.resolve(strict=True), folder)
                except (HeinzelError, OSError):
                    if scope is not None:
                        self._abort_or_leave_pending(scope)
                    raise
                before = self._fingerprints()
                try:
                    generation = self._install_announced(
                        [core_requirement, *specs], (guards, sources, constraints), expected, announced
                    )
                    require_untouched(prefix, untouched)
                    generation = self._installation.remove_transient_tool_constraint(
                        expected=generation, original=policy, added=transient, options=added_options(announced)
                    )
                    if pin.kind == "release":
                        generation = self._installation.name_core_by_tag(
                            expected=generation, commit=core_requirement.rsplit("@", 1)[1], tag=f"v{target}"
                        )
                    if selection.held_at_commit:  # they follow their branch or tag again, as before
                        generation = self._installation.restore_receipt_requirements(
                            expected=generation, original=original_receipt, names=selection.held_at_commit
                        )
                except (HeinzelError, OSError) as exc:
                    return self._put_back(snapshot, (scope, plan), before, current, f"{exc}", refused=False)
                if not trial(target):
                    return self._put_back(snapshot, (scope, plan), before, current, None, refused=target)
                snapshot.discard()
                result = UpdateResult(
                    before=current,
                    after=target,
                    latest=announced.core,
                    pinned_to=None,
                    updated=True,
                    message="",
                    next=[],
                )
                if scope is not None and plan is not None:
                    self._finish_mutation(scope, plan, generation, result)
        heads = self._refresh_heads(target)
        return AnnouncedUpdate(
            updated=True,
            after=target,
            restored=False,
            refused=False,
            message=with_heads(f"Installed hz {target}.", heads),
            heads=heads,
        )

    def _announced_selection(self, target: str, announced: Announcement) -> "Selection | AnnouncedUpdate":
        """What goes into uv, before anything changes: the exact core, and every plugin as it must stay or move.

        Plugins from git, a folder or a URL are updated by hand: they go in exactly as installed (one from git at its
        installed commit), are never refreshed or reinstalled, and must be unchanged afterwards. An answer instead
        when hz from a release tag has no commit to install, or a plugin updated by hand doesn't allow the new hz.

        Raises:
            HeinzelError: If the installation isn't a uv tool from a release tag or the package index, a plugin from
                git has no commit to hold it to, or an announced plugin isn't an installed one from PyPI.
        """
        pin = self._installation.core_pin()
        if pin is None or not self._installation.requirements():
            msg = "Heinzelmännchen wasn't installed with `uv tool install`; it can't update itself."
            raise HeinzelError(msg)
        prefix = self._installation.prefix.resolve(strict=True)
        if pin.kind == "release" and pin.git:
            commit = announced.commit if announced.core is not None else installed_commit(prefix)
            if commit is None:
                return refused_by_hand(target, f"The release tag of hz {target} names no commit to hold it to")
            core_requirement = pin.at(commit)
        elif pin.kind in ("index", "default") and pin.git is None:
            core_requirement = pin.requirement
        else:
            msg = "Only installations from release tags or the package index update themselves."
            raise HeinzelError(msg)
        plugin_pins = self._installation.plugin_pins()
        plugins = self._announced_plugins(prefix, plugin_pins, target, announced)
        if isinstance(plugins, AnnouncedUpdate):
            return plugins
        names, specs, by_hand, held_at_commit = plugins
        return Selection(pin, core_requirement, plugin_pins, names, specs, by_hand, held_at_commit)

    @staticmethod
    def _announced_plugins(
        prefix: Path, plugin_pins: list[CorePin], target: str, announced: Announcement
    ) -> tuple[list[str], list[str], list[str], list[str]] | AnnouncedUpdate:
        """Each plugin's name and requirement for uv, those updated by hand, and those held at their git commit.

        An announced plugin pinned to a commit of a GitHub repository goes in at the commit its release tag named
        when it was announced (ADR 0137); the receipt names that commit afterwards, so it stays pinned.

        Raises:
            HeinzelError: If a plugin from git has no commit to hold it to, or an announced plugin is neither an
                installed one from PyPI nor one pinned to a git commit with a recorded tag commit.
        """
        names: list[str] = [str(canonicalize_name(Requirement(plugin.requirement).name)) for plugin in plugin_pins]
        indexed = {name for name, plugin in zip(names, plugin_pins, strict=True) if plugin.kind == "index"}
        tagged = {
            name
            for name, plugin in zip(names, plugin_pins, strict=True)
            if plugin.kind == "commit" and plugin.git is not None and name in announced.plugin_commits
        }
        if missing := sorted((set(announced.plugins) - indexed - tagged) | (set(announced.plugin_commits) - tagged)):
            msg = (
                "Announced plugins aren't installed from PyPI or pinned to a tagged GitHub commit: "
                f"{', '.join(missing)}."
            )
            raise HeinzelError(msg)
        specs: list[str] = []
        held_at_commit: list[str] = []
        for name, plugin in zip(names, plugin_pins, strict=True):
            if plugin.kind == "index":
                specs.append(plugin.requirement)
                continue
            if name in tagged and name in announced.plugins:
                specs.append(plugin.at(announced.plugin_commits[name]))
                continue
            if not allows_core(prefix, name, target):
                return refused_by_hand(
                    target, f"{name}, a plugin updated by hand, doesn't allow hz {target}. Update it first"
                )
            if plugin.git is None:
                specs.append(plugin.requirement)
                continue
            commit = plugin_commit(prefix, name)
            if commit is None:
                msg = f"{name} came from git but doesn't say from which commit; update it by hand."
                raise HeinzelError(msg)
            specs.append(plugin.at(commit))
            held_at_commit.append(name)
        return names, specs, sorted(set(names) - indexed - (tagged & set(announced.plugins))), held_at_commit

    def _prepare_announced(
        self,
        selection: tuple[CorePin, list[CorePin], list[str]],
        plugins: Mapping[str, str],
        current: str,
        target: str,
        scope: DaemonMutationScope | None,
    ) -> tuple[dict[str, str], tuple[str, ...], ToolConstraintPolicy]:
        """What to hold, as exact constraints, and the receipt's own policy to keep; read from disk, never run.

        Every distribution in the tool folder is held at its version, apart from hz and the announced plugins.

        Raises:
            HeinzelError: If the selection, the running version or the installed versions can't be verified.
        """
        pin, plugin_pins, names = selection
        self._verify_selection(pin, plugin_pins, scope)
        try:
            self._require_running_version(current)
            installed = self._installation.installed_versions(names)
            expected = {"heinzel": target}
            expected.update({name: installed[name] for name in names if name in installed})
            expected.update(plugins)
            held = environment_versions(self._installation.prefix.resolve(strict=True))
            held.update(expected)
            policy = self._installation.capture_tool_constraint_policy()
        except HeinzelError:
            if scope is not None:
                self._abort_or_leave_pending(scope)
            raise
        return expected, tuple(f"{name}=={version}" for name, version in held.items()), policy

    def _put_back(  # noqa: PLR0913 - the copy, the mutation, what it was and why
        self,
        snapshot: ToolSnapshot,
        lifecycle: tuple[DaemonMutationScope | None, DaemonHandoverPlan | None],
        before: "Before",
        current: str,
        why: str | None,
        *,
        refused: str | Literal[False],
    ) -> AnnouncedUpdate:
        """Put the tool folder back from `snapshot`; its continuity matches again and the mutation completes.

        Raises:
            HeinzelError: If it can't be put back, or isn't as it was afterwards; explicit recovery is required.
        """
        scope, plan = lifecycle
        try:
            snapshot.restore()
        except OSError as exc:
            msg = (
                f"The update didn't work and hz {current} couldn't be put back ({exc}). It keeps running until you "
                "restart it; reinstall it before that: uv tool install --reinstall heinzel"
            )
            raise HeinzelError(msg) from exc
        generation = self._installation.startup_fingerprint()
        if generation != before.generation:
            if before.content is None or self._installation.content_fingerprint() != before.content:
                msg = "The installation isn't as it was after putting it back; explicit recovery is required."
                raise HeinzelError(msg)
            # The same files, in the folder uv made anew for the other interpreter: a record that held before
            # holds again, so it is renewed for this folder.
            if before.matched:
                self._installation.record_startup_state(expected=generation)
        # A change that failed half way left the marker; the folder matches its record again. If it can't go, it stays
        # and says so: the next update or `hz ui restart` tries again.
        with suppress(OSError, HeinzelError):
            self._installation.clear_stale_startup_pending()
        if scope is not None and plan is not None:
            # The folder is as it was, byte for byte, but not inode for inode: the mutation completes with the
            # original generation, its record stays valid, and the daemon is handed over to the same code.
            if scope.checkpoint_matched:
                self._installation.record_startup_state(expected=generation)
            scope.verified_complete(generation)
            scope.request(plan)
        if refused:
            message = (
                f"hz {refused} was installed but didn't start, so hz {current} and its plugins were put back as they "
                "were and keep running. That update won't be tried again; a newer one will."
            )
        else:
            message = (
                f"The automatic update didn't install exactly the announced versions ({why}), so hz {current} and its "
                "plugins were put back as they were and keep running. It will try again in 6 hours."
            )
        return AnnouncedUpdate(updated=False, after=current, restored=True, refused=bool(refused), message=message)

    def update(  # noqa: PLR0913 - what a person can ask for, each a keyword
        self,
        source: str | None = None,
        current: str = __version__,
        channel: UpdateChannel = "stable",
        *,
        agreed: UpdatePlan | None = None,
        shown: UpdatePlan | None = None,
        desktop: bool = False,
        restart: bool = False,
        from_app: bool = False,
        daemon: bool = True,
    ) -> UpdateResult:
        """Update to the latest release, or reinstall from `source`; `current` is the version running now.

        After a change, every installed long-running head is moved onto the installed code through its own owned
        restart path and asked which version its live process loaded (`heads`): the daemon's handover tick and the
        persistent web server. The update is complete only when each is verified; the message says which aren't.
        A daemon that runs older code than is installed is restarted too, even when nothing changed. With `daemon`
        false none of them is restarted: the result says which one still runs older code.

        `channel` is where the installation follows: `edge` (the newest edge release's wheel, verified by digest) or
        stable, which `release` also names: the releases (a pin to the newest release tag, which every later
        update follows; an installation on `main` moves there once a release is as new as it, never as a downgrade) or
        `main` (its newest commit).

        With `agreed` (the plan a person saw and agreed to) each plugin goes in as that plan says (`moves_to`): one
        pinned to an exact index version follows the index from that version up, and one pinned to a commit of a
        GitHub repository that tags its releases goes to the commit of its newest release tag, as automatic updates
        install it (ADR 0137). Nothing is asked again for them, and never with `source`, which installs exactly
        what it names. The agreement belongs to this call alone. `shown` is the plan the progress rows are announced
        from. With `desktop` an installed desktop app is brought along by the new hz's own `hz app install`; a
        failure there never undoes the update. With `restart` an open app is quit and reopened by it, unless `from_app`
        says the app itself runs this update: then it installs when the app is quit.

        Raises:
            HeinzelError: If Heinzelmännchen wasn't installed with `uv tool install`, another update runs in this
                process, the latest release can't be asked for, or the install fails, naming the plugins that
                block it; Heinzelmännchen still works.
        """
        if not self._updating.acquire(blocking=False):
            msg = "An update is already running here; wait for it to finish, then look at the result."
            raise HeinzelError(msg)
        moves = Agreed.of(agreed if source is None else None)
        held = AGREED.set(moves)
        self._restart_daemon = daemon
        try:
            with self._across_processes("manual"):
                return self._update_with_steps(
                    source, current, channel, shown=shown, desktop=(desktop, restart, from_app), daemon=daemon
                )
        finally:
            self._restart_daemon = True
            AGREED.reset(held)
            self._updating.release()

    def _update_with_steps(  # noqa: PLR0913 - the answers of one call
        self,
        source: str | None,
        current: str,
        channel: UpdateChannel,
        *,
        shown: UpdatePlan | None,
        desktop: tuple[bool, bool, bool],
        daemon: bool,
    ) -> UpdateResult:
        started = self._clock()
        self._handover = None
        pins = self._installation.plugin_pins()
        names = [canonicalize_name(p.name) for p in pins]
        app = self._desktop_state()
        ran = self._daemon_version()
        steps = self._steps = Steps(
            self._progress,
            own_rows(names, shown),
            len(names),
            desktop=None if app is None else label(app.variant),
            daemon=ran is not None,
        )
        try:
            steps.start("check")
            try:
                result = self._update(source, current, channel)
            except HeinzelError as exc:
                steps.fail(str(exc).splitlines()[0])
                raise
            steps.installed(result)
            heads: list[HeadRefresh] = []
            steps.start("daemon")
            heads += self._service_heads(result, ran, daemon=daemon)
            steps.services(heads)
            if app is not None:
                steps.start("desktop")
                head = self._app_head(result, app, desktop)
                heads.append(head)
                steps.desktop(head)
            seconds = round(self._clock() - started, 1)
            message = with_heads(result.message, heads) if result.updated else result.message
            return result.model_copy(update={"heads": heads, "message": message, "seconds": seconds})
        finally:
            self._steps = None

    def plan(self, current: str = __version__, channel: UpdateChannel = "stable") -> UpdatePlan:
        """What a confirmed update would change: hz itself, each plugin, and whether a desktop app comes along.

        Only asks (the release source, PyPI, GitHub tags); nothing is installed. What can't be asked says so, and
        moves nothing. Its `token` names exactly this plan, so an update can tell whether it still holds.
        """
        pins = self._installation.plugin_pins()
        with step(log, "update.plan", operation="system.update") as outcome:
            latest = self._asked_latest(current)
            found, newer = self._asked_plugins(pins)
            try:
                installed = self._installation.installed_versions([canonicalize_name(p.name) for p in pins])
            except HeinzelError:
                installed = {}
            plans = [plugin_plan(pin, installed, found, newer) for pin in pins]
            outcome.note(
                moved=sorted(p.distribution for p in plans if p.moves_to),
                ahead=sorted(found.ahead),
                unasked=sorted(found.unasked),
            )
            if latest is None or found.unasked:
                outcome.skip("a release source couldn't be asked")
        return UpdatePlan(
            current=current,
            latest=latest,
            core=core_line(current, latest, channel),
            plugins=plans,
            desktop=self._desktop_installed(),
            desktop_running=self._desktop_installed() and self._running(),
            desktop_variant=self._variant(self._found(), recorded_flavor(self._receipt())),
            daemon=self._daemon_version() is not None,
            token=plan_token(latest, plans),
        )

    def _asked_plugins(self, pins: Sequence[CorePin]) -> tuple[PinnedPlugins, Mapping[str, str]]:
        """The releases of pinned plugins and of those that follow the index; a source that fails moves nothing."""
        try:
            found = self._pinned_plugins(RELEASE_TIMEOUT) if self._pinned_plugins is not None else PinnedPlugins()
        # Isolation boundary: whatever PyPI or GitHub raises, every plugin stays and the plan says why.
        except Exception as exc:  # noqa: BLE001
            reason = f"its releases couldn't be asked ({exc})"
            found = PinnedPlugins(unasked={canonicalize_name(p.name): reason for p in pins})
        try:
            newer = self._newer_plugins(RELEASE_TIMEOUT) if self._newer_plugins is not None else {}
        # Isolation boundary: the plan then names no target for the followers; the update asks again anyway.
        except Exception:  # noqa: BLE001
            newer = {}
        return found, newer
