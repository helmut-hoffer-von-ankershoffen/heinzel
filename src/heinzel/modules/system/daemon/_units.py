"""The launchd LaunchAgent and the systemd user units that run `hz system daemon tick` (ADR 0020).

Both fire on wall-clock slots, not after an interval, because launchd's `StartCalendarInterval` and systemd's
`OnCalendar=` with `Persistent=true` catch up missed slots, coalesced, after sleep; `StartInterval` does not.
"""

import plistlib
import shlex
from pathlib import Path

LABEL = "io.github.helmut-hoffer-von-ankershoffen.heinzel.daemon"
"""launchd label, reverse-DNS from the repository's home."""
SYSTEMD_NAME = "hz-daemon"
INTERVALS = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60)
"""Minutes that divide an hour, so slots fall at the same minutes every hour on both platforms."""
TICK = ("system", "daemon", "tick")
NICE = 10
TICK_TIMEOUT = "30min"
"""systemd stops a tick that runs longer; every job has its own, shorter timeout."""
RANDOMIZED_DELAY = "60"
"""Seconds systemd spreads ticks over, so many machines don't hit the same services at once."""
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
"""What launchd jobs find on PATH besides the shim's directory; launchd starts them with an almost empty one."""
REPOSITORY = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel"


def check_interval(minutes: int) -> None:
    """Accept only intervals that divide an hour.

    Raises:
        ValueError: Naming the intervals that work.
    """
    if minutes not in INTERVALS:
        allowed = ", ".join(map(str, INTERVALS))
        msg = f"The interval must divide an hour evenly; use one of {allowed} minutes, not {minutes}."
        raise ValueError(msg)


def plist_path(unit_dir: Path) -> Path:
    """Where the LaunchAgent lives."""
    return unit_dir / f"{LABEL}.plist"


def systemd_paths(unit_dir: Path) -> tuple[Path, Path]:
    """Where the service and the timer live."""
    return unit_dir / f"{SYSTEMD_NAME}.service", unit_dir / f"{SYSTEMD_NAME}.timer"


NOTIFIER_BUNDLE_ID = "dev.heinzel.notifier"
"""Bundle id of Heinzel Notifier.app (notify/macos/Info.plist), whose icon and name Login Items shows."""

NOTIFIER_APP = "Heinzel Notifier.app"
"""Where the app is installed, under ~/Applications."""

LOGIN_ITEM_HINT = "Install the notifier to show Heinzelmännchen with its icon in Login Items: hz system notify install"
"""Said by `install` while the app is missing; the plist already names it, so installing it later is enough."""


def launchd_plist(executable: Path, minutes: int, log_dir: Path) -> str:
    """The LaunchAgent: a tick at every `minutes`-minute slot, in the background, at low priority."""
    check_interval(minutes)
    agent = {
        "Label": LABEL,
        # macOS 13+ attributes the Login Items entry to this app, so it shows its icon and name, not a bare "hz".
        "AssociatedBundleIdentifiers": [NOTIFIER_BUNDLE_ID],
        "ProgramArguments": [str(executable), *TICK],
        "StartCalendarInterval": [{"Minute": minute} for minute in range(0, 60, minutes)],
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": NICE,
        "RunAtLoad": False,
        # The tick logs to its own rotated JSON-lines file. Its report on stdout goes nowhere and stderr catches only
        # crashes, because launchd never rotates these files.
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": str(log_dir / "launchd.err.log"),
        "EnvironmentVariables": {"PATH": f"{executable.parent}:{PATH}"},
    }
    return plistlib.dumps(agent, sort_keys=True).decode()


def on_calendar(minutes: int) -> str:
    """Systemd's calendar expression for every `minutes`-minute slot."""
    check_interval(minutes)
    return "*:00" if minutes == 60 else f"*:0/{minutes}"  # noqa: PLR2004 - an hour


def systemd_service(executable: Path) -> str:
    """The oneshot service that runs one tick."""
    command = shlex.join([str(executable), *TICK]).replace("%", "%%")  # systemd expands % specifiers
    return (
        "[Unit]\n"
        "Description=Heinzelmännchen daemon tick\n"
        f"Documentation={REPOSITORY}/blob/main/docs/adr/0020-daemon-head.md\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={command}\n"
        "StandardOutput=null\n"
        f"Nice={NICE}\n"
        "IOSchedulingClass=idle\n"
        f"TimeoutStartSec={TICK_TIMEOUT}\n"
    )


def systemd_timer(minutes: int) -> str:
    """The timer that starts the service at every slot and catches up after sleep or downtime."""
    return (
        "[Unit]\n"
        f"Description=Run the Heinzelmännchen daemon tick every {minutes} minutes\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar={on_calendar(minutes)}\n"
        "Persistent=true\n"
        f"RandomizedDelaySec={RANDOMIZED_DELAY}\n"
        f"Unit={SYSTEMD_NAME}.service\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
