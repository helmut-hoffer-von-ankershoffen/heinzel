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
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

import pytest

from tests.e2e._mobile_server_output import MobileServerOutput, check_driver, redact, sanitize_timeout

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


def verify_fixture_plugins(env: dict[str, str]) -> None:
    """Refuse real providers before a core browser test can import their code."""
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from importlib.metadata import entry_points; "
                "actual={(item.name,item.value) for item in entry_points(group='heinzel.modules')}; "
                "assert actual=={('sound-demo','heinzel_sound_demo')}, 'Browser tests require a core-only environment'"
            ),
        ],
        env=env,
        capture_output=True,
        check=True,
        timeout=30,
    )


def verify_stream_fixture(url: str) -> None:
    """Check the real isolated backend before the browser substitutes its sound-list fixture."""
    parsed = urlsplit(url)
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port is not None
    tokens = parse_qs(parsed.fragment).get("token", [])
    assert len(tokens) == 1
    assert tokens[0]
    origin = f"http://127.0.0.1:{parsed.port}"
    with urlopen(origin + "/", timeout=10) as response:  # noqa: S310 - validated loopback origin
        directives = [part.split() for part in response.headers.get("Content-Security-Policy", "").split(";")]
        frames = next((part[1:] for part in directives if part and part[0] == "frame-src"), [])
        assert any(frame == "https://www.youtube-nocookie.com" for frame in frames)
    request = Request(origin + "/api/system/sound/list", headers={"Authorization": f"Bearer {tokens[0]}"})  # noqa: S310 - validated loopback origin
    with urlopen(request, timeout=10) as response:  # noqa: S310 - validated loopback origin
        sound = json.load(response)
    assert sound["effective"] == "fixture-radio"
    assert any(item["name"] == "fixture-radio" and item["stream"] for item in sound["soundscapes"])


@pytest.fixture
def environment(tmp_path):
    """Made-up configuration in temporary directories; a BROWSER that records the URL instead of opening it."""
    recorded = tmp_path / "opened.txt"
    browser = tmp_path / "browser.py"
    browser.write_text(
        f"import sys, pathlib\npathlib.Path({str(recorded)!r}).write_text(sys.argv[1])\n", encoding="utf-8"
    )
    demo_workflows(tmp_path / "workflows")
    # Only a synthetic fixture provides stream content; no real plugin or bundled core catalog is installed.
    metadata = tmp_path / "plugins/heinzel_sound_demo-1.0.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel-sound-demo\nVersion: 1.0.0\n")
    (metadata / "entry_points.txt").write_text("[heinzel.modules]\nsound-demo = heinzel_sound_demo\n")
    env = {key: value for key, value in os.environ.items() if not key.startswith("HZ_")}
    env |= {
        "HZ_CONFIG_HOME": str(tmp_path / "config"),
        "HZ_DATA_DIR": str(tmp_path / "data"),
        "HZ_CACHE_DIR": str(tmp_path / "cache"),
        "HZ_STATE_DIR": str(tmp_path / "state"),
        "HZ_LOG_DIR": str(tmp_path / "log"),
        "HZ_SYSTEM_DAEMON__UNIT_DIR": str(tmp_path / "units"),
        "HZ_SYSTEM_CREDENTIAL__BACKEND": "environment",
        "HZ_PLUGINS_ENABLED": "true",
        "PYTHONPATH": os.pathsep.join((str(metadata.parent), str(REPO / "tests/fixtures/sound"))),
        # Let the real server admit the stream origin before the offline iframe layer/playback probe runs.
        "HZ_SYSTEM_SOUND__NAME": "fixture-radio",
        "HZ_SYSTEM_SOUND__FOLLOW_THEME": "false",
        "HZ_HEINZEL__USER_DIR": str(tmp_path / "workflows"),
        "HZ_ORG__HANDLE": "mira",
        "HZ_ORG__ROLE": "engineering-manager",
        "HZ_ORG__TIMEZONE": "Europe/Berlin",
        "HZ_ORG__NAME": "Mira Example",
        "HZ_ORG__EMAIL": "mira@example.com",
        "BROWSER": f"{sys.executable} {browser} %s",
    }
    verify_fixture_plugins(env)
    return env, recorded


def test_browser_fixture_refuses_unrelated_providers_before_import(environment, tmp_path):
    env, _ = environment
    metadata = tmp_path / "plugins/heinzel_unrelated-1.0.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel-unrelated\nVersion: 1.0.0\n")
    (metadata / "entry_points.txt").write_text("[heinzel.modules]\nunrelated = never_import_this_provider\n")
    with pytest.raises(subprocess.CalledProcessError) as refused:
        verify_fixture_plugins(env)
    assert b"Browser tests require a core-only environment" in refused.value.stderr


@pytest.mark.timeout(600)
def test_hz_ui_opens_the_app_and_it_shows_the_doctor_report(environment, tmp_path):
    env, recorded = environment
    web_build.ensure_built()
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    install = [*run, "exec", "playwright-core", "install", "chromium"]
    subprocess.run(install, cwd=REPO, check=True, timeout=600, capture_output=True)
    shots = Path(os.environ.get("HZ_SCREENSHOTS", tmp_path / "screenshots"))

    ui = subprocess.Popen(
        [sys.executable, "-m", "heinzel", "ui", "--port", "0"],
        cwd=REPO,
        env=env,
        stderr=subprocess.PIPE,
        text=True,
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
    assert result["navigation"] == {
        "providerSettings": True,
        "compactSettings": True,
        "privateJsonParity": True,
        "removedProviderAbsent": True,
    }


@pytest.mark.timeout(600)
def test_footer_measurement_waits_for_the_navigation_width_transition():
    """A nearly finished sidebar animation still changes the available footer width."""
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", "chromium"],
        cwd=REPO,
        check=True,
        timeout=600,
        capture_output=True,
    )
    driven = subprocess.run(
        [*run, "exec", "node", "e2e/footer-readiness.mjs"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=110,
    )
    assert driven.returncode == 0, driven.stderr
    result = json.loads(driven.stdout.strip().splitlines()[-1])
    assert 56 < result["nearFinalWidth"] <= 57
    assert result["waitedForFinishedAnimation"] is True


@pytest.mark.timeout(600)
def test_theme_image_viewports_preserve_the_cap_hinge_in_actual_chromium(tmp_path):
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", "chromium"],
        cwd=REPO,
        check=True,
        timeout=600,
        capture_output=True,
    )
    shots = Path(os.environ.get("HZ_SCREENSHOTS", tmp_path / "screenshots"))
    driven = subprocess.run(
        [*run, "exec", "node", "e2e/avatar-tilt.mjs", str(shots)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=110,
    )
    assert driven.returncode == 0, driven.stderr
    result = json.loads(driven.stdout.strip().splitlines()[-1])
    assert result == {"comparisons": 27, "reduced": True, "errors": []}


@pytest.mark.timeout(600)
@pytest.mark.e2e_early
def test_mobile_touch_viewports_keep_navigation_settings_and_chat_reachable(environment, tmp_path):
    """Chromium mobile emulation checks responsive reachability; it is not a physical iPhone Safari test."""
    env, _recorded = environment
    web_build.ensure_built()
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", "chromium"],
        cwd=REPO,
        check=True,
        timeout=600,
        capture_output=True,
    )
    shots = Path(os.environ.get("HZ_SCREENSHOTS", tmp_path / "screenshots"))
    ui = subprocess.Popen(
        [sys.executable, "-m", "heinzel", "ui", "--port", "0"], cwd=REPO, env=env, stderr=subprocess.PIPE
    )
    capture = MobileServerOutput(ui)
    token = ""
    try:
        url = capture.wait_url()
        assert url.startswith("http://127.0.0.1:")
        assert "#token=" in url
        token = parse_qs(urlsplit(url).fragment).get("token", [""])[0]
        verify_stream_fixture(url)
        try:
            driven = subprocess.run(
                [*run, "exec", "node", "e2e/mobile-responsive.mjs", url, str(shots)],
                cwd=REPO,
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
        except subprocess.TimeoutExpired as error:
            sanitize_timeout(error)
            raise
        check_driver(driven.returncode, redact(driven.stderr, token))
        result = json.loads(driven.stdout.strip().splitlines()[-1])
    except BaseException:
        sys.stderr.write(f"Mobile server pre-cleanup exit: {ui.poll()}\n")
        sys.stderr.write(f"Mobile server stderr tail:\n{capture.diagnostic(token)}\n")
        raise
    finally:
        original = sys.exception()
        try:
            if ui.poll() is None:
                ui.terminate()
            try:
                ui.wait(30)
            except subprocess.TimeoutExpired:
                ui.kill()
                ui.wait(5)
                msg = "Mobile test server required forced cleanup"
                raise RuntimeError(msg) from None
            capture.close()
            if ui.stderr is not None:
                ui.stderr.close()
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            if original is None:
                raise
            original.add_note("Mobile test server cleanup was uncertain; inspect the owned process state.")

    assert result["evidence"] == "mobile/touch emulation; not physical iPhone Safari"
    assert len(result["cases"]) == 6
    assert {case["name"] for case in result["cases"]} == {
        "narrow-portrait",
        "wide-portrait",
        "short-portrait",
        "narrow-landscape",
        "wide-landscape",
        "short-landscape",
    }
    assert result["dockedPlayer"]["popOutUsable"] is True


@pytest.mark.timeout(600)
def test_docked_media_stays_below_every_overlay_in_mounted_chromium(environment, tmp_path):
    """A focused real-browser hit test of media against menus, status popovers and Settings."""
    env, _recorded = environment
    web_build.ensure_built()
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", "chromium"],
        cwd=REPO,
        check=True,
        timeout=600,
        capture_output=True,
    )
    shots = Path(os.environ.get("HZ_SCREENSHOTS", tmp_path / "screenshots"))
    ui = subprocess.Popen(
        [sys.executable, "-m", "heinzel", "ui", "--port", "0"], cwd=REPO, env=env, stderr=subprocess.PIPE, text=True
    )
    try:
        assert ui.stderr is not None
        line = ""
        for line in ui.stderr:
            if "runs at" in line:
                break
        url = line.strip().rsplit(" ", 1)[-1]
        assert url.startswith("http://127.0.0.1:")
        assert "#token=" in url
        verify_stream_fixture(url)
        driven = subprocess.run(
            [*run, "exec", "node", "e2e/media-layers.mjs", url, str(shots)],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        assert driven.returncode == 0, driven.stderr
        result = json.loads(driven.stdout.strip().splitlines()[-1])
    finally:
        ui.terminate()
        ui.wait(30)
        if ui.stderr is not None:
            ui.stderr.close()

    assert len(result["panes"]) == 12
    assert {
        "docked-settings",
        "docked-audio",
        "docked-account",
        "docked-daemon",
        "docked-version",
    } <= set(result["panes"])
    assert result["sameFrame"] is True
    assert result["playbackAdvanced"] is True
    assert result["compactLayouts"][-2:] == [
        {"width": 768, "bounded": True},
        {"width": 390, "bounded": True},
    ]


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.timeout(300)
def test_plugin_gallery_is_lazy_accessible_and_responsive_in_both_engines(environment, tmp_path, engine):
    """The built Plugins screen previews actual declared media at desktop and all six phone sizes."""
    env, _recorded = environment
    env["PLAYWRIGHT_BROWSER"] = engine
    web_build.ensure_built()
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", engine], cwd=REPO, check=True, timeout=600, capture_output=True
    )
    shots = Path(os.environ.get("HZ_SCREENSHOTS", tmp_path / "screenshots"))
    ui = subprocess.Popen(
        [sys.executable, "-m", "heinzel", "ui", "--port", "0"], cwd=REPO, env=env, stderr=subprocess.PIPE
    )
    output = MobileServerOutput(ui)
    token = ""
    try:
        address = output.wait_url()
        token = parse_qs(urlsplit(address).fragment).get("token", [""])[0]
        driven = subprocess.run(
            [*run, "exec", "node", "e2e/plugin-gallery-proof.mjs", address, str(shots)],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        check_driver(driven.returncode, redact(driven.stderr, token))
        assert json.loads(driven.stdout.strip().splitlines()[-1]) == {
            "engine": engine,
            "viewports": 7,
            "gallery": True,
            "axe": True,
        }
    finally:
        ui.terminate()
        ui.wait(30)
        output.close()
        if ui.stderr is not None:
            ui.stderr.close()
