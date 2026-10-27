"""The mounted sharing control hot-applies policy against an isolated fake vendor."""

import json
import os
import re
import select
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]
ORIGIN = "https://workstation.tail-example.ts.net"
OTHER = {"svc:other": {"TCP": {"3000": {"TCPForward": "127.0.0.1:3000"}}}}


def stop_server(server: subprocess.Popen[str]) -> str:
    """Stop the fixture before reading its stderr, so a failure cannot block waiting for EOF."""
    if server.poll() is None:
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=30)
    stderr = server.stderr.read() if server.stderr is not None and not server.stderr.closed else ""
    if server.stdout is not None and not server.stdout.closed:
        server.stdout.close()
    if server.stderr is not None and not server.stderr.closed:
        server.stderr.close()
    return stderr


def fixture_url(server: subprocess.Popen[str], lifecycle: Path) -> str:
    """Wait for the fixture's first URL and report why startup failed instead of parsing an empty line as JSON."""
    assert server.stdout is not None
    ready, _, _ = select.select([server.stdout], [], [], 30)
    if not ready:
        reason = "did not announce its URL within 30 seconds"
    else:
        line = server.stdout.readline()
        if line:
            try:
                return json.loads(line)["url"]
            except (json.JSONDecodeError, KeyError) as error:
                reason = f"announced an invalid session URL ({type(error).__name__})"
        else:
            reason = f"exited before announcing its URL (exit code {server.poll()})"

    stderr = stop_server(server)
    stderr = re.sub(r"#token=[^\s'\"]+", "#token=<redacted>", stderr)
    details = [f"tailscale web fixture {reason}"]
    if stderr:
        details.append(f"--- fixture stderr ---\n{stderr}")
    if lifecycle.exists():
        details.append(
            f"--- startup stages and socket state (no fd, port, or token) ---\n{lifecycle.read_text(encoding='utf-8')}"
        )
    pytest.fail("\n".join(details))


@pytest.mark.timeout(300)
def test_mounted_sharing_hot_applies_copies_and_disables_without_os_changes(tmp_path):
    environment = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "TMPDIR"}}
    environment |= {
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "HZ_CONFIG_HOME": str(tmp_path / "config"),
        "HZ_DATA_DIR": str(tmp_path / "data"),
        "HZ_CACHE_DIR": str(tmp_path / "cache"),
        "HZ_STATE_DIR": str(tmp_path / "state"),
        "HZ_LOG_DIR": str(tmp_path / "log"),
        "HZ_SYSTEM_DAEMON__UNIT_DIR": str(tmp_path / "units"),
        "HZ_SYSTEM_CREDENTIAL__BACKEND": "environment",
        "HZ_PLUGINS_ENABLED": "false",
        "HZ_ORG__HANDLE": "mira",
        "HZ_ORG__ROLE": "engineering-manager",
        "HZ_ORG__TIMEZONE": "Europe/Berlin",
        "HZ_ORG__NAME": "Mira Example",
    }
    config = tmp_path / "config"
    config.mkdir()
    (config / "config.toml").write_text("[hz]\nui_port = 8737\n", encoding="utf-8")
    vendor = tmp_path / "vendor.json"
    vendor.write_text(json.dumps({"version": 0, "writes": 0, "config": {"Services": OTHER}}), encoding="utf-8")
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run([*run, "ensure"], cwd=REPO, check=True, capture_output=True, timeout=120)
    server = subprocess.Popen(
        [sys.executable, str(REPO / "tests" / "fixtures" / "tailscale_web.py"), str(tmp_path)],
        cwd=REPO,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    session = ""
    driven = None
    result = None
    backend_stderr = ""
    fixture_lifecycle = tmp_path / "fixture-lifecycle.jsonl"
    try:
        assert server.stdout is not None
        url = fixture_url(server, fixture_lifecycle)
        assert url.startswith("http://127.0.0.1:")
        assert "#token=" in url
        driven = subprocess.run(
            [*run, "exec", "node", "e2e/tailscale.mjs"],
            input=json.dumps({"url": url, "shots": str(tmp_path / "screenshots")}),
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        session = parse_qs(urlsplit(url).fragment)["token"][0]
        if driven.returncode == 0:
            result = json.loads(driven.stdout.strip().splitlines()[-1])
    finally:
        backend_stderr = stop_server(server)
    assert driven is not None
    if driven.returncode:
        diagnostics = driven.stderr.replace(session, "<redacted-session>")
        backend_stderr = backend_stderr.replace(session, "<redacted-session>")
        if backend_stderr:
            diagnostics += f"\n--- fixture backend stderr ---\n{backend_stderr}"
        if fixture_lifecycle.exists():
            events = fixture_lifecycle.read_text(encoding="utf-8")
            diagnostics += f"\n--- fixture startup stages and socket state (no fd, port, or token) ---\n{events}"
        pytest.fail(diagnostics)
    assert result is not None
    assert result["mutations"] == {"enable": 1, "disable": 1, "restart": 0}
    assert result["copied"] == ORIGIN
    assert result["errors"] == []
    assert {(sample["width"], sample["height"]) for sample in result["layouts"]} == {
        (360, 780),
        (390, 844),
        (430, 932),
        (780, 360),
        (844, 390),
        (932, 430),
    }
    final = json.loads(vendor.read_text(encoding="utf-8"))
    assert final["writes"] == 2
    assert final["config"] == {"Services": OTHER, "TCP": {}, "Web": {}}
    saved = tomllib.loads((config / "config.toml").read_text(encoding="utf-8"))
    assert saved["hz"]["ui_allowed_origins"] == []
    assert saved["tailscale"]["intent"]["phase"] == "released"
    assert saved["tailscale"]["intent"]["enabled"] is False
