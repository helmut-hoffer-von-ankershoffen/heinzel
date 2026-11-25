"""Stage and use a sealed recovery runtime for a supervised automatic update."""

import json
import shutil
import stat
import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from heinzel import HeinzelError, Progress
from heinzel.kernel import (
    Installation,
    MaintenanceLocks,
    host_maintenance_lock,
    installation_mutation_paths,
    private_lock,
)
from heinzel.kernel._private_control import private_directory, private_write
from heinzel.kernel.install._installation_continuity import tool_python
from heinzel.modules.system.daemon._abandoned_mutation import (
    Evidence,
    _owner_ended,
    host_boot,
    parse_marker,
    recover_abandoned_mutation,
)
from heinzel.modules.system.daemon._recovery_files import path_absent, recovery_file
from heinzel.modules.system.lifecycle._recovery_boot import NAME, file_digest, owned_path, seal_tree
from heinzel.modules.system.lifecycle._recovery_launcher import interpreter_matches
from heinzel.modules.system.lifecycle._service import WebService
from heinzel.modules.system.updater._announced import ToolSnapshot, _sync_directory, _sync_tree


def _requires_recovery(installation: Installation, state: Path) -> bool:
    directory = state / "ui"
    marker, launcher = directory / "service.json", directory / "launch.sh"
    if path_absent(marker) and path_absent(launcher):
        return False
    try:
        _, binary = installation.tool_destination()
        prefix = installation.prefix
        service = WebService(state, Path.home(), binary / "hz", prefix=prefix, uv=prefix / "bin/uv")
        selected = service.recovery_interpreter()
        if selected is not None and interpreter_matches(selected, prefix):
            return True
    except (OSError, ValueError, HeinzelError):
        pass
    msg = "Migrate the owned web launcher with `hz ui install` before automatic updates can apply."
    raise HeinzelError(msg)


def _size(source: Path) -> int:
    total = 0
    for path in source.rglob("*"):
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            msg = "The installed runtime contains links or special files and cannot be staged for automatic recovery."
            raise HeinzelError(msg)
        if stat.S_ISREG(info.st_mode) and not path.name.endswith((".pyc", ".pyo")):
            total += info.st_size
    return total


def _prepare(installation: Installation, state: Path) -> None:
    prefix = installation.prefix
    selected = list(prefix.glob("lib/python*/site-packages"))
    if len(selected) != 1 or not installation.startup_state_matches():
        msg = "The running tool cannot be verified for automatic recovery."
        raise HeinzelError(msg)
    source = selected[0]
    owned_path(source)
    if state.stat().st_dev != prefix.stat().st_dev:
        msg = "The rollback needs the state folder on the same disk as the installed tool."
        raise HeinzelError(msg)
    # Recovery needs another copy of the importable runtime in addition to the ordinary rollback snapshot.
    if shutil.disk_usage(state).free <= _size(source) + _size_for_snapshot(prefix):
        msg = "There is insufficient space to stage automatic update recovery."
        raise HeinzelError(msg)
    generation = installation.startup_fingerprint()
    content = installation.content_fingerprint()
    executable, _, _ = tool_python(prefix)
    executable_digest = file_digest(Path(executable))
    if Path(executable).is_relative_to(prefix):
        msg = "Automatic recovery needs a Python interpreter outside the mutable tool."
        raise HeinzelError(msg)
    destination = state / NAME
    private_directory(state)
    staged = destination.with_suffix(".partial")
    staged.mkdir(mode=0o700)
    library = staged / "site"
    shutil.copytree(source, library, symlinks=True, ignore=shutil.ignore_patterns("*.pyc", "*.pyo"))
    library.chmod(0o700)
    _, seal = seal_tree(library)
    if installation.startup_fingerprint() != generation or not installation.startup_state_matches():
        msg = "The running tool changed while staging automatic recovery."
        raise HeinzelError(msg)
    private_write(staged / "proof.json", json.dumps([executable, executable_digest, seal, content]))
    _sync_tree(staged)
    staged.rename(destination)
    _sync_directory(state)


def _size_for_snapshot(prefix: Path) -> int:
    return sum(info.st_size for path in prefix.rglob("*") if stat.S_ISREG((info := path.lstat()).st_mode))


def _remove(state: Path) -> None:
    published = state / NAME
    for staged in (published, published.with_suffix(".partial")):
        if not path_absent(staged):
            owned_path(staged, private=True)
            shutil.rmtree(staged)
            _sync_directory(state)


@contextmanager
def supervised_recovery(installation: Installation, folder: Path) -> Generator[None]:
    """Prepare a durable worker before the first mutation marker, then retire it once all fences are absent."""
    if folder.name != "auto-update":
        yield
        return
    installation = installation.for_interrupted_update()
    state = folder.parent.resolve()
    with MaintenanceLocks((host_maintenance_lock(installation.prefix), state / "ui/lifecycle.lock")):
        if not _requires_recovery(installation, state):
            yield
            return
        _require_clear(installation, state)
        _remove(state)
        try:
            _prepare(installation, state)
            yield
        finally:
            if all(path_absent(path) for path in installation_mutation_paths(installation.prefix, state)):
                daemon = state / "daemon"
                if not path_absent(daemon):
                    _sync_directory(daemon)
                _remove(state)


def _require_clear(installation: Installation, state: Path) -> None:
    if any(not path_absent(path) for path in installation_mutation_paths(installation.prefix, state)):
        msg = "Another installation change requires recovery before an automatic update."
        raise HeinzelError(msg)


def _restore(installation: Installation, state: Path, content: str) -> None:
    daemon = state / "daemon"
    snapshot, mutation = state / "auto-update/before-update", daemon / "mutation-pending.json"
    blocked = {*installation_mutation_paths(installation.prefix, state), daemon / "handover-pending.json"}
    blocked.difference_update((snapshot, mutation, installation.prefix / ".heinzel-startup-pending"))
    if any(not path_absent(path) for path in blocked):
        msg = "Another lifecycle operation is pending."
        raise HeinzelError(msg)
    marker = recovery_file(mutation)
    owner = parse_marker(marker.content)
    evidence = Evidence(*host_boot())
    if owner is None or owner.pid is None or _owner_ended(owner, marker, evidence) is None:
        msg = "The automatic updater's owner has not provably ended."
        raise HeinzelError(msg)
    if not path_absent(snapshot):
        descriptor = ToolSnapshot(installation.prefix, snapshot, snapshot.with_name("after-update"))
        if descriptor.recovery_content() != content:
            msg = "The worker does not match the interrupted update's verified snapshot."
            raise HeinzelError(msg)
    ToolSnapshot.recover_interrupted(installation, state / "auto-update")
    if not installation.repaired_startup_state_matches():
        msg = "The installation's continuity could not be recovered."
        raise HeinzelError(msg)
    if recover_abandoned_mutation(state, installation, lambda: evidence, lambda: datetime.now(UTC)) is None:
        msg = "The interrupted automatic update could not be archived."
        raise HeinzelError(msg)


def main() -> int:
    """Recover using the same writers as the web scheduler, installer and daemon."""
    prefix, state = map(Path, sys.argv[1:3])
    content = sys.argv[3]
    installation = Installation(Progress(), prefix=prefix, uv="unused")
    daemon = state / "daemon"
    try:
        with (
            private_lock(state / "web" / "auto-update-apply.lock"),
            private_lock(state / "update.lock"),
            private_lock(host_maintenance_lock(prefix)),
            private_lock(daemon / "lifecycle.lock"),
            private_lock(daemon / "tick.lock"),
        ):
            _restore(installation, state, content)
            _remove(state)
    except (OSError, ValueError, HeinzelError):
        return 75
    return 0
