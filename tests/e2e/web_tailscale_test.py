"""The real mounted sharing control changes only an isolated fake vendor and verifies a replacement web process."""

import json
import os
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


@pytest.mark.timeout(300)
def test_mounted_sharing_restarts_verifies_copies_and_disables_without_os_changes(tmp_path):
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
    subprocess.run([*run, "build"], cwd=REPO, check=True, capture_output=True, timeout=120)
    server = subprocess.Popen(
        [sys.executable, str(REPO / "tests" / "fixtures" / "tailscale_web.py"), str(tmp_path)],
        cwd=REPO,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert server.stdout is not None
        url = json.loads(server.stdout.readline())["url"]
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
        assert driven.returncode == 0, driven.stderr.replace(session, "<redacted-session>")
        result = json.loads(driven.stdout.strip().splitlines()[-1])
    finally:
        server.terminate()
        server.wait(30)
        if server.stdout is not None:
            server.stdout.close()
        if server.stderr is not None:
            server.stderr.close()
    assert result["replacements"] == 2
    assert result["mutations"] == {"enable": 1, "disable": 1, "restart": 2}
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
