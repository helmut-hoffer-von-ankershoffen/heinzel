"""Installing exactly the announced versions, the trial start and putting everything back (ADR 0116).

Nothing here runs uv or an installed hz: the uv run is a function that changes the metadata in a temporary tool
folder, and continuity is the real fingerprint of that folder (ADR 0108).
"""

import errno
import fcntl
import json
import os
import signal
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from heinzel import HeinzelError, Progress, Settings
from heinzel.heads.web._auto_update import AutoUpdater
from heinzel.kernel import DaemonHandoverPlan, DaemonHandoverRequest, Installation
from heinzel.kernel.install._installation import _core_by_tag
from heinzel.kernel.install._installation_mutations import installation_mutation_pending
from heinzel.modules.system import recover_announced
from heinzel.modules.system.daemon._abandoned_mutation import (
    Evidence,
    marker_token_matches,
    mutation_record,
    recover_abandoned_mutation,
)
from heinzel.modules.system.daemon._mutation_scope import MutationAuthority, MutationScope
from heinzel.modules.system.daemon._runtime import write_private
from heinzel.modules.system.updater import _announced as announced
from heinzel.modules.system.updater._adapter_state import FileInstallationState
from heinzel.modules.system.updater._announced import Announcement, ToolSnapshot, environment_versions
from heinzel.modules.system.updater._update import UpdateService

pytestmark = pytest.mark.unit
SITE = Path("lib", "python3.13", "site-packages")
REPO = "https://github.com/org/heinzel"
TAG_COMMIT = "c" * 40


# The tool folder ------------------------------------------------------------------------------------------------------


CRM_COMMIT = "a" * 40
CRM = {
    "METADATA": "Metadata-Version: 2.1\nName: heinzel-crm\nVersion: 0.3.0\nRequires-Dist: heinzel>=1\n",
    "RECORD": "heinzel_crm/__init__.py,sha256=abc,10\n",
    "direct_url.json": json.dumps(
        {"url": "https://example.test/crm", "vcs_info": {"vcs": "git", "commit_id": CRM_COMMIT}}
    ),
}
"""A plugin from git as uv installs it: its metadata, the hashes of its files and the commit it came from."""


def install(prefix: Path, versions: dict[str, str], files: dict[str, dict[str, str]] | None = None) -> None:
    """What uv leaves: a dist-info folder per distribution (with `files` for some), and hz's code with its version."""
    site = prefix / SITE
    for info in site.glob("*.dist-info"):
        for file in info.iterdir():
            file.unlink()
        info.rmdir()
    for name, version in versions.items():
        info = site / f"{name.replace('-', '_')}-{version}.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        for file, text in (files or {}).get(name, {}).items():
            (info / file).write_text(text)
    (site / "heinzel").mkdir(exist_ok=True)
    (site / "heinzel" / "__init__.py").write_text(f"hz {versions['heinzel']}")


def tool_folder(tmp_path, plugin="", core='{ name = "heinzel" }'):
    """A uv tool folder with hz 1.0.0, a plugin and a dependency, its receipt and entry point."""
    prefix = tmp_path / "tools" / "heinzel"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "hz").write_text("entrypoint")
    bin_dir = tmp_path / "installed-bin"
    bin_dir.mkdir()
    (bin_dir / "hz").symlink_to(prefix / "bin" / "hz")
    (prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{core}, {{ name = "heinzel-clock" }}{", " + plugin if plugin else ""}]'
        '\nentrypoints = [{ name = "hz", '
        "install-path = " + repr(str(bin_dir / "hz")) + " }]\n"
    )
    install(prefix, {"heinzel": "1.0.0", "heinzel-clock": "1.0.0", "urllib3": "2.0.0"})
    return prefix


def contents(folder: Path) -> dict[str, str]:
    return {
        str(path.relative_to(folder)): path.read_text() if path.is_file() and not path.is_symlink() else ""
        for path in sorted(folder.rglob("*"))
        if not path.name.startswith(".heinzel-startup")
    }


def test_putting_back_keeps_the_tool_folder_and_its_content_byte_for_byte(tmp_path):
    prefix = tool_folder(tmp_path)
    before, inode = contents(prefix), prefix.stat().st_ino
    snapshot = ToolSnapshot.take(prefix, tmp_path / "state")
    install(prefix, {"heinzel": "9.9.9", "heinzel-clock": "1.0.0", "urllib3": "2.8.0"})  # the update
    snapshot.restore()
    assert contents(prefix) == before
    assert prefix.stat().st_ino == inode  # the folder itself was never replaced
    assert sorted(p.name for p in (tmp_path / "state").iterdir()) == []


@pytest.mark.parametrize("stop_after", [1, 3])
def test_a_put_back_that_stopped_half_way_is_finished_by_recover(tmp_path, monkeypatch, stop_after):
    prefix = tool_folder(tmp_path)
    before = contents(prefix)
    snapshot = ToolSnapshot.take(prefix, tmp_path / "state")
    install(prefix, {"heinzel": "9.9.9", "heinzel-clock": "1.0.0", "urllib3": "2.0.0"})
    renamed, real = [], Path.rename

    def rename(self, target):
        if len(renamed) == stop_after:
            raise OSError(errno.EIO, "power cut")
        renamed.append(self)
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError, match="power cut"):
        snapshot.restore()
    monkeypatch.setattr(Path, "rename", real)
    snapshot.recover()  # as the next start does
    assert contents(prefix) == before


@pytest.mark.parametrize("killed", ["right after the copy", "right after the install"])
def test_a_process_killed_during_its_update_is_put_back_by_the_next_start(tmp_path, killed):
    prefix = tool_folder(tmp_path)
    tool = Installation(Progress(), prefix=prefix, uv="unused")
    tool.record_startup_state(expected=tool.startup_fingerprint())
    before = contents(prefix)
    ToolSnapshot.take(prefix, tmp_path / "state")
    if killed == "right after the install":
        install(prefix, {"heinzel": "9.9.9", "heinzel-clock": "1.0.0", "urllib3": "2.0.0"})
    # The process is gone. The next `hz ui` finds the copy and nothing else (system.recover-announced):
    ToolSnapshot(prefix, tmp_path / "state" / "before-update", tmp_path / "state" / "after-update").recover()
    assert contents(prefix) == before
    assert tool.startup_state_matches()
    assert list((tmp_path / "state").iterdir()) == []


def test_a_copy_cut_short_is_never_put_back(tmp_path, monkeypatch):
    prefix = tool_folder(tmp_path)
    real = announced.shutil.copytree

    def cut(source, target, **kwargs):
        raise OSError(errno.ENOSPC, "no space left")

    monkeypatch.setattr(announced.shutil, "copytree", cut)
    with pytest.raises(OSError, match="no space"):
        ToolSnapshot.take(prefix, tmp_path / "state")
    monkeypatch.setattr(announced.shutil, "copytree", real)
    before = contents(prefix)
    ToolSnapshot(prefix, tmp_path / "state" / "before-update", tmp_path / "state" / "after-update").recover()
    assert contents(prefix) == before  # only a complete copy is the truth


def test_the_copy_must_be_on_the_tool_folders_disk(tmp_path, monkeypatch):
    prefix = tool_folder(tmp_path)
    real = Path.stat
    monkeypatch.setattr(
        Path,
        "stat",
        lambda self, **kw: (
            os.stat_result((*real(self, **kw)[:2], 999, *real(self, **kw)[3:]))
            if self.name == "state"
            else real(self, **kw)
        ),
    )
    with pytest.raises(HeinzelError, match="another disk"):
        ToolSnapshot.take(prefix, tmp_path / "state")


# Installing exactly the announced versions ----------------------------------------------------------------------------


class Lifecycle:
    """A daemon mutation as `system.daemon` runs it: complete only once verified and handed over."""

    def __init__(self, checkpoint_matched=True):
        self.checkpoint_matched, self.pending, self.verified, self.requested = checkpoint_matched, False, None, 0

    def mutation(self, checkpoint, rollback_identity):
        @contextmanager
        def scope():
            self.pending = True
            yield self
            assert self.verified is not None
            assert self.requested == 1
            self.pending = False

        return scope()

    def capture(self):
        return DaemonHandoverPlan(active=True, platform="darwin", unit_signature="unit")

    def verified_complete(self, expected):
        self.verified = expected

    def abort_unchanged(self):
        self.pending = False

    def request(self, plan):
        self.requested += 1
        return DaemonHandoverRequest(requested=True, reason="fake manager")


class Releases:
    def latest_version(self, timeout):
        return "9.9.9"


CUTOFF = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
CORE_CUTOFF = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)


def service(tmp_path, monkeypatch, *, moves=None, crm=None, **folder):
    """An update service on the tool folder; its uv obeys the exact constraints, then moves what `moves` names.

    With `crm` (its dist-info files), the plugin heinzel-crm from git is installed too, and uv leaves it as it was
    unless `moves` names it.
    """
    prefix = tool_folder(tmp_path, **folder)
    files = {"heinzel-crm": crm} if crm else {}
    if crm:
        install(prefix, {**environment_versions(prefix), "heinzel-crm": "0.3.0"}, files)
    tool = Installation(Progress(), prefix=prefix, uv="unused")
    tool.record_startup_state(expected=tool.startup_fingerprint())
    runs, retagged = [], []

    def run(command, failure, activity, environment=None, *, additional_constraints=()):
        runs.append((command, additional_constraints))
        held = dict(c.split("==") for c in additional_constraints)
        changed = (
            {"heinzel-crm": {**CRM, "RECORD": "heinzel_crm/__init__.py,sha256=new,12\n"}}
            if moves and "heinzel-crm" in moves
            else files
        )
        install(prefix, {**held, **{k: v for k, v in (moves or {}).items() if k != "heinzel-crm"}}, changed)

    def never(*args, **kwargs):
        pytest.fail("an installed hz ran")

    monkeypatch.setattr(tool, "tool_transaction", nullcontext)  # its interpreter checks have their own tests
    monkeypatch.setattr(tool, "run", run)
    monkeypatch.setattr(tool, "hz", never)
    monkeypatch.setattr(tool, "installed_core_version", lambda: "1.0.0")
    monkeypatch.setattr(tool, "installed_versions", lambda names: environment_versions(prefix))
    monkeypatch.setattr(
        tool,
        "tool_install_command",
        lambda core, plugins, refresh=False, reinstall=True: [
            "uv",
            "tool",
            "install",
            *(["--reinstall"] if reinstall else []),
            core,
            *plugins,
        ],
    )
    monkeypatch.setattr(tool, "validate_install_constraints", lambda selected, constraints: None)
    monkeypatch.setattr(tool, "verify_installed_requirements", lambda *a, **k: {})
    monkeypatch.setattr(
        tool, "remove_transient_tool_constraint", lambda *, expected, original, added, options: expected
    )
    monkeypatch.setattr(
        tool, "name_core_by_tag", lambda *, expected, commit, tag: retagged.append((commit, tag)) or expected
    )
    monkeypatch.setattr(
        tool,
        "restore_receipt_requirements",
        lambda *, expected, original, names: retagged.append(("receipt", tuple(names))) or expected,
    )
    monkeypatch.setattr(announced, "installed_commit", lambda prefix: TAG_COMMIT)
    monkeypatch.setattr("heinzel.modules.system.updater._update.installed_commit", lambda prefix: TAG_COMMIT)
    monkeypatch.setattr("heinzel.modules.system.updater._update_install.installed_commit", lambda prefix: TAG_COMMIT)
    lifecycle = Lifecycle()
    update = UpdateService(tool, Releases(), lifecycle=lifecycle)
    update.retagged = retagged
    return update, tool, lifecycle, runs


def news(core="9.9.9", plugins=None, commit=None):
    return Announcement(core, plugins or {}, commit, exclude_newer=CUTOFF, exclude_newer_core=CORE_CUTOFF)


class UpdateInterrupted(BaseException):
    """Stop before the update process handles a rollback."""


def replace_tool_prefix(tool):
    """Model uv replacing the tool directory when switching interpreters."""
    prefix = tool.prefix
    original = prefix.rename(prefix.with_name("original"))
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/hz").write_text("new entrypoint")
    (prefix / "uv-receipt.toml").write_bytes((original / "uv-receipt.toml").read_bytes())
    install(prefix, {"heinzel": "9.9.9", "heinzel-clock": "1.0.0", "urllib3": "2.0.0"})
    (prefix / ".heinzel-startup-pending").touch(mode=0o600)


def interrupt_after_prefix_replacement(update, tool, state, monkeypatch):
    def replace_prefix(*args, **kwargs):
        replace_tool_prefix(tool)
        raise UpdateInterrupted

    monkeypatch.setattr(tool, "run", replace_prefix)
    with pytest.raises(UpdateInterrupted):
        update.apply_announced(news(), current="1.0.0", folder=state / "auto-update", trial=lambda _: True)


def _kill_after_scope_verification(update, tool, marker, token, monkeypatch):
    lifecycle = update._lifecycle

    @contextmanager
    def mutation(checkpoint, rollback_identity):
        authority = MutationAuthority(
            pending_path=marker,
            token_matches=lambda token: marker_token_matches(marker.read_bytes(), token),
            capture=lifecycle.capture,
            request=lifecycle.request,
            fingerprint=tool.startup_fingerprint,
            checkpoint=checkpoint,
            had_checkpoint=checkpoint(),
            original_fingerprint=tool.startup_fingerprint(),
            original_identity=rollback_identity(),
            rollback_identity=rollback_identity,
        )
        scope = MutationScope(token, authority)
        yield scope
        scope.commit()

    verify = MutationScope.verified_complete

    def die_before_disarm(scope, expected):
        verify(scope, expected)
        os.kill(os.getpid(), signal.SIGKILL)

    monkeypatch.setattr(lifecycle, "mutation", mutation)
    monkeypatch.setattr(MutationScope, "verified_complete", die_before_disarm)


def _kill_at_update_phase(tmp_path, phase):
    """Subprocess entry point for process death, including the absence of finally handlers."""
    state = tmp_path / "state"
    state_port = FileInstallationState(state)
    with pytest.MonkeyPatch.context() as patch:
        if phase == "recovery":
            tool = Installation(Progress(), prefix=tmp_path / "tools/heinzel", uv="unused")
            patch.setattr(tool, "record_startup_state", lambda **kw: os.kill(os.getpid(), signal.SIGKILL))
            UpdateService(tool, Releases(), state=state_port).recover_announced(state / "auto-update")
            return

        update, tool, _, _ = service(tmp_path, patch)
        update._state = state_port
        token = "a" * 64
        marker = state / "daemon/mutation-pending.json"
        owner = mutation_record(token, os.getpid(), None)
        write_private(marker, json.dumps(owner, indent=2) + "\n")
        (tmp_path / "before.json").write_text(json.dumps([tool.startup_fingerprint(), tool.content_fingerprint()]))

        if phase == "partial":
            copytree = announced.shutil.copytree

            def die_during_copy(src, dst, **kwargs):
                copytree(src, dst, **kwargs)
                os.kill(os.getpid(), signal.SIGKILL)

            patch.setattr(announced.shutil, "copytree", die_during_copy)
        elif phase == "commit":
            patch.setattr(tool, "record_startup_state", lambda **kw: os.kill(os.getpid(), signal.SIGKILL))
        elif phase == "verified":
            _kill_after_scope_verification(update, tool, marker, token, patch)

        def die_during_update(*args, **kwargs):
            if phase in {"commit", "install", "verified"}:
                replace_tool_prefix(tool)
            if phase in {"partial", "snapshot", "install"}:
                os.kill(os.getpid(), signal.SIGKILL)

        patch.setattr(tool, "run", die_during_update)
        update.apply_announced(news(), current="1.0.0", folder=state / "auto-update", trial=lambda _: True)


def kill_at_update_phase(tmp_path, phase):
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import runpy, sys; from pathlib import Path; "
                "runpy.run_path(sys.argv[1])['_kill_at_update_phase'](Path(sys.argv[2]), sys.argv[3])"
            ),
            __file__,
            str(tmp_path),
            phase,
        ],
        check=False,
        timeout=20,
    )
    assert child.returncode == -signal.SIGKILL


@pytest.mark.parametrize("phase", ["partial", "snapshot", "install", "commit", "verified", "recovery"])
def test_killed_updater_restores_verified_continuity(tmp_path, phase):
    kill_at_update_phase(tmp_path, "install" if phase == "recovery" else phase)
    state = tmp_path / "state"
    kernel = SimpleNamespace(settings=SimpleNamespace(state_dir=state))
    tool = Installation(Progress(), prefix=tmp_path / "tools/heinzel", uv="unused")
    update = UpdateService(tool, Releases(), state=FileInstallationState(state))
    original_generation, original_content = json.loads((tmp_path / "before.json").read_text())
    snapshot = state / "auto-update/before-update"
    partial = snapshot.with_suffix(".partial")
    assert partial.is_dir() == (phase == "partial")
    assert snapshot.is_dir() == (phase != "partial")
    assert installation_mutation_pending(tool.prefix, state)

    def recover_daemon():
        return recover_abandoned_mutation(state, tool, lambda: Evidence(None, 1), lambda: datetime.now(UTC))

    assert recover_daemon() is None
    if phase == "verified":
        assert tool.installed_core_version() == "9.9.9"
        assert tool.startup_state_matches()
        assert not (state / "daemon/handover-request.json").exists()
    if phase == "recovery":
        kill_at_update_phase(tmp_path, "recovery")
        assert snapshot.is_dir()
        assert not list(snapshot.iterdir())
        assert installation_mutation_pending(tool.prefix, state)

    assert recover_announced(update, kernel) == (phase != "partial")
    assert tool.content_fingerprint() == original_content
    assert (tool.startup_fingerprint() == original_generation) == (phase in {"partial", "snapshot"})
    assert tool.startup_state_matches()
    receipt = recover_daemon()
    assert receipt is not None
    assert json.loads(receipt.read_text())["status"] == "archived"
    assert not installation_mutation_pending(tool.prefix, state)
    assert not snapshot.exists()
    assert not partial.exists()


def test_interrupted_update_can_be_recovered_through_a_symlinked_parent(tmp_path, monkeypatch):
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(physical, target_is_directory=True)
    update, tool, _, _ = service(physical, monkeypatch)
    before = tool.content_fingerprint()
    interrupt_after_prefix_replacement(update, tool, alias / "state", monkeypatch)

    with monkeypatch.context() as running:
        running.setattr(sys, "prefix", str(alias / "tools/heinzel"))
        assert Installation(Progress(), uv="unused").prefix == tool.prefix
    fresh = Installation(Progress(), prefix=alias / "tools/heinzel", uv="unused")
    assert fresh.prefix == alias / "tools/heinzel"
    assert UpdateService(fresh, Releases()).recover_announced(alias / "state/auto-update")
    assert tool.content_fingerprint() == before
    assert tool.startup_state_matches()


def test_interrupted_update_refuses_a_symlinked_tool_directory(tmp_path, monkeypatch):
    update, tool, _, _ = service(tmp_path, monkeypatch)
    state = tmp_path / "state"
    interrupt_after_prefix_replacement(update, tool, state, monkeypatch)
    target = tool.prefix.rename(tool.prefix.with_name("recreated"))
    tool.prefix.symlink_to(target, target_is_directory=True)
    with pytest.raises(HeinzelError, match="snapshot is unsafe"):
        update.recover_announced(state / "auto-update")
    assert (state / "auto-update/before-update").is_dir()
    assert (target / "bin/hz").read_text() == "new entrypoint"


def test_server_recovery_holds_the_apply_lock_and_the_installation_lock(tmp_path, monkeypatch, make_kernel):
    update, tool, _, _ = service(tmp_path, monkeypatch)
    state = tmp_path / "state"
    interrupt_after_prefix_replacement(update, tool, state, monkeypatch)
    kernel = make_kernel(builtins=True, settings_=Settings(state_dir=state))
    kernel.bind(Installation, tool)
    restore = ToolSnapshot.restore
    observed = []

    def verify_locks(snapshot, **kwargs):
        for lock in (state / "web/auto-update-apply.lock", state / "update.lock"):
            with lock.open("r+") as held, pytest.raises(BlockingIOError):
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        observed.append(snapshot)
        restore(snapshot, **kwargs)

    monkeypatch.setattr(ToolSnapshot, "restore", verify_locks)
    AutoUpdater(kernel, restart=None, development=None)

    assert len(observed) == 1
    assert tool.startup_state_matches()
    assert not installation_mutation_pending(tool.prefix, state)


@pytest.mark.parametrize("unverified", ["legacy", "modified before update", "modified copy"])
def test_interrupted_update_does_not_record_unverified_continuity(tmp_path, monkeypatch, unverified):
    update, tool, _, _ = service(tmp_path, monkeypatch)
    state = tmp_path / "state"
    kernel = SimpleNamespace(settings=SimpleNamespace(state_dir=state))
    snapshot = state / "auto-update/before-update"
    if unverified == "modified before update":
        (tool.prefix / "bin/hz").write_text("changed outside the updater")
    interrupt_after_prefix_replacement(update, tool, state, monkeypatch)
    if unverified == "legacy":
        (state / "auto-update/.rollback/continuity").unlink()
    if unverified == "modified copy":
        (snapshot / "bin/hz").write_text("changed after the copy")

    with pytest.raises(HeinzelError, match="explicit recovery is required"):
        recover_announced(update, kernel)
    assert not tool.startup_state_matches()
    assert snapshot.is_dir()


def test_everything_is_held_but_the_announced_versions_and_nothing_installed_runs(tmp_path, monkeypatch):
    update, _, lifecycle, runs = service(tmp_path, monkeypatch)
    started = []
    result = update.apply_announced(
        news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: started.append(v) or True
    )
    assert (result.updated, result.after, started) == (True, "9.9.9", ["9.9.9"])
    command, constraints = runs[0]
    assert command[3:5] == ["heinzel", "heinzel-clock"]  # the receipt's requirements, no versions in them
    assert set(constraints) == {"heinzel==9.9.9", "heinzel-clock==1.0.0", "urllib3==2.0.0"}  # dependencies too
    assert "--refresh" not in command
    assert "--reinstall" not in command
    assert command[5:] == [
        "--refresh-package",
        "heinzel",  # only what was announced is asked again and reinstalled
        "--reinstall-package",
        "heinzel",
        "--exclude-newer",
        "2026-11-01T09:00:00Z",  # anything new resolves only among uploads as old as the minimum age
        "--exclude-newer-package",
        "heinzel=2026-11-02T03:00:00Z",
    ]
    assert (lifecycle.requested, lifecycle.pending) == (1, False)


def test_a_dependency_that_moves_is_put_back_and_continuity_holds(tmp_path, monkeypatch):
    update, tool, lifecycle, _ = service(tmp_path, monkeypatch, moves={"urllib3": "2.8.0"})
    result = update.apply_announced(news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert (result.updated, result.restored, result.refused) == (False, True, False)
    assert "urllib3 2.8.0 instead of 2.0.0" in result.message
    assert tool.startup_state_matches()  # the standard app starts as before
    assert not lifecycle.pending  # the mutation completed: no handover is left pending


def test_an_update_that_does_not_start_is_put_back_and_refused(tmp_path, monkeypatch):
    update, tool, lifecycle, _ = service(tmp_path, monkeypatch)
    before = contents(tool.prefix)
    result = update.apply_announced(
        news(plugins={"heinzel-clock": "2.0.0"}), current="1.0.0", folder=tmp_path / "state", trial=lambda v: False
    )
    assert (result.updated, result.restored, result.refused) == (False, True, True)
    assert contents(tool.prefix) == before
    assert tool.startup_state_matches()
    assert not lifecycle.pending


GIT_CRM = '{ name = "heinzel-crm", git = "https://example.test/crm?rev=main" }'


def crm_files(prefix: Path) -> dict[str, str]:
    info = next((prefix / SITE).glob("heinzel_crm-*.dist-info"))
    return {file.name: file.read_text() for file in sorted(info.iterdir())}


@pytest.mark.parametrize("starts", [True, False])
def test_a_plugin_from_git_stays_exactly_as_installed_while_hz_and_pypi_plugins_update(tmp_path, monkeypatch, starts):
    update, tool, _, runs = service(tmp_path, monkeypatch, crm=CRM, plugin=GIT_CRM)
    before = crm_files(tool.prefix)
    result = update.apply_announced(
        news(plugins={"heinzel-clock": "2.0.0"}), current="1.0.0", folder=tmp_path / "state", trial=lambda v: starts
    )
    assert (result.updated, result.restored) == (starts, not starts)
    command, constraints = runs[0]
    assert f"heinzel-crm @ git+https://example.test/crm@{CRM_COMMIT}" in command  # its installed commit, not main
    assert "--reinstall" not in command
    assert "--refresh" not in command
    moving = [command[i + 1] for i, part in enumerate(command) if part in {"--refresh-package", "--reinstall-package"}]
    assert sorted(set(moving)) == ["heinzel", "heinzel-clock"]  # never heinzel-crm: no new build of it
    assert "heinzel-crm==0.3.0" in constraints
    assert crm_files(tool.prefix) == before  # not one of its files changed, after the update or the put-back
    if starts:
        assert ("receipt", ("heinzel-crm",)) in update.retagged  # the receipt follows main again


def test_an_update_that_would_change_a_plugin_from_git_is_put_back(tmp_path, monkeypatch):
    update, tool, lifecycle, _ = service(tmp_path, monkeypatch, crm=CRM, plugin=GIT_CRM, moves={"heinzel-crm": "0.3.0"})
    before = crm_files(tool.prefix)
    result = update.apply_announced(news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert (result.updated, result.restored) == (False, True)
    assert "heinzel-crm" in result.message
    assert crm_files(tool.prefix) == before
    assert not lifecycle.pending


def test_a_plugin_from_git_that_does_not_allow_the_new_hz_stops_the_update_in_one_line(tmp_path, monkeypatch):
    capped = {**CRM, "METADATA": CRM["METADATA"].replace("heinzel>=1", "heinzel<2,>=1")}
    update, tool, _, runs = service(tmp_path, monkeypatch, crm=capped, plugin=GIT_CRM)
    before = crm_files(tool.prefix)
    result = update.apply_announced(news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert (result.updated, runs) == (False, [])
    assert result.message == (
        "heinzel-crm, a plugin updated by hand, doesn't allow hz 9.9.9. Update it first; hz isn't updated by itself."
    )
    assert crm_files(tool.prefix) == before


def test_a_release_tag_installs_the_commit_it_named_and_the_receipt_names_the_tag(tmp_path, monkeypatch):
    core = f'{{ name = "heinzel", git = "{REPO}?rev=v1.0.0" }}'
    update, _, _, runs = service(tmp_path, monkeypatch, core=core)
    result = update.apply_announced(
        news(commit=TAG_COMMIT), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True
    )
    assert result.updated
    assert runs[0][0][3] == f"heinzel @ git+{REPO}@{TAG_COMMIT}"  # the commit, not the tag that could move
    assert update.retagged == [(TAG_COMMIT, "v9.9.9")]


def test_a_release_tag_with_no_recorded_commit_is_updated_by_hand(tmp_path, monkeypatch):
    core = f'{{ name = "heinzel", git = "{REPO}?rev=v1.0.0" }}'
    update, _, _, runs = service(tmp_path, monkeypatch, core=core)
    result = update.apply_announced(news(commit=None), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert (result.updated, result.refused, runs) == (False, True, [])
    assert result.message.endswith("hz isn't updated by itself.")


def test_the_receipt_names_the_tag_again_and_nothing_else_changes():
    receipt = f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev={TAG_COMMIT}" }}]\n'.encode()
    assert _core_by_tag(receipt, TAG_COMMIT, "v9.9.9").decode() == receipt.decode().replace(TAG_COMMIT, "v9.9.9")
    with pytest.raises(HeinzelError):
        _core_by_tag(receipt, "d" * 40, "v9.9.9")


def test_an_announced_plugin_that_is_not_installed_installs_nothing(tmp_path, monkeypatch):
    update, _, _, runs = service(tmp_path, monkeypatch)
    with pytest.raises(
        HeinzelError, match="aren't installed from PyPI or pinned to a tagged GitHub commit: heinzel-other"
    ):
        update.apply_announced(news(plugins={"heinzel-other": "1.0"}), folder=tmp_path / "state", trial=lambda v: True)
    assert runs == []


# A plugin pinned to a tagged GitHub commit (ADR 0137) -----------------------------------------------------------------

CRM_REPO = "https://github.com/acme-example/crm"
TAGGED_CRM = f'{{ name = "heinzel-crm", git = "{CRM_REPO}?rev={CRM_COMMIT}" }}'
NEW_COMMIT = "b" * 40


def crm_at(version: str, commit: str) -> dict[str, str]:
    return {
        **CRM,
        "METADATA": CRM["METADATA"].replace("0.3.0", version),
        "direct_url.json": json.dumps({"url": CRM_REPO, "vcs_info": {"vcs": "git", "commit_id": commit}}),
    }


def tagged(tmp_path, monkeypatch, lands):
    """The update service with heinzel-crm pinned to a GitHub commit; uv leaves it at `lands` (version, commit)."""
    update, tool, lifecycle, runs = service(tmp_path, monkeypatch, crm=CRM, plugin=TAGGED_CRM)

    def run(command, failure, activity, environment=None, *, additional_constraints=()):
        runs.append((command, additional_constraints))
        held = dict(c.split("==") for c in additional_constraints)
        install(tool.prefix, {**held, "heinzel-crm": lands[0]}, {"heinzel-crm": crm_at(*lands)})

    monkeypatch.setattr(tool, "run", run)
    return update, tool, lifecycle, runs


def tagged_news(commit=NEW_COMMIT):
    return Announcement(None, {"heinzel-crm": "0.4.0"}, plugin_commits={"heinzel-crm": commit}, exclude_newer=CUTOFF)


def test_a_tagged_plugin_installs_exactly_the_commit_its_tag_named_and_stays_pinned(tmp_path, monkeypatch):
    update, tool, lifecycle, runs = tagged(tmp_path, monkeypatch, ("0.4.0", NEW_COMMIT))
    result = update.apply_announced(tagged_news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert (result.updated, result.after) == (True, "1.0.0")  # a plugins-only update keeps the running hz
    command, constraints = runs[0]
    assert f"heinzel-crm @ git+{CRM_REPO}@{NEW_COMMIT}" in command  # the recorded commit, never the tag that can move
    moving = [command[i + 1] for i, part in enumerate(command) if part in {"--refresh-package", "--reinstall-package"}]
    assert moving == ["heinzel-crm", "heinzel-crm"]  # only it is asked again and rebuilt
    assert {"heinzel-crm==0.4.0", "heinzel-clock==1.0.0", "heinzel==1.0.0"} <= set(constraints)
    assert ("receipt", ("heinzel-crm",)) not in update.retagged  # the receipt names the new commit: still pinned
    assert announced.plugin_commit(tool.prefix, "heinzel-crm") == NEW_COMMIT
    assert not lifecycle.pending


@pytest.mark.parametrize(
    ("lands", "why"),
    [
        (("0.4.0", "d" * 40), "heinzel-crm came from commit dddddddddddd, not bbbbbbbbbbbb"),
        (("0.3.9", NEW_COMMIT), "heinzel-crm 0.3.9 instead of 0.4.0"),
    ],
)
def test_a_tagged_plugin_that_lands_at_another_commit_or_version_is_put_back(tmp_path, monkeypatch, lands, why):
    update, tool, lifecycle, _ = tagged(tmp_path, monkeypatch, lands)
    before = crm_files(tool.prefix)
    result = update.apply_announced(tagged_news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert (result.updated, result.restored) == (False, True)
    assert why in result.message
    assert crm_files(tool.prefix) == before
    assert tool.startup_state_matches()
    assert not lifecycle.pending


def test_a_commit_announced_for_a_plugin_on_a_branch_installs_nothing(tmp_path, monkeypatch):
    update, _, _, runs = service(tmp_path, monkeypatch, crm=CRM, plugin=GIT_CRM)
    with pytest.raises(HeinzelError, match="pinned to a tagged GitHub commit: heinzel-crm"):
        update.apply_announced(tagged_news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert runs == []


def test_the_receipt_follows_a_git_plugins_branch_again_and_nothing_else_changes():
    from heinzel.kernel.install._installation import _requirements_from

    original = (
        '[tool]\nrequirements = [{ name = "heinzel" }, '
        '{ name = "heinzel-crm", git = "https://example.test/crm?rev=main" }]\n'
    )
    pinned = original.replace("rev=main", f"rev={CRM_COMMIT}").encode()
    assert _requirements_from(pinned, original.encode(), ["heinzel-crm"]).decode() == original


# A failed change's continuity marker (the Mini, 0.9.27 to 0.9.28) -----------------------------------------------------


class CheckingLifecycle(Lifecycle):
    """Like `system.daemon`: the checkpoint is asked when the mutation begins."""

    def mutation(self, checkpoint, rollback_identity):
        self.checkpoint_matched = checkpoint()
        return super().mutation(checkpoint, rollback_identity)


def test_a_put_back_removes_the_marker_its_failed_change_left(tmp_path, monkeypatch):
    update, tool, lifecycle, _ = service(tmp_path, monkeypatch, moves={"urllib3": "2.8.0"})
    lifecycle.checkpoint_matched = False  # no record is written on the way back: only the repair removes it
    marker = tool.prefix / ".heinzel-startup-pending"
    install = tool.run

    def failing_half_way(*args, **kwargs):
        install(*args, **kwargs)
        marker.touch(mode=0o600)  # as a continuity write that failed after taking its marker leaves it

    monkeypatch.setattr(tool, "run", failing_half_way)
    result = update.apply_announced(news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert (result.updated, result.restored) == (False, True)
    assert not marker.exists()
    assert tool.startup_state_matches()  # web restarts and later updates are no longer held


def test_the_next_update_repairs_a_stale_marker_and_records_its_new_generation(tmp_path, monkeypatch):
    update, tool, _, _ = service(tmp_path, monkeypatch)
    lifecycle = CheckingLifecycle()
    update._lifecycle = lifecycle
    marker = tool.prefix / ".heinzel-startup-pending"
    marker.touch(mode=0o600)  # left by an earlier automatic update that was put back
    result = update.apply_announced(news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert result.updated
    assert lifecycle.checkpoint_matched  # the tool matched its record, so the chain of verified generations holds
    assert not marker.exists()
    assert tool.startup_state_matches()  # the new generation is recorded, not the old one


def test_a_marker_over_an_uncertain_installation_stays(tmp_path, monkeypatch):
    update, tool, _, _ = service(tmp_path, monkeypatch)
    lifecycle = CheckingLifecycle()
    update._lifecycle = lifecycle
    (tool.prefix / "bin/hz").write_text("changed outside any update")
    marker = tool.prefix / ".heinzel-startup-pending"
    marker.touch(mode=0o600)
    update.apply_announced(news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert not lifecycle.checkpoint_matched
    assert marker.exists()
