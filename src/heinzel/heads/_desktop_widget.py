"""Install-only graceful widget quiescence and conservative retired-image cleanup."""

import os
import plistlib
import stat
import subprocess
import time
from pathlib import Path
from typing import cast
from xml.parsers.expat import ExpatError

from heinzel.heads._desktop_native import (
    CodeIdentity,
    ExecutableIdentity,
    ImageGoneError,
    Native,
    NativeUnavailableError,
    ProcessIdentity,
)

WIDGET = Path("Contents/PlugIns/HeinzelWidgets.appex")
WIDGET_EXECUTABLE = WIDGET / "Contents/MacOS/HeinzelWidgets"
_TIMEOUT = 5.0
_MAX_PLIST_BYTES = 65536
_ERRORS = (NativeUnavailableError, OSError, ValueError, AttributeError, subprocess.SubprocessError, ExpatError)


def _pids() -> tuple[int, ...]:
    result = subprocess.run(["/bin/ps", "-axo", "pid="], check=True, capture_output=True, timeout=10)
    if result.stderr or not result.stdout or len(result.stdout) > 8 * 1024 * 1024:
        raise NativeUnavailableError
    values = tuple(int(line.strip()) for line in result.stdout.splitlines())
    if any(pid < 0 for pid in values) or len(values) != len(set(values)):
        raise NativeUnavailableError
    return tuple(pid for pid in values if pid != 0)  # PID0 is the kernel, never an app image.


def inventory() -> tuple[ExecutableIdentity, ...]:
    """Discover PIDs only through ps; classify exclusively from generation-bound kernel paths.

    Raises:
        NativeUnavailableError: If any still-live process has an unknown image.
    """
    native = Native()
    rows: list[ExecutableIdentity] = []
    for pid in _pids():
        try:
            rows.append(native.executable(pid))
        except ImageGoneError:
            # Its executable has no path any more (deleted, or its folder replaced by an update), so it runs from no
            # bundle on disk: an unrelated program updated while it runs must not make Heinzel look open.
            continue
        except NativeUnavailableError:
            # A vanished/zombie process is no longer using resources. Permission errors,
            # fallback names and unreadable live processes never become unrelated/idle.
            if not native.zombie(pid) and pid in _pids():
                raise
    return tuple(rows)


def uses(row: ExecutableIdentity, bundle: Path) -> bool:
    """Keep the original conservative translocation boundary."""
    return row.path.is_relative_to(bundle) or (
        "/AppTranslocation/" in str(row.path) and f"/{bundle.name}/" in str(row.path)
    )


def can_stage(bundle: Path) -> bool:
    """Classify widget-only use; this does not authorize any signal or replacement."""
    try:
        return all(row.path == bundle / WIDGET_EXECUTABLE for row in inventory() if uses(row, bundle))
    except _ERRORS:
        return False


def _plain(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid_type or info.st_uid != os.getuid() or info.st_mode & 0o022 or (not directory and info.st_nlink != 1):
        raise NativeUnavailableError


def _widget_identity(native: Native, bundle: Path) -> CodeIdentity:
    extension = bundle / WIDGET
    for component in (
        bundle,
        bundle / "Contents",
        bundle / "Contents/PlugIns",
        extension,
        extension / "Contents",
        extension / "Contents/MacOS",
    ):
        _plain(component, directory=True)
    metadata = extension / "Contents/Info.plist"
    _plain(metadata, directory=False)
    _plain(bundle / WIDGET_EXECUTABLE, directory=False)
    if metadata.stat().st_size > _MAX_PLIST_BYTES:
        raise NativeUnavailableError
    info = plistlib.loads(metadata.read_bytes())
    values = cast("dict[str, object]", info) if isinstance(info, dict) else {}
    extension_info = values.get("NSExtension")
    extension_values = cast("dict[str, object]", extension_info) if isinstance(extension_info, dict) else {}
    if (
        values.get("CFBundleIdentifier"),
        values.get("CFBundleExecutable"),
        extension_values.get("NSExtensionPointIdentifier"),
    ) != ("team.heinzel.apple.widgets", "HeinzelWidgets", "com.apple.widgetkit-extension"):
        raise NativeUnavailableError
    app, widget = native.static(bundle), native.static(extension)
    if (
        app.identifier != "team.heinzel.apple"
        or widget.identifier != "team.heinzel.apple.widgets"
        or app.team != widget.team
        or app.adhoc != widget.adhoc
        or (widget.adhoc and widget.team)
        or (not widget.adhoc and not widget.team)
    ):
        raise NativeUnavailableError
    return widget


def _targets(native: Native, bundle: Path, expected: CodeIdentity) -> tuple[ProcessIdentity, ...]:
    targets: list[ProcessIdentity] = []
    for row in inventory():
        if not uses(row, bundle):
            continue
        if row.path != bundle / WIDGET_EXECUTABLE:
            raise NativeUnavailableError
        process = native.process(row.pid)
        if (process.uid, process.real_uid, process.saved_uid) != (os.getuid(),) * 3 or (
            process.unique_id,
            process.version,
        ) != (row.unique_id, row.version):
            raise NativeUnavailableError
        if native.executable(process.pid) != row or native.running(process) != expected:
            raise NativeUnavailableError
        targets.append(process)
    return tuple(targets)


def _await_exit(native: Native, bundle: Path, targets: tuple[ProcessIdentity, ...]) -> bool:
    deadline = time.monotonic() + _TIMEOUT
    original = {process.pid: process for process in targets}
    while True:
        remaining = [row for row in inventory() if uses(row, bundle)]
        if not remaining:
            return True
        # A replacement generation or another executable is never signaled again.
        if any(row.pid not in original or native.process(row.pid) != original[row.pid] for row in remaining):
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def quiesce(bundle: Path) -> bool:
    """Validate every target before requesting one graceful exit; never stop a main app/helper."""
    try:
        native = Native()
        expected = _widget_identity(native, bundle)
        targets = _targets(native, bundle, expected)
        # Revalidate the complete set and static identity before the first signal.
        if _widget_identity(native, bundle) != expected:
            return False
        if any(
            native.process(process.pid) != process
            or native.executable(process.pid)
            != ExecutableIdentity(process.pid, bundle / WIDGET_EXECUTABLE, process.unique_id, process.version)
            or native.running(process) != expected
            for process in targets
        ):
            return False
        if not all(native.request_exit(process) for process in targets):
            return False
        return _await_exit(native, bundle, targets)
    except _ERRORS:
        return False


def retired_in_use(retired: Path, public: Path) -> bool:
    """A cached public path may still identify old mapped code; compare dynamic image identity."""
    try:
        native = Native()
        for row in inventory():
            if uses(row, retired):
                return True
            if not uses(row, public):
                continue
            if not row.path.is_relative_to(public):
                return True
            old_path = retired / row.path.relative_to(public)
            # Unknown old executable, inaccessible process, or identity race retains all resources.
            before = native.process(row.pid)
            if (before.unique_id, before.version) != (row.unique_id, row.version):
                return True
            old = native.static(old_path)
            running = native.running(before)
            if native.process(row.pid) != before or running.digest == old.digest:
                return True
    except _ERRORS:
        return True
    return False


def in_use(bundle: Path) -> bool:
    """Native replacement's final guard never uses argv-based legacy process detection."""
    try:
        return any(uses(row, bundle) for row in inventory())
    except _ERRORS:
        return True


def register(bundle: Path) -> bool:
    """An unsuccessful registration remains retryable in the committed journal."""
    try:
        return Native().register(bundle)
    except _ERRORS:
        return False
