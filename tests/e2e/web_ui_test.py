"""`hz ui` as a person runs it (ADR 0039): it opens the browser at the app, and the app shows the doctor's report.

Runs the real `hz ui` against made-up configuration in temporary directories, opens it in Chromium through Playwright
(web/e2e/ui.mjs), runs axe on every screen and fails on any console error, which is where a CSP violation shows.
Needs network the first time: Playwright downloads Chromium, and the app is built if it is missing or stale.
`HZ_SCREENSHOTS=dir` keeps the screenshots it takes.
"""

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("web_build", REPO / "scripts" / "web_build.py")
assert spec
assert spec.loader
web_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web_build)

CONTRACT = """\
---
name: {name}
kind: responsibility
description: {description}
hz:
  schema: 1
  title: {title}
{body}---

### Goal

It is true.

### Requires

- `in0`: what it holds.

### Maintains

- `{output}`: it.

### Done when

- [ ] It is.
"""


def demo_workflows(directory: Path) -> None:
    """Two made-up contracts, one reading the other's output, so the graph has an edge between contracts."""
    directory.mkdir(parents=True)
    digest = textwrap.indent(
        "requires: [tracker:read]\n"
        "inputs:\n  in0: {read: tracker.issues_updated, args: {project: ACME}, window: 7d}\n"
        "outputs:\n  digest: {format: markdown}\n",
        "  ",
    )
    newsletter = textwrap.indent(
        'inputs:\n  in0: {read: "contract:me/weekly-digest#digest", stage: draft}\n'
        "outputs:\n  issue: {format: markdown}\n",
        "  ",
    )
    for name, title, description, body, output in (
        ("weekly-digest", "Weekly digest", "Keeps the team's week in one page.", digest, "digest"),
        ("newsletter", "Team newsletter", "Keeps stakeholders informed.", newsletter, "issue"),
    ):
        text = CONTRACT.format(name=name, title=title, description=description, body=body, output=output)
        (directory / f"{name}.prose.md").write_text(text, encoding="utf-8")


@pytest.fixture
def environment(tmp_path):
    """Made-up configuration in temporary directories; a BROWSER that records the URL instead of opening it."""
    recorded = tmp_path / "opened.txt"
    browser = tmp_path / "browser.py"
    browser.write_text(
        f"import sys, pathlib\npathlib.Path({str(recorded)!r}).write_text(sys.argv[1])\n", encoding="utf-8"
    )
    demo_workflows(tmp_path / "workflows")
    env = {key: value for key, value in os.environ.items() if not key.startswith("HZ_")}
    env |= {
        "HZ_CONFIG_HOME": str(tmp_path / "config"),
        "HZ_DATA_DIR": str(tmp_path / "data"),
        "HZ_CACHE_DIR": str(tmp_path / "cache"),
        "HZ_STATE_DIR": str(tmp_path / "state"),
        "HZ_LOG_DIR": str(tmp_path / "log"),
        "HZ_SYSTEM_DAEMON__UNIT_DIR": str(tmp_path / "units"),
        "HZ_SYSTEM_CREDENTIAL__BACKEND": "environment",
        "HZ_PLUGINS_ENABLED": "false",
        "HZ_WORKFLOW__USER_DIR": str(tmp_path / "workflows"),
        "HZ_ORG__HANDLE": "mira",
        "HZ_ORG__ROLE": "engineering-manager",
        "HZ_ORG__TIMEZONE": "Europe/Berlin",
        "HZ_ORG__NAME": "Mira Example",
        "HZ_ORG__EMAIL": "mira@example.com",
        "BROWSER": f"{sys.executable} {browser} %s",
    }
    return env, recorded


@pytest.mark.timeout(600)
def test_hz_ui_opens_the_app_and_it_shows_the_doctor_report(environment, tmp_path):
    env, recorded = environment
    if not web_build.current():
        web_build.build()
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    install = [*run, "exec", "playwright-core", "install", "chromium"]
    subprocess.run(install, cwd=REPO, check=True, timeout=600, capture_output=True)
    shots = Path(os.environ.get("HZ_SCREENSHOTS", tmp_path / "screenshots"))

    ui = subprocess.Popen(
        ["uv", "run", "hz", "ui", "--port", "0"], cwd=REPO, env=env, stderr=subprocess.PIPE, text=True
    )
    try:
        assert ui.stderr is not None
        line = ""
        for line in ui.stderr:  # the URL is the first thing it prints
            if "runs at" in line:
                break
        url = line.strip().rsplit(" ", 1)[-1]
        assert url.startswith("http://127.0.0.1:")
        assert "#token=" in url
        driven = subprocess.run(
            [*run, "exec", "node", "e2e/ui.mjs", url, str(shots)],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        assert driven.returncode == 0, driven.stderr
        result = json.loads(driven.stdout.strip().splitlines()[-1])
    finally:
        ui.terminate()
        ui.wait(30)
        if ui.stderr is not None:
            ui.stderr.close()

    assert recorded.read_text(encoding="utf-8") == url  # the browser was opened at the session URL
    assert "Heinzelmännchen is" in result["doctor"]
    assert "python" in result["doctor"]
    assert result["errors"] == []  # no CSP violation, no failed request, no exception
    assert {screen: found for screen, found in result["violations"].items() if found} == {}
    assert set(result["screens"]) >= {"doctor", "home", "settings", "contracts", "team", "doctor-dark"}
    # The splash: in the mode the system asks for from the first paint, gone by itself, and the fix when hz is gone.
    splash = result["splash"]
    assert splash["light"] == {"line": "Connecting to hz…", "background": "rgb(255, 255, 255)", "gone": True}
    assert splash["dark"] == {"line": "Connecting to hz…", "background": "rgb(24, 24, 24)", "gone": True}
    assert "hz isn't answering" in splash["error"]
    assert "hz ui" in splash["error"]
