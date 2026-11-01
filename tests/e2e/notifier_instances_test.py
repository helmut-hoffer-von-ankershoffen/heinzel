"""Real Heinzel Notifier processes on macOS: each one ends by itself, and a leftover can be found and ended.

Runs the built app (`make notifier`) through LaunchServices as hz does; skipped where there is none, so CI's macOS job,
which builds it, is where it counts. Nothing is posted: `--status` reads the notification settings and needs no
permission, and it starts a process exactly as a notification does.
"""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from heinzel.modules.system.notify._macos import BUNDLED, REAL_ENV, NotifierApp, tree_digest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(sys.platform != "darwin", reason="Heinzel Notifier is a macOS app"),
    pytest.mark.skipif(tree_digest(BUNDLED) is None, reason="the app isn't built: make notifier"),
]

LAUNCHES = 15


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(REAL_ENV, "1")  # this test, and only this one, may start the real app
    app = NotifierApp(tmp_path / "home", bundled=BUNDLED)
    assert app.ensure().state in {"installed", "current"}
    yield app
    app.forget()  # macOS would otherwise list this temporary copy in System Settings > Notifications for good


def test_no_process_is_left_after_many_launches(app):
    before = len(app.instances())
    answers = [app.status() for _ in range(LAUNCHES)]
    assert all(answer in {"authorized", "denied", "not-determined", "provisional"} for answer in answers), answers
    deadline = time.monotonic() + 15
    while len(app.instances()) > before and time.monotonic() < deadline:
        time.sleep(0.5)
    assert len(app.instances()) == before  # N launches, none still running: the count didn't grow


def test_a_process_that_lingers_is_found_by_its_bundle_id_and_ended(app):
    subprocess.run(["/usr/bin/open", "-n", "-g", str(app.path)], check=True, timeout=10)  # as a click starts it
    deadline = time.monotonic() + 10
    while not app.instances() and time.monotonic() < deadline:
        time.sleep(0.2)
    running = app.instances()
    assert len(running) == 1
    assert running[0].label.startswith("application.dev.heinzel.notifier.")
    assert app.clean() == 0  # seconds old: a notification may be posting, it is left alone
    time.sleep(1.5)  # old enough for older_than=0
    assert app.clean(older_than=0) == 1
    deadline = time.monotonic() + 5
    while app.instances() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert app.instances() == []
