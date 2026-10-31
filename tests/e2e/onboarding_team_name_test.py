"""Mounted first-run setup: a team name typed in the Team step is kept by hz and still shown after a reload."""

import json
import os
import sys
import threading
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

from heinzel import Config, Settings
from heinzel.heads import web
from tests.e2e._browser_result import check_browser, require_browser, wait_browser
from tests.e2e.docs_test import GuardedCommand
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, web_build

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("viewport", ["phone", "desktop"])
def test_mounted_setup_keeps_the_team_name_after_a_reload(make_kernel, tmp_path, monkeypatch, engine, viewport):
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

    class RecordedServer(uvicorn.Server):
        instances: ClassVar[list[uvicorn.Server]] = []

        def __init__(self, config):
            super().__init__(config)
            self.instances.append(self)

    monkeypatch.setattr(uvicorn, "Server", RecordedServer)
    ready = threading.Event()
    urls = []

    def announce(value):
        urls.append(value)
        ready.set()

    thread = threading.Thread(target=lambda: web.serve(kernel, port=0, browser=False, announce=announce), daemon=True)
    thread.start()
    try:
        assert ready.wait(15)
        token = parse_qs(urlsplit(urls[0]).fragment).get("token", [""])[0]
        assert token
        command = [
            sys.executable,
            str(ROOT / "scripts/web_build.py"),
            "exec",
            "node",
            "e2e/onboarding-team-name.mjs",
            urls[0],
            engine,
            viewport,
        ]
        guarded = GuardedCommand(command, cwd=ROOT, env=os.environ.copy())
        try:
            returncode = require_browser(wait_browser(guarded, 240))
        finally:
            guarded.stop()
        stdout, stderr = guarded.capture.text("stdout"), guarded.capture.text("stderr")
        check_browser(returncode, (stderr + "\n" + stdout).replace(token, "<redacted-session>"))
        assert json.loads(stdout.strip().splitlines()[-1])["team"] == "Payments"
        saved = Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml"  # the isolated user configuration
        assert 'name = "Payments"' in saved.read_text(encoding="utf-8")
    finally:
        if RecordedServer.instances:
            RecordedServer.instances[0].should_exit = True
        thread.join(15)
        assert not thread.is_alive()
