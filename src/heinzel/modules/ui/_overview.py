"""Fast local web server observation for the interactive CLI."""

import sys
import time
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from heinzel.kernel import Config, HeinzelError, LocalObservation, Settings, WebOverview, private_read
from heinzel.modules.system.daemon._overview import loaded
from heinzel.modules.ui._service import LABEL, UNIT

MAX_PORT = 65535


def _port(state_dir: Path) -> int | None:
    record = TypeAdapter(dict[str, JsonValue]).validate_json(private_read(state_dir / "ui/service.json"))
    port = record.get("port")
    return port if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= MAX_PORT else None


def observe(state_dir: Path, platform: str = sys.platform, *, deadline: float | None = None) -> LocalObservation:
    """Read the installed web service record and ask the service manager once, within the deadline."""
    if platform not in ("darwin", "linux"):
        return LocalObservation("unknown", "this system is not supported")
    try:
        port = _port(state_dir)
    except FileNotFoundError:
        return LocalObservation("not installed", "run `hz ui install` to keep it running")
    except (OSError, ValueError, HeinzelError):
        return LocalObservation("unknown", "run `hz ui status` for details")
    where = f"port {port}" if port else ""
    state = loaded(platform, deadline if deadline is not None else time.monotonic() + 0.2, LABEL, UNIT)
    if state is None:
        return LocalObservation("unknown", "run `hz ui status` for details")
    return LocalObservation("running" if state else "stopped", where)


class WebOverviewService(WebOverview):
    """Observe only the built-in web service using user-scope settings."""

    def __init__(self) -> None:
        settings = Config.load(Path.home()).settings(Settings, "hz")
        super().__init__(lambda deadline: observe(settings.state_dir, deadline=deadline))
