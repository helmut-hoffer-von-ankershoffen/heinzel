"""SessionEnd hook: queue the session's transcript for skill `self-audit` to mine; never fails the session."""

import contextlib
import json
import sys
from pathlib import Path

with contextlib.suppress(Exception):  # a hook that only queues must never end a session with an error
    transcript = json.load(sys.stdin)["transcript_path"]
    queue = Path(__file__).absolute().parents[1] / "audit" / "inbox" / "queue"
    queue.parent.mkdir(parents=True, exist_ok=True)
    with queue.open("a", encoding="utf-8") as f:
        f.write(f"{transcript}\n")
