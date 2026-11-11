"""The mounted Settings dialog shares the real private MCP operations, with an isolated host manager."""

import os
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from tests.e2e._browser_result import check_browser, require_browser, wait_browser
from tests.e2e._mounted import MountedServer
from tests.e2e.docs_test import GuardedCommand
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, web_build
from tests.fixtures import mcp_managed

managed = mcp_managed.managed
pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_mounted_managed_mcp_consent_readiness_and_owned_stop(managed, tmp_path, monkeypatch, engine):
    assert not entry_points(group="heinzel.modules"), "The lifecycle fixture runs with Core only."
    web_build.ensure_built()
    _, manager, calls, kernel = managed
    home = tmp_path / "browser-home"
    home.mkdir()
    env = {
        "HOME": str(home),
        "TMPDIR": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE)),
    }

    with MountedServer(kernel(), monkeypatch) as server:
        guarded = GuardedCommand(
            [str(web_build.node_bin() / "node"), "e2e/mcp-shared-lifecycle.mjs", server.url, engine],
            cwd=ROOT / "web",
            env=env,
        )
        try:
            status = require_browser(wait_browser(guarded, 80))
        finally:
            guarded.stop()
        output = guarded.capture.text("stderr") + "\n" + guarded.capture.text("stdout")
        check_browser(status, output.replace(server.token, "<redacted-session>"))

    assert [args[1] for args in manager.calls if args[1] in {"bootstrap", "bootout"}] == ["bootstrap"]
    assert manager.process.poll() == 0
    assert calls.count("mcp-token") == 1
    assert "mcp-check" in calls
