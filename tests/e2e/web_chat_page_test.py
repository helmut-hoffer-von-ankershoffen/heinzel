"""Mounted Heinzel's page: open it from More, rename with Undo, pause at once and resume only on confirmation."""

import json
import os
import sys
from datetime import UTC
from pathlib import Path

import pytest

from heinzel import Config, Settings
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._service import DaemonService
from tests.e2e._browser_result import check_browser, require_browser, wait_browser
from tests.e2e._mounted import MountedServer
from tests.e2e.docs_test import GuardedCommand
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, web_build

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(600)
def test_the_heinzel_page_renames_with_undo_and_resumes_only_on_confirmation(make_kernel, tmp_path, monkeypatch):
    web_build.ensure_built()
    for name, folder in {
        "HOME": "home",
        "XDG_CONFIG_HOME": "config",
        "XDG_DATA_HOME": "xdg-data",
        "XDG_STATE_HOME": "xdg-state",
        "XDG_CACHE_HOME": "xdg-cache",
    }.items():
        monkeypatch.setenv(name, str(tmp_path / folder))
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE))
    settings = Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
    )
    kernel = make_kernel(builtins=True, settings_=settings, config=Config.load(tmp_path / "home"))
    # An isolated daemon admits the check-in the page pauses and resumes; it never starts a process.
    kernel.bind(DaemonService, DaemonService(kernel, settings, DaemonSettings(), platform="isolated", zone=UTC))

    shots = os.environ.get("CHAT_PAGE_SHOTS", "")
    with MountedServer(kernel, monkeypatch) as server:
        node = [sys.executable, str(ROOT / "scripts/web_build.py"), "exec", "node", "e2e/chat-page.mjs", server.url]
        guarded = GuardedCommand([*node, *([shots] if shots else [])], cwd=ROOT, env=os.environ.copy())
        try:
            returncode = require_browser(wait_browser(guarded, 240))
        finally:
            guarded.stop()
        stdout, stderr = guarded.capture.text("stdout"), guarded.capture.text("stderr")
        check_browser(returncode, (stderr + "\n" + stdout).replace(server.token, "<redacted-session>"))
        assert json.loads(stdout.strip().splitlines()[-1])["chatPage"] == "verified"
