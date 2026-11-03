"""Exercise the actual compiled daemon registry from the mounted browser test."""

import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

from heinzel import CurrentUser
from heinzel.kernel import CompiledJobs
from heinzel.modules.system.daemon import DaemonService, DaemonSettings


def _wait_for_slot(cadence_seconds, now, wait):
    """Keep the real-clock test turn away from the host's intended expiry boundary."""
    remaining = cadence_seconds - now().timestamp() % cadence_seconds
    if remaining < 10:
        wait(remaining + 0.1)


def _publish_json(directory, name, value):
    """Expose a complete coordination document with one same-directory rename."""
    descriptor, path = tempfile.mkstemp(prefix=".daemon-publish-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
        Path(path).replace(directory / name)
    finally:
        Path(path).unlink(missing_ok=True)


def start_daemon_probe(kernel, directory, *, cadence_seconds=420):
    result = {"failure": None, "deduplicated": False}

    def run():
        try:
            signal = directory / "daemon-start.json"
            ready = threading.Event()
            for _ in range(1200):
                if signal.exists():
                    break
                ready.wait(0.05)
            data = json.loads(signal.read_text())
            session = data["session"]
            current = kernel.resolve(CurrentUser)
            with current.local("daemon"):
                jobs = kernel.resolve(CompiledJobs).jobs
                job = next(job for job in jobs if job.name.startswith("agent-" + session.lower()))
                _wait_for_slot(cadence_seconds, lambda: datetime.now(UTC), ready.wait)
                now = datetime.now(UTC)
                daemon = DaemonService(kernel, kernel.settings, DaemonSettings(), clock=lambda: now, zone=UTC)
                registered = [item for item in daemon._jobs() if item.id == "heinzel:" + job.name]
                daemon._jobs = lambda: registered
                daemon._post_notices = lambda _log: None
                report = daemon.tick()
                rendered = any(
                    item.job == "heinzel:" + job.name and item.disposition == "rendered" for item in report.runs
                )
                if rendered:
                    calls = (directory / "calls").read_text()
                    restarted = DaemonService(kernel, kernel.settings, DaemonSettings(), clock=lambda: now, zone=UTC)
                    restarted._jobs = lambda: registered
                    restarted._post_notices = lambda _log: None
                    restarted.tick()
                    result["deduplicated"] = (directory / "calls").read_text() == calls
                else:
                    result["failure"] = str(report)
        except Exception as error:  # noqa: BLE001 - test-only output contains no account secrets
            result["failure"] = str(error)
        finally:
            _publish_json(
                directory,
                "daemon-finished.json",
                {
                    "ok": result["failure"] is None,
                    "deduplicated": result["deduplicated"],
                    "failure": result["failure"],
                },
            )

    thread = threading.Thread(target=run, daemon=True)
    result["thread"] = thread
    thread.start()
    return result
