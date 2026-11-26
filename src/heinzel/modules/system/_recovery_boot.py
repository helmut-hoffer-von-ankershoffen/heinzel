"""Stdlib-only launcher code for authenticating a staged recovery environment."""

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

NAME = "supervised-recovery"
MAX_BYTES = 2_000_000_000
MAX_FILES = 100_000
_PROOF_LIMIT = 8192
_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def path_absent(path: Path) -> bool:
    """Require a missing entry, rejecting a dangling link.

    Raises:
        OSError: If the entry cannot be inspected.
    """
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def owned_path(path: Path, *, private: bool = False) -> os.stat_result:
    """Accept a physical path controlled by this user or the OS.

    Raises:
        ValueError: If the observed path or a parent is unsafe.
    """
    if not path.is_absolute() or path.resolve(strict=True) != path:
        msg = "The recovery path is not physical."
        raise ValueError(msg)
    for parent in path.parents:
        info = parent.lstat()
        sticky_root = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if info.st_uid not in {0, os.getuid()} or (info.st_mode & 0o022 and not sticky_root):
            msg = "The recovery parent is writable by other users."
            raise ValueError(msg)
    info = path.lstat()
    if info.st_uid not in {0, os.getuid()} or info.st_mode & 0o022:
        msg = "The recovery path is writable by other users."
        raise ValueError(msg)
    if private and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        msg = "The recovery path is not private."
        raise ValueError(msg)
    return info


def _read_hash(path: Path, remaining: int) -> tuple[int, bytes]:
    with os.fdopen(os.open(path, _FLAGS), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > remaining:
            msg = "An unsafe or oversized recovery file was found."
            raise ValueError(msg)
        digest = hashlib.file_digest(stream, "sha256").digest()
        if _identity(os.fstat(stream.fileno())) != _identity(info) or _identity(path.lstat()) != _identity(info):
            msg = "A recovery file changed while it was checked."
            raise ValueError(msg)
        return info.st_size, digest


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def file_digest(path: Path) -> str:
    """SHA-256 of a bounded physical file under the same trust rules as the launcher."""
    owned_path(path)
    return _read_hash(path, MAX_BYTES)[1].hex()


def _raise(error: OSError) -> None:
    raise error


def seal_tree(directory: Path) -> tuple[int, str]:
    """Read an owned, bounded directory with no links or bytecode.

    Raises:
        ValueError: If any entry does not meet the recovery trust rules.
    """
    owned_path(directory, private=True)
    count = size = 0
    digest = hashlib.sha256()
    for root, directories, files in os.walk(directory, onerror=_raise):
        directories.sort()
        entries = sorted([*directories, *files])
        for name in entries:
            path = Path(root, name)
            info = path.lstat()
            count += 1
            if (
                count > MAX_FILES
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
                or name.endswith((".pyc", ".pyo"))
                or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
            ):
                msg = "An unsafe entry was found in the recovery environment."
                raise ValueError(msg)
            digest.update(os.fsencode(path.relative_to(directory)) + b"\0")
            digest.update(stat.S_IMODE(info.st_mode).to_bytes(2, "big"))
            if stat.S_ISDIR(info.st_mode):
                digest.update(b"d")
            else:
                length, checksum = _read_hash(path, MAX_BYTES - size)
                size += length
                digest.update(b"f" + checksum)
    return size, digest.hexdigest()


def _proof(path: Path) -> tuple[str, str, str, str]:
    owned_path(path, private=True)
    with os.fdopen(os.open(path, _FLAGS), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > _PROOF_LIMIT:
            msg = "The recovery proof is unsafe."
            raise ValueError(msg)
        python, executable_digest, seal, content = json.loads(stream.read(_PROOF_LIMIT + 1))
    if not isinstance(python, str) or not all(
        isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
        for digest in (executable_digest, seal, content)
    ):
        msg = "The recovery proof is invalid."
        raise ValueError(msg)
    return python, executable_digest, seal, content


_WORKER = (
    "import sys; site, *target = sys.argv[1:]; sys.argv[1:] = target; sys.path.append(site); "
    "from heinzel.modules.system._auto_recovery_worker import main; sys.exit(main())"
)


def main() -> int:
    """Execute only a sealed environment outside the target SDK."""
    prefix, state = map(Path, sys.argv[1:])
    staged = state / NAME
    try:
        python, executable_digest, seal, content = _proof(staged / "proof.json")
        executable = Path(python)
        if executable.is_relative_to(prefix) or owned_path(executable).st_mode & 0o111 == 0:
            return 75
        library = staged / "site"
        if file_digest(executable) != executable_digest or seal_tree(library)[1] != seal:
            return 75
        os.execve(  # noqa: S606 - exact isolated interpreter and verified arguments
            executable,
            [python, "-I", "-S", "-B", "-c", _WORKER, str(library), str(prefix), str(state), content],
            {"HOME": os.environ["HOME"]},
        )
    except (OSError, ValueError, KeyError, TypeError):
        return 75


if __name__ == "__main__":
    sys.exit(main())
