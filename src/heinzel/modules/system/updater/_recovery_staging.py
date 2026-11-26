"""Stage and use a sealed recovery runtime for a supervised automatic update."""

import json
import shutil
import stat
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from heinzel import HeinzelError
from heinzel.kernel import (
    Installation,
    MaintenanceLocks,
    host_maintenance_lock,
    installation_mutation_paths,
)
from heinzel.kernel._private_control import private_directory, private_write
from heinzel.kernel.install._installation_continuity import tool_python
from heinzel.modules.system._recovery_boot import NAME, file_digest, owned_path, path_absent, seal_tree
from heinzel.modules.system.updater._announced import sync_directory, sync_tree


def _requires_recovery(state: Path, launcher_ready: Callable[[Path], bool] | None) -> bool:
    directory = state / "ui"
    marker, launcher = directory / "service.json", directory / "launch.sh"
    if path_absent(marker) and path_absent(launcher):
        return False
    try:
        if launcher_ready is not None and launcher_ready(state):
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
    sync_tree(staged)
    staged.rename(destination)
    sync_directory(state)


def _size_for_snapshot(prefix: Path) -> int:
    return sum(info.st_size for path in prefix.rglob("*") if stat.S_ISREG((info := path.lstat()).st_mode))


def retire_staged_recovery(state: Path) -> None:
    published = state / NAME
    for staged in (published, published.with_suffix(".partial")):
        if not path_absent(staged):
            owned_path(staged, private=True)
            shutil.rmtree(staged)
            sync_directory(state)


@contextmanager
def supervised_recovery(
    installation: Installation, folder: Path, launcher_ready: Callable[[Path], bool] | None
) -> Generator[None]:
    """Prepare a durable worker before the first mutation marker, then retire it once all fences are absent."""
    if folder.name != "auto-update":
        yield
        return
    installation = installation.for_interrupted_update()
    state = folder.parent.resolve()
    with MaintenanceLocks((host_maintenance_lock(installation.prefix), state / "ui/lifecycle.lock")):
        if not _requires_recovery(state, launcher_ready):
            yield
            return
        _require_clear(installation, state)
        retire_staged_recovery(state)
        try:
            _prepare(installation, state)
            yield
        finally:
            if all(path_absent(path) for path in installation_mutation_paths(installation.prefix, state)):
                daemon = state / "daemon"
                if not path_absent(daemon):
                    sync_directory(daemon)
                retire_staged_recovery(state)


def _require_clear(installation: Installation, state: Path) -> None:
    if any(not path_absent(path) for path in installation_mutation_paths(installation.prefix, state)):
        msg = "Another installation change requires recovery before an automatic update."
        raise HeinzelError(msg)
