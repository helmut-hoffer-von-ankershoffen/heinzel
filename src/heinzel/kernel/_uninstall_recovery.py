"""Standalone offline removal recovery, copied before its SDK disappears (ADR 0131)."""

import fcntl
import hashlib
import json
import os
import selectors
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 100_000
MAX_SDK_BYTES = 2_000_000_000
MANAGER_COLUMNS = 3
EXPECTED_AUTHORITY = ""
DAEMON = "io.github.helmut-hoffer-von-ankershoffen.heinzel.daemon"
WEB = "io.github.helmut-hoffer-von-ankershoffen.heinzel.web"


def identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def physical(path: Path, *, private: bool = True) -> None:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        msg = "Recovery path is redirected"
        raise ValueError(msg)
    for part in (path, *path.parents):
        info = part.lstat()
        sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or (info.st_mode & 0o022 and not sticky)
        ):
            msg = "Recovery path ownership changed"
            raise ValueError(msg)
    if path.stat().st_uid != os.getuid() or (private and path.stat().st_mode & 0o077):
        msg = "Recovery directory is not private"
        raise ValueError(msg)


def sync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read(path: Path, limit: int = MAX_BYTES, *, private: bool = True) -> bytes:
    physical(path.parent, private=private)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_size > limit:
            msg = "Recovery file ownership changed"
            raise ValueError(msg)
        data = bytearray()
        while len(data) <= limit:
            part = os.read(fd, min(1024 * 1024, limit + 1 - len(data)))
            if not part:
                break
            data.extend(part)
        if len(data) != info.st_size or identity(info) != identity(os.fstat(fd)):
            msg = "Recovery file changed during inspection"
            raise ValueError(msg)
        return bytes(data)
    finally:
        os.close(fd)


def write(path: Path, value: dict[str, Any]) -> None:
    physical(path.parent)
    if os.path.lexists(path):
        read(path)
    fd, temporary = tempfile.mkstemp(prefix=".journal-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
        sync(path.parent)
    finally:
        if os.path.lexists(temporary):
            Path(temporary).unlink()


class _Manifest:
    def __init__(self) -> None:
        self.result: dict[str, list[int | str]] = {}
        self.bytes = 0

    def visit(self, directory: int, relative: Path) -> None:
        with os.scandir(directory) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if info.st_uid != os.getuid() or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
                msg = "SDK entry ownership changed"
                raise ValueError(msg)
            if name.endswith((".pyc", ".pyo")) and stat.S_ISREG(info.st_mode):
                continue
            self.entry(directory, name, relative / name, info)
            if len(self.result) > MAX_FILES:
                msg = "SDK recovery manifest exceeds its budget"
                raise ValueError(msg)

    def entry(self, directory: int, name: str, path: Path, info: os.stat_result) -> None:
        row: list[int | str] = [info.st_dev, info.st_ino, info.st_mode, info.st_uid]
        if stat.S_ISLNK(info.st_mode):
            row.append(os.readlink(name, dir_fd=directory))
        else:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                if identity(os.fstat(descriptor)) != identity(info):
                    msg = "SDK entry changed during capture"
                    raise ValueError(msg)
                if stat.S_ISDIR(info.st_mode):
                    self.visit(descriptor, path)
                elif stat.S_ISREG(info.st_mode):
                    row.append(self.digest(descriptor, info))
                else:
                    msg = "Unsupported SDK entry"
                    raise ValueError(msg)
            finally:
                os.close(descriptor)
        if name != "__pycache__" or not stat.S_ISDIR(info.st_mode):
            self.result[str(path)] = row

    def digest(self, descriptor: int, info: os.stat_result) -> str:
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            self.bytes += len(chunk)
            if self.bytes > MAX_SDK_BYTES:
                msg = "SDK recovery content exceeds its budget"
                raise ValueError(msg)
            digest.update(chunk)
        if identity(os.fstat(descriptor)) != identity(info):
            msg = "SDK entry changed during capture"
            raise ValueError(msg)
        return digest.hexdigest()


def manifest(prefix: Path) -> dict[str, list[int | str]]:
    """Capture bounded opened SDK entries without following links; generated bytecode is excluded."""
    captured = _Manifest()
    descriptor = os.open(prefix, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        captured.visit(descriptor, Path())
    finally:
        os.close(descriptor)
    return captured.result


def python_continuity(path: Path) -> str:
    content = read(path, 128 * 1024 * 1024, private=False)
    return hashlib.sha256(len(content).to_bytes(8, "big") + content).hexdigest()


def authority_digest(journal: dict[str, Any]) -> str:
    selected = {key: value for key, value in journal.items() if key != "phase"}
    return hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _manager(command: list[str], platform: str) -> str:
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C"}
    if platform == "linux":
        environment["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    return _bounded_output(command, environment)


def _bounded_output(command: list[str], environment: dict[str, str], *, deadline: float | None = None) -> str:
    deadline = time.monotonic() + 5 if deadline is None else deadline
    process = subprocess.Popen(  # noqa: S603 - fixed native manager; never a shell or a service PID
        command, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    content = bytearray()
    try:
        if process.stdout is None:
            msg = "Service manager output pipe is unavailable"
            raise ValueError(msg)
        descriptor = process.stdout.fileno()
        os.set_blocking(descriptor, False)
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, 5)
                if not selector.select(remaining):
                    raise subprocess.TimeoutExpired(command, 5)
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > 4 * 1024 * 1024:
                    msg = "Service manager output exceeds its budget"
                    raise ValueError(msg)
        code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        if code:
            raise subprocess.CalledProcessError(code, command)
        return content.decode("utf-8")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)
        if process.stdout is not None:
            process.stdout.close()


def managers_absent(journal: dict[str, Any]) -> None:
    """Resume only after fixed user managers confirm stopped jobs; never send stop/signals.

    Raises:
        ValueError: If the user session, bus or stopped evidence cannot be verified.
    """
    if journal["platform"] == "darwin":
        _mac_absent(journal)
    elif journal["platform"] == "linux":
        _linux_absent(journal)
    else:
        msg = "Unsupported recovery platform"
        raise ValueError(msg)


def _mac_absent(journal: dict[str, Any] | None = None) -> None:
    if _manager(["/bin/launchctl", "managername"], "darwin").strip() != "Aqua" or _manager(
        ["/bin/launchctl", "manageruid"], "darwin"
    ).strip() != str(os.getuid()):
        msg = "Recovery is not in the original user service session"
        raise ValueError(msg)
    lines = _manager(["/bin/launchctl", "list"], "darwin").splitlines()
    if (
        not lines
        or lines[0].split() != ["PID", "Status", "Label"]
        or any(len(line.split()) != MANAGER_COLUMNS for line in lines[1:])
    ):
        msg = "Service manager evidence is incomplete"
        raise ValueError(msg)
    if any(line.split()[-1] in {DAEMON, WEB, *(journal or {}).get("manager_labels", [])} for line in lines[1:]):
        msg = "A service is still loaded; inspect its local manager before recovery"
        raise ValueError(msg)
    if _manager(["/bin/launchctl", "managername"], "darwin").strip() != "Aqua" or _manager(
        ["/bin/launchctl", "manageruid"], "darwin"
    ).strip() != str(os.getuid()):
        msg = "The macOS user session changed during verification"
        raise ValueError(msg)


def _linux_identity() -> tuple[int, ...]:
    runtime = Path(f"/run/user/{os.getuid()}")
    physical(runtime)
    for parent in (Path("/run"), Path("/run/user")):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            msg = "The Linux manager parent authority changed"
            raise ValueError(msg)
    directory = (runtime / "systemd").lstat()
    endpoint = (runtime / "systemd/private").lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != os.getuid()
        or directory.st_mode & 0o022
        or not stat.S_ISSOCK(endpoint.st_mode)
        or endpoint.st_uid != os.getuid()
    ):
        msg = "The Linux manager socket authority changed"
        raise ValueError(msg)
    return directory.st_dev, directory.st_ino, endpoint.st_dev, endpoint.st_ino


def _properties(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            msg = "Service manager properties are ambiguous"
            raise ValueError(msg)
        values[key] = value
    return values


def manager_properties(output: str) -> dict[str, str]:
    """Expose the same bounded unique-property grammar to in-process maintenance guards."""
    return _properties(output)


def manager_identity_matches(values: dict[str, str], expected: dict[str, Any], unit: str) -> bool:
    """Share the exact standalone retained-unit identity check with the host coordinator."""
    return _loaded_identity(values, expected, unit)


def _loaded_identity(values: dict[str, str], expected: dict[str, Any], unit: str) -> bool:
    if (
        values.get("Id") != unit
        or values.get("FragmentPath") != expected["fragment"]
        or values.get("DropInPaths") != ""
    ):
        return False
    if expected.get("disabled") and values.get("UnitFileState") != "disabled":
        return False
    if "triggers" in expected:
        return values.get("Triggers") == expected["triggers"] and not values.get("ExecStart")
    command = values.get("ExecStart", "")
    prefix = f"{{ path={expected['program']} ; argv[]={expected['arguments']} ; ignore_errors=no ;"
    if not command.startswith(prefix) or command.count("argv[]=") != 1 or command.count("{ path=") != 1:
        return False
    return values.get("WorkingDirectory") == expected["directory"] and (
        "environment" not in expected or _environment_identity(values.get("Environment", ""), expected["environment"])
    )


def _environment_identity(raw: str, wanted: dict[str, str]) -> bool:
    environment: dict[str, str] = {}
    for entry in shlex.split(raw):
        key, separator, value = entry.partition("=")
        if not separator or key in environment:
            return False
        environment[key] = value
    return all(environment.get(key) == value for key, value in wanted.items()) and not any(
        key.startswith("HZ_") and key not in wanted for key in environment
    )


def _linux_absent(journal: dict[str, Any]) -> None:
    before = _linux_identity()
    for unit in sorted({"hz-daemon.service", "hz-daemon.timer", "hz-ui.service", *journal["manager_units"]}):
        output = _manager(
            [
                "/usr/bin/systemctl",
                "--user",
                "show",
                unit,
                (
                    "--property=Id,LoadState,FragmentPath,DropInPaths,ExecStart,Triggers,Environment,WorkingDirectory,"
                    "ActiveState,UnitFileState,MainPID,ControlPID,Job"
                ),
            ],
            "linux",
        )
        values = _properties(output)
        if values.get("LoadState") == "not-found":
            if any(values.get(key, "0") != "0" for key in ("MainPID", "ControlPID")) or values.get("Job", ""):
                msg = "An absent service has conflicting execution evidence"
                raise ValueError(msg)
            continue
        expected = journal["manager_units"].get(unit)
        if (
            values.get("LoadState") != "loaded"
            or expected is None
            or not _loaded_identity(values, expected, unit)
            or values.get("ActiveState") != "inactive"
            or values.get("MainPID") != "0"
            or values.get("ControlPID") != "0"
            or values.get("Job") != ""
        ):
            msg = "A service is foreign or not proven stopped"
            raise ValueError(msg)
    if _linux_identity() != before:
        msg = "The Linux manager socket changed during verification"
        raise ValueError(msg)


def _verify_owned(row: dict[str, Any]) -> None:
    path = Path(row["path"])
    if not os.path.lexists(path):
        return
    info = path.lstat()
    if (
        list(identity(info)) != row["identity"]
        or hashlib.sha256(read(path, private=False)).hexdigest() != row["sha256"]
    ):
        msg = "Integration file changed; it was retained"
        raise ValueError(msg)


def _owned_unlink(row: dict[str, Any]) -> None:
    path = Path(row["path"])
    if not os.path.lexists(path):
        return
    _verify_owned(row)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if list(identity(os.stat(path.name, dir_fd=descriptor, follow_symlinks=False))) != row["identity"]:
            msg = "Integration file was replaced before cleanup"
            raise ValueError(msg)
        os.unlink(path.name, dir_fd=descriptor)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _finish_owned_links(journal: dict[str, Any]) -> None:
    for row in journal["links"]:
        _require_gate(journal)
        path = Path(row["path"])
        if not os.path.lexists(path):
            continue
        physical(path.parent, private=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if (
                not stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.getuid()
                or (info.st_dev, info.st_ino) != (row["device"], row["inode"])
                or os.readlink(path.name, dir_fd=directory) != row["target"]
            ):
                msg = "A remaining command link changed; it was retained"
                raise ValueError(msg)
            os.unlink(path.name, dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(directory)


def _require_gate(journal: dict[str, Any]) -> None:
    if read(Path(journal["gate"]), 1024).decode() != journal["gate_content"]:
        msg = "The installation recovery gate changed"
        raise ValueError(msg)


def _finished_targets(journal: dict[str, Any]) -> None:
    paths = [Path(journal["prefix"]), *(Path(name) for name in journal["entrypoints"])]
    paths.extend(Path(row["path"]) for row in journal["files"])
    for path in paths:
        physical(path.parent, private=False)
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        msg = "The absent recovery gate does not authorize unfinished cleanup"
        raise ValueError(msg)


def _gate_authority(journal: dict[str, Any]) -> bool:
    try:
        _require_gate(journal)
    except FileNotFoundError:
        if journal["phase"] not in {"removed", "complete"}:
            msg = "The recovery gate is missing before finished cleanup"
            raise ValueError(msg) from None
        _finished_targets(journal)
        return False
    if journal["phase"] == "complete":
        _finished_targets(journal)
    return True


def _resume_finished(journal: dict[str, Any], path: Path) -> None:
    # A crash after gate unlink may leave phase=removed. This path never invokes uv or any unlink helper.
    managers_absent(journal)
    _finished_targets(journal)
    gate = Path(journal["gate"])
    try:
        gate.lstat()
    except FileNotFoundError:
        pass
    else:
        msg = "The installation recovery gate changed"
        raise ValueError(msg)
    sync(gate.parent)
    journal["phase"] = "complete"
    write(path, journal)


def _remove_remaining(journal: dict[str, Any], path: Path) -> None:
    _require_gate(journal)
    physical(Path(journal["prefix"]).parent, private=False)
    physical(Path(journal["environment"]["UV_TOOL_BIN_DIR"]), private=False)
    prefix = Path(journal["prefix"])
    if os.path.lexists(prefix):
        if (
            prefix.resolve(strict=True) != prefix
            or [prefix.stat().st_dev, prefix.stat().st_ino] != journal["prefix_identity"]
        ):
            msg = "The tool environment was replaced"
            raise ValueError(msg)
        remaining = manifest(prefix)
        if any(journal["manifest"].get(name) != row for name, row in remaining.items()):
            msg = "The remaining SDK changed; no removal was retried"
            raise ValueError(msg)
        uv = Path(journal["uv"])
        if (
            list(identity(uv.lstat())) != journal["uv_identity"]
            or hashlib.sha256(read(uv, 128 * 1024 * 1024)).hexdigest() != journal["uv_sha256"]
        ):
            msg = "The private remover changed"
            raise ValueError(msg)
        journal["phase"] = "removing"
        write(path, journal)
        _require_gate(journal)
        result = subprocess.run(  # noqa: S603 - authority-hashed private binary, pinned tool directories and fixed offline args
            [str(uv), "--offline", "--no-config", "--no-python-downloads", "tool", "uninstall", "heinzel"],
            env=journal["environment"],
            capture_output=True,
            check=False,
            timeout=900,
        )
        if result.returncode or os.path.lexists(prefix):
            msg = "Offline uv removal remains incomplete"
            raise ValueError(msg)
    _require_gate(journal)
    _finish_owned_links(journal)
    sync(prefix.parent)
    sync(Path(journal["environment"]["UV_TOOL_BIN_DIR"]))


def _finish_cleanup(journal: dict[str, Any], path: Path) -> None:
    _require_gate(journal)
    journal["phase"] = "removed"
    write(path, journal)
    managers_absent(journal)
    for row in journal["files"]:
        _require_gate(journal)
        _owned_unlink(row)
    if journal["platform"] == "linux":
        _manager(["/usr/bin/systemctl", "--user", "daemon-reload"], "linux")
    gate = Path(journal["gate"])
    _require_gate(journal)
    gate.unlink()
    sync(gate.parent)
    journal["phase"] = "complete"
    write(path, journal)


@contextmanager
def _locks(journal: dict[str, Any]) -> Generator[None, None, None]:
    descriptors: list[int] = []
    try:
        for name in journal["locks"]:
            path = Path(name)
            physical(path.parent)
            descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
                or [info.st_dev, info.st_ino] != journal["lock_identities"][name]
            ):
                os.close(descriptor)
                msg = "Recovery lock ownership changed"
                raise ValueError(msg)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(descriptor)
                raise
            descriptors.append(descriptor)
        yield
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def recover(directory: Path, expected: str = EXPECTED_AUTHORITY) -> dict[str, Any]:
    physical(directory)
    path = directory / "journal.json"
    journal: dict[str, Any] = json.loads(read(path))
    if (
        not expected
        or authority_digest(journal) != expected
        or journal["format"] != 1
        or journal["uid"] != os.getuid()
        or journal["directory"] != str(directory)
    ):
        msg = "Recovery identity does not match"
        raise ValueError(msg)
    python = Path(journal["python"])
    if Path(sys.executable).resolve(strict=True) != python or python_continuity(python) != journal["python_continuity"]:
        msg = "The original recovery interpreter changed; no removal was attempted"
        raise ValueError(msg)
    if journal["phase"] not in {"prepared", "stopped", "removing", "removed", "complete"}:
        msg = "Unknown removal phase; inspect its local recovery record"
        raise ValueError(msg)
    with _locks(journal):
        if not _gate_authority(journal):
            _resume_finished(journal, path)
            return journal
        managers_absent(journal)
        for row in journal["files"]:
            _verify_owned(row)
        _remove_remaining(journal, path)
        _finish_cleanup(journal, path)
    return journal


if __name__ == "__main__":
    try:
        recover(Path(__file__).resolve().parent, EXPECTED_AUTHORITY)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.stderr.write(
            "Heinzel offline recovery remains incomplete. Services were not started; user data was kept.\n"
        )
        sys.exit(1)
    sys.stdout.write("Heinzel uninstall recovery completed. User data, configuration, skills and notifier were kept.\n")
