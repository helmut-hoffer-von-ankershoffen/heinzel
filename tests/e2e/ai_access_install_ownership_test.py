"""Browser proof for the client-install owner, using a fictional isolated server."""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e.web_ui_test import environment, web_build  # noqa: F401 - pytest fixture

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(300)
def test_client_install_survives_dialog_close_and_reopen(environment, tmp_path):  # noqa: F811 - imported fixture
    """A delayed synthetic install stays owned across Close, Escape and backdrop."""
    fixture_env, _ = environment
    env = {key: value for key, value in fixture_env.items() if key.startswith("HZ_") or key in {"PATH", "PYTHONPATH"}}
    env["HOME"] = str(tmp_path)
    env["BROWSER"] = "/usr/bin/false"
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(Path.home() / "Library" / "Caches" / "ms-playwright")
    web_build.ensure_built()
    server = subprocess.Popen(
        [sys.executable, "-m", "heinzel", "ui", "serve", "--port", "0", "--no-browser"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert server.stdout is not None
        url = server.stdout.readline().strip()
        assert url.startswith("http://127.0.0.1:")
        result = subprocess.run(
            [sys.executable, "scripts/web_build.py", "exec", "node", "e2e/ai-access-install-ownership.mjs", url],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "three dismissal paths passed" in result.stdout
    finally:
        server.terminate()
        server.wait(timeout=30)
        for stream in (server.stdout, server.stderr):
            if stream:
                stream.close()
