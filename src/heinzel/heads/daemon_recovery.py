"""Explicit candidate-only legacy daemon retirement, independent of the installed old CLI."""

import argparse
import os
import stat
import sys
from contextlib import ExitStack
from pathlib import Path

from heinzel import Config, HeinzelError, Progress, Settings
from heinzel.heads import install
from heinzel.heads._tool_install import resolve_target
from heinzel.kernel import (
    DaemonLegacyRecovery,
    DaemonRecoveryPlan,
    DaemonRecoveryResult,
    DaemonRecoverySelection,
    Installation,
    boot,
)

_RECEIPT_LIMIT = 16 * 1024


def _receipt_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _check_receipt_directory(descriptor: int, *, selected: bool = False) -> None:
    info = os.fstat(descriptor)
    sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
    if (
        info.st_uid not in {0, os.getuid()}
        or (info.st_mode & 0o022 and not sticky_root)
        or (selected and (info.st_uid != os.getuid() or info.st_mode & 0o022))
    ):
        msg = "The standard receipt directory is unsafe."
        raise HeinzelError(msg)


def _standard_receipt(data: Path) -> bytes:
    """Read the historical installer's 0600 receipt beneath its ordinary data directory.

    Published installs may have a 0755 data directory. This read-only compatibility
    boundary permits search/read bits on directories, never on the receipt itself.
    Every directory is opened without links and retained until identity revalidation.

    Raises:
        HeinzelError: If ownership, permissions, bounds or identities are unsafe.
        OSError: If the physical receipt cannot be opened.
    """
    absolute = data.absolute()
    if ".." in absolute.parts:
        msg = "The standard receipt path is not physical."
        raise HeinzelError(msg)
    with ExitStack() as stack:
        directory = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, directory)
        parents: list[tuple[int, int, str]] = []
        for name in absolute.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            stack.callback(os.close, child)
            parents.append((child, directory, name))
            directory = child
        for child, _, _ in parents:
            _check_receipt_directory(child)
        _check_receipt_directory(directory, selected=True)
        descriptor = os.open("standard-app.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_mode & 0o077
                or before.st_nlink != 1
                or before.st_size > _RECEIPT_LIMIT
            ):
                msg = "The standard receipt must be an owned private bounded regular file."
                raise HeinzelError(msg)
            content = handle.read(_RECEIPT_LIMIT + 1)
            entry = os.stat("standard-app.json", dir_fd=directory, follow_symlinks=False)
            if (
                len(content) > _RECEIPT_LIMIT
                or _receipt_identity(before) != _receipt_identity(os.fstat(handle.fileno()))
                or _receipt_identity(before) != _receipt_identity(entry)
            ):
                msg = "The standard receipt changed during inspection."
                raise HeinzelError(msg)
        for child, parent, name in parents:
            info = os.fstat(child)
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _receipt_identity(info) != _receipt_identity(entry):
                msg = "The standard receipt directory changed during inspection."
                raise HeinzelError(msg)
            _check_receipt_directory(child, selected=child == directory)
        return content


def recovery() -> DaemonLegacyRecovery:
    """Bind the independently verified persistent standard target before resolving recovery.

    Raises:
        HeinzelError: If the current standard target or continuity is unknown.
    """
    uv = install.uv_executable()
    target = resolve_target(uv, create=False)
    config = Config.load(Path.home())
    settings = config.settings(Settings, "hz")
    receipt = settings.data_dir / "standard-app.json"
    content = _standard_receipt(settings.data_dir)
    profile = install.AppProfile.model_validate_json(content)
    installation = Installation(Progress(), prefix=target.prefix, uv=uv)
    installation.verify_tool_destination(target.store, target.binary)
    if profile.format != install.PROFILE_FORMAT or not installation.startup_state_matches():
        msg = "The persistent standard app cannot be verified; no legacy recovery was authorized."
        raise HeinzelError(msg)
    kernel = boot(settings.model_copy(update={"plugins_enabled": False}))
    kernel.bind(Installation, installation)
    kernel.bind(DaemonRecoverySelection, DaemonRecoverySelection(receipt, content))
    return kernel.resolve(DaemonLegacyRecovery)


def main(argv: list[str] | None = None) -> int:
    """Parse only explicit recovery commands; help and malformed input never resolve authority."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="Inspect exact legacy integration without changing it.")
    retire = commands.add_parser("retire", help="Retire only the exact digest shown by plan; preserve data.")
    retire.add_argument("--confirm", required=True, metavar="PLAN_SHA256")
    restore = commands.add_parser("restore", help="Plan or confirm restoration of an interrupted retirement.")
    restore.add_argument("transaction", metavar="RETIREMENT_SHA256")
    restore.add_argument("--confirm", metavar="RESTORE_PLAN_SHA256")
    args = parser.parse_args(argv)
    try:
        service = recovery()
        result: DaemonRecoveryPlan | DaemonRecoveryResult
        if args.command == "plan":
            result = service.plan()
        elif args.command == "retire":
            result = service.retire(args.confirm)
        elif args.confirm is None:
            result = service.restore_plan(args.transaction)
        else:
            result = service.restore(args.transaction, args.confirm)
        sys.stdout.write(result.model_dump_json(indent=2) + "\n")
    except (HeinzelError, OSError, ValueError, TypeError, KeyError):
        sys.stderr.write(
            "Legacy daemon recovery refused: authority is unknown, changed or requires explicit recovery.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
