"""Retire the private `hz ui serve --port 0` servers older desktop apps left behind (one backend per machine).

Before 0.9.54 both desktop apps started their own server instead of attaching to the owned web service, and a killed
app left that server running with launchd (pid 1) as its parent. Each such server runs its own auto-updater on old
code. A candidate is exactly this user's process whose program lies in the owned uv tool and whose arguments are
exactly the old app's spawn; the supervised server, dev checkouts and other users never match. Only orphans are
stopped (adopted by pid 1, `launchd` or `systemd --user`, or with no parent left), with SIGINT and no SIGKILL, each
re-checked just before its signal. A server whose app still runs goes when the updater reopens that app.
"""

import os
import signal
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from heinzel._adapter_system import SYSTEM_CLOCK, SYSTEM_RUNNER

APP_SPAWN = ("ui", "serve", "--port", "0", "--no-browser")
"""The exact arguments both desktop apps used to start their private server."""
_PYTHON_FLAGS = frozenset({"-I", "-E", "-s", "-S", "-B", "-u", "-P"})
STOP_SECONDS = 8.0
POLL_SECONDS = 0.1
Lister = Callable[[], str]
Signaller = Callable[[int, int], None]


def _ps() -> str:
    # A fixed argument vector of the platform's own ps; nothing from the process list reaches a shell.
    command = ["/bin/ps", "-axww", "-o", "pid=,ppid=,uid=,command="]
    result = SYSTEM_RUNNER.run(command, timeout=10)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    return result.stdout


@dataclass(frozen=True, slots=True)
class Stray:
    """One leftover app-spawned server: its pid, its full command line, and whether its app is gone."""

    pid: int
    ppid: int
    command: str
    orphaned: bool
    """Its app is gone: pid 1, a `systemd --user` or `launchd` manager, or nothing adopted it."""


def _adopter(command: str) -> bool:
    """Whether a parent is a service manager that adopts orphans: Linux reparents to `systemd --user`, a subreaper."""
    words = command.split()
    name = Path(words[0]).name if words else ""
    return name == "launchd" or (name == "systemd" and "--user" in words[1:])


def _app_spawn(arguments: Sequence[str]) -> bool:
    """Whether everything after the program is the old app's spawn, via `[-I] -m heinzel` or an `hz` script."""
    rest = list(arguments)
    while rest and rest[0] in _PYTHON_FLAGS:
        rest.pop(0)
    if rest[:2] == ["-m", "heinzel"]:
        rest = rest[2:]
    elif rest and Path(rest[0]).name == "hz":
        rest = rest[1:]
    return tuple(rest) == APP_SPAWN


def _table(listing: Lister) -> dict[int, tuple[int, int, list[str]]]:
    """Pid -> (ppid, uid, command words); a line that isn't `PID PPID UID COMMAND...` is skipped.

    Words are split on whitespace, so a tool prefix containing a space never matches: a safe miss, never a wrong hit.
    """
    table: dict[int, tuple[int, int, list[str]]] = {}
    for line in listing().splitlines():
        fields = line.split()
        if len(fields) < 4 or not all(value.isdigit() for value in fields[:3]):  # noqa: PLR2004 - pid, ppid, uid
            continue
        table[int(fields[0])] = (int(fields[1]), int(fields[2]), fields[3:])
    return table


def strays(prefix: Path, *, uid: int | None = None, own: int | None = None, listing: Lister = _ps) -> list[Stray]:
    """This user's app-spawned servers whose program is inside the owned uv tool at `prefix`."""
    uid = os.getuid() if uid is None else uid
    own = os.getpid() if own is None else own
    root = str(prefix).rstrip("/") + "/"
    table = _table(listing)
    found: list[Stray] = []
    for pid, (ppid, owner, words) in table.items():
        program, arguments = words[0], words[1:]
        if owner != uid or pid in {own, 1} or not program.startswith(root) or not _app_spawn(arguments):
            continue
        parent = table.get(ppid)
        orphaned = ppid == 1 or parent is None or _adopter(" ".join(parent[2]))
        found.append(Stray(pid, ppid, " ".join(words), orphaned))
    return found


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def retire_strays(  # noqa: PLR0913 - isolated process seams
    prefix: Path,
    *,
    uid: int | None = None,
    listing: Lister = _ps,
    send: Signaller = os.kill,
    alive: Callable[[int], bool] = _alive,
    clock: Callable[[], float] = SYSTEM_CLOCK.monotonic,
    sleep: Callable[[float], None] = SYSTEM_CLOCK.sleep,
) -> tuple[list[int], list[int]]:
    """Stop orphaned app-spawned servers with SIGINT and wait for them; return (stopped, survivors).

    Safe to repeat: a second run finds nothing. A server whose app still runs is left alone; a survivor of the
    bounded wait is reported, never killed.
    """
    try:
        orphans = [(stray.pid, stray.command) for stray in strays(prefix, uid=uid, listing=listing) if stray.orphaned]
    except (OSError, subprocess.SubprocessError):
        return [], []
    signalled: list[int] = []
    for pid, command in orphans:
        # Re-read just before the signal: only the same orphaned process, never a pid reused since the first look.
        try:
            now = {stray.pid: stray for stray in strays(prefix, uid=uid, listing=listing)}
        except (OSError, subprocess.SubprocessError):
            break
        current = now.get(pid)
        if current is None or not current.orphaned or current.command != command:
            continue
        try:
            send(pid, signal.SIGINT)
        except ProcessLookupError:
            continue
        except OSError:
            continue
        signalled.append(pid)
    deadline = clock() + STOP_SECONDS
    while any(alive(pid) for pid in signalled) and clock() < deadline:
        sleep(POLL_SECONDS)
    survivors = [pid for pid in signalled if alive(pid)]
    return [pid for pid in signalled if pid not in survivors], survivors
