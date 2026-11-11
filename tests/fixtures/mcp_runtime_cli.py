"""Instrument the real CLI: refuse outside configuration/network and record update-source attempts."""

import atexit
import json
import os
import sys
from pathlib import Path
from typing import TypedDict
from unittest.mock import patch


class Audit(TypedDict):
    """Only bounded fictional counters and configuration booleans, no secrets."""

    private: bool
    update_check: bool | None
    external: int
    updates: int


home = Path.home().resolve()
audit: Audit = {"private": False, "update_check": None, "external": 0, "updates": 0}


def save() -> None:
    """Keep the fictional observation inside this generation's private HOME."""
    (home / f".fixture-audit-{os.getpid()}.json").write_text(json.dumps(audit))


atexit.register(save)


def address(value: object) -> None:
    """Refuse public resolution/connections before they occur."""
    if value is not None and value not in ("127.0.0.1", "::1", "localhost"):
        audit["external"] += 1
        msg = "The fictional CLI cannot contact an external address"
        raise RuntimeError(msg)


def network(event: str, arguments: tuple[object, ...]) -> None:
    """Use the real interpreter's audit boundary, leaving Core's network implementation intact."""
    if event == "socket.getaddrinfo" and arguments:
        address(arguments[0])
    elif event == "socket.connect" and len(arguments) >= 2:
        target = arguments[1]
        if isinstance(target, tuple) and target:
            address(target[0])


sys.addaudithook(network)

from heinzel.heads.cli import main  # noqa: E402 - network guard precedes the real Core imports
from heinzel.kernel import ReleaseCache, Settings  # noqa: E402
from heinzel.kernel._config import Config, config_files  # noqa: E402

files = config_files(Path.cwd())
assert Path.cwd() == home
assert all(file.path.is_relative_to(home) for file in files), "No outside configuration may be read"
audit["private"] = True
audit["update_check"] = Config.load().settings(Settings, "hz").update_check
assert audit["update_check"] is False


def forbid_refresh(*_args: object, **_kwargs: object) -> None:
    """Fail if the actual update path attempts to contact a release source."""
    audit["updates"] += 1
    msg = "The fictional CLI cannot refresh a release source"
    raise RuntimeError(msg)


with patch.object(ReleaseCache, "refresh", side_effect=forbid_refresh):
    main()
