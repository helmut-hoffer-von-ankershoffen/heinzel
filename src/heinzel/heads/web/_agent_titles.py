"""Read the name of the Claude session running in a terminal's foreground process group."""

import json
import os
from pathlib import Path

import psutil


def claude_title(main: int) -> str | None:
    """Return a live foreground Claude session's name, or None when it cannot be identified."""
    try:
        pid = os.tcgetpgrp(main)
        process = psutil.Process(pid)
        command = process.cmdline()
        if not command or Path(command[0]).name not in {"claude", "claude-code"}:
            return None
        config = Path(process.environ().get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        with (config / "sessions" / f"{pid}.json").open(encoding="utf-8") as source:
            metadata = json.loads(source.read(16_385))
        started = metadata.get("startedAt")
        if (
            metadata.get("pid") != pid
            or not isinstance(metadata.get("sessionId"), str)
            or not isinstance(started, int | float)
            or started / 1000 < process.create_time() - 1
        ):
            return None
        name = metadata.get("name")
        if not isinstance(name, str):
            return None
        return "".join(character for character in name if character.isprintable()).strip()[:192] or None
    except (OSError, ValueError, TypeError, AttributeError, psutil.Error):
        return None
