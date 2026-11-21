"""Browser-driven installed-server restart keeps the selected profile and page."""

import json
import subprocess

import pytest

from heinzel.testing import scaled
from tests.e2e.tool_install_test import sandbox, seed_uv_cache, wheel
from tests.e2e.ui_service_test import supervised
from tests.e2e.web_ui_test import REPO, _browser_ready
from tests.integration.ui_service_test import service, service_factory

__all__ = ["sandbox", "seed_uv_cache", "service", "service_factory", "supervised", "wheel"]

pytestmark = pytest.mark.e2e


@pytest.fixture
def supervised_install_options():
    """Compile the private tool at install time for the browser restart journey."""
    # Keep first-import compilation out of this concurrent browser journey. The separate
    # cold-publication cases still exercise uv's default installation and the same deadline.
    return ("--compile-bytecode",)


@pytest.mark.timeout(900)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_installed_server_restarts_from_settings_and_reconnects_the_selected_profile(supervised, tmp_path, engine):
    service, running = supervised
    run = _browser_ready(engine)
    process = running.launch()
    first = running.ready()
    driven = subprocess.run(
        [*run, "exec", "node", "e2e/web-server-restart.mjs", engine, str(tmp_path / "screenshots")],
        input=service.open_local(),
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=scaled(200),
    )
    assert driven.returncode == 0, driven.stderr
    assert json.loads(driven.stdout.strip().splitlines()[-1]) == {
        "replaced": True,
        "profilePreserved": True,
        "pagePreserved": True,
        "restarts": 1,
    }
    second = running.ready(first.instance)
    assert second.pid == first.pid == process.pid
    assert second.port == first.port
