"""`hz ui` as a person runs it (ADR 0039): it opens the browser at the app, and the app shows the doctor's report.

Runs the real `hz ui` against made-up configuration in temporary directories, opens it in Chromium through Playwright
(web/e2e/ui.mjs), runs axe on every screen and fails on any console error, which is where a CSP violation shows.
Needs network the first time: Playwright downloads Chromium, and the app is built if it is missing or stale.
`HZ_SCREENSHOTS=dir` keeps the screenshots it takes.
"""

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import textwrap
import time
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


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start_ui(env: dict[str, str], port: int) -> tuple[subprocess.Popen[str], str]:
    ui = subprocess.Popen(
        [sys.executable, "-m", "heinzel", "ui", "--port", str(port), "--no-browser"],
        cwd=REPO,
        env=env,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert ui.stdout is not None
    url = ui.stdout.readline().strip()
    assert url.startswith(f"http://127.0.0.1:{port}/#token="), url
    return ui, url


def _node(run: list[str], script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one browser driver of web/e2e and capture what it printed."""
    return subprocess.run(
        [*run, "exec", "node", f"e2e/{script}.mjs", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=200,
    )


def _stop_ui(ui: subprocess.Popen[str]) -> None:
    ui.send_signal(signal.SIGINT)  # Ctrl-C, as in a terminal
    try:
        ui.wait(30)
    except subprocess.TimeoutExpired:
        ui.kill()
        ui.wait(30)
    for stream in (ui.stdout, ui.stderr):
        if stream is not None:
            stream.close()


@pytest.mark.timeout(900)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("mode", ["tab", "stale"])
def test_a_paired_browser_reopens_signed_in_without_a_link_after_hz_ui_restarts(environment, tmp_path, engine, mode):
    """Pair once with the printed link; Ctrl-C and a new `hz ui` with a new session token keep it signed in."""
    env, _ = environment
    web_build.ensure_built()
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", engine], cwd=REPO, check=True, timeout=900, capture_output=True
    )
    profile = tmp_path / "browser-profile"
    port = _free_port()

    def drive(step: str, url: str) -> dict[str, object]:
        driven = subprocess.run(
            [*run, "exec", "node", "e2e/device-reopen.mjs", engine, str(profile), step, url, mode],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert driven.returncode == 0, driven.stderr + driven.stdout
        return json.loads(driven.stdout.strip().splitlines()[-1])

    ui, link = _start_ui(env, port)
    origin = f"http://127.0.0.1:{port}/"
    try:
        first = drive("pair", link)
        assert first["signedIn"] is True, first
        assert first["device"] is True, first
        # The app window is closed and reopened from the same profile while `hz ui` still runs: no token in the URL.
        again = drive("open", origin)
        assert again["signedIn"] is True, again
    finally:
        _stop_ui(ui)
    ui, new_link = _start_ui(env, port)  # a new session token, the same fixed port and so the same origin
    try:
        assert new_link != link
        reopened = drive("open", origin)
        assert reopened["signedIn"] is True, reopened
        # An unpaired device is refused, and says so.
        revoked = subprocess.run(
            [sys.executable, "-m", "heinzel", "ui", "--revoke", "all"],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert revoked.returncode == 0, revoked.stderr
        refused = drive("open", origin)
        assert refused["signedIn"] is False, refused
    finally:
        _stop_ui(ui)


@pytest.mark.timeout(600)
def test_plugin_install_confirmation_opens_in_the_viewport_under_its_own_row(environment, tmp_path):
    """Install on a row low in Discover shows its confirmation next to the click, focused, on desktop and phone."""
    env, _ = environment
    web_build.ensure_built()
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", "chromium"], cwd=REPO, check=True, timeout=600, capture_output=True
    )
    shots = Path(os.environ.get("HZ_SCREENSHOTS", tmp_path / "screenshots"))
    ui = subprocess.Popen(
        [sys.executable, "-m", "heinzel", "ui", "--port", "0", "--no-browser"],
        cwd=REPO,
        env=env,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert ui.stdout is not None
        url = ui.stdout.readline().strip()
        assert url.startswith("http://127.0.0.1:")
        driven = subprocess.run(
            [*run, "exec", "node", "e2e/plugin-install-inline.mjs", url, str(shots)],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            timeout=200,
        )
    finally:
        ui.terminate()
        ui.wait(30)
        for stream in (ui.stdout, ui.stderr):
            if stream is not None:
                stream.close()
    assert driven.returncode == 0, driven.stderr + driven.stdout
    result = json.loads(driven.stdout.strip().splitlines()[-1])
    assert [item["name"] for item in result["confirmations"]] == ["desktop", "phone"]


@pytest.mark.timeout(900)
def test_a_plugin_installed_outside_the_running_app_offers_restart_and_apply_in_the_footer(environment, tmp_path):
    """After a terminal install while `hz ui` runs, the app itself says which plugin and offers the restart."""
    env, _ = environment
    web_build.ensure_built()
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", "chromium"], cwd=REPO, check=True, timeout=600, capture_output=True
    )
    # A throwaway installation of the built wheel: `hz system plugin develop` changes only this environment.
    wheels = tmp_path / "wheels"
    subprocess.run(["uv", "build", "--quiet", "--wheel", "--out-dir", str(wheels)], cwd=REPO, check=True, timeout=600)
    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", "--quiet", str(venv)], check=True, timeout=120)
    wheel = next(wheels.glob("*.whl"))
    subprocess.run(
        ["uv", "pip", "install", "--quiet", "--python", str(venv / "bin" / "python"), str(wheel)],
        check=True,
        timeout=600,
    )
    plugin = tmp_path / "heinzel-sidecar"
    (plugin / "src" / "heinzel_sidecar").mkdir(parents=True)
    (plugin / "pyproject.toml").write_text(
        '[project]\nname = "heinzel-sidecar"\nversion = "1.0.0"\ndescription = "A plugin installed outside the app."\n'
        'requires-python = ">=3.12"\ndependencies = []\n'
        '[project.entry-points."heinzel.modules"]\nsidecar = "heinzel_sidecar"\n'
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
        '[tool.hatch.build.targets.wheel]\npackages = ["src/heinzel_sidecar"]\n',
        encoding="utf-8",
    )
    (plugin / "src" / "heinzel_sidecar" / "__init__.py").write_text(
        'from heinzel import Module\n\nmodule = Module("sidecar", "A plugin installed outside the app.")\n\n\n'
        '@module.operation\ndef ping() -> int:\n    """Answer with one."""\n    return 1\n',
        encoding="utf-8",
    )
    shots = Path(os.environ.get("HZ_SCREENSHOTS", tmp_path / "screenshots"))
    hz = venv / "bin" / "hz"
    ui = subprocess.Popen(
        [str(hz), "ui", "--port", "0", "--no-browser"],
        cwd=tmp_path,
        env=env,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert ui.stdout is not None
        url = ui.stdout.readline().strip()
        assert url.startswith("http://127.0.0.1:")
        driven = subprocess.run(
            [*run, "exec", "node", "e2e/plugins-pending.mjs", url, str(hz), str(plugin), str(shots)],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=400,
        )
    finally:
        ui.terminate()
        ui.wait(30)
        for stream in (ui.stdout, ui.stderr):
            if stream is not None:
                stream.close()
    assert driven.returncode == 0, driven.stderr + driven.stdout
    assert json.loads(driven.stdout.strip().splitlines()[-1]) == {"pending": True}


@pytest.mark.timeout(600)
def test_a_refused_sign_in_offers_a_paste_field_that_signs_in_with_the_printed_link(environment, tmp_path):
    """An installed app has no address bar: the sign-in page takes the printed link and rejects another address."""
    env, _ = environment
    web_build.ensure_built()
    run = [sys.executable, str(REPO / "scripts" / "web_build.py")]
    subprocess.run(
        [*run, "exec", "playwright-core", "install", "chromium"], cwd=REPO, check=True, timeout=600, capture_output=True
    )
    ui, url = _start_ui(env, _free_port())
    try:
        driven = _node(run, "signin-paste", url, str(tmp_path))
    finally:
        _stop_ui(ui)
    assert driven.returncode == 0, driven.stderr
    assert json.loads(driven.stdout.strip().splitlines()[-1]) == {"pasted": True}


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


# The sections of web/e2e/mobile-responsive.mjs: each viewport sweep, and the phone flows (scroll, sound, notification).
# Each runs in its own driver child against its own `hz ui` and isolated directories, so the cases are independent.
MOBILE_VIEWPORTS = (
    "narrow-portrait",
    "wide-portrait",
    "short-portrait",
    "narrow-landscape",
    "wide-landscape",
    "short-landscape",
)
MOBILE_SECTIONS = (*MOBILE_VIEWPORTS, "phone")
# The driver child's deadline per section. One section is a seventh or less of what the single 300 s child covered
# (all of it took 205 s on Ubuntu and 294 s on macOS; a section ran in 15-45 s locally), so this leaves a section 2.5x
# its expected time, and a hung one fails in two minutes instead of five.
MOBILE_CHILD_SECONDS = 120


def test_mobile_sections_cover_every_viewport_of_the_driver():
    """A viewport added to the driver must also be a test case, or it would silently stop running."""
    driver = (REPO / "web" / "e2e" / "mobile-responsive.mjs").read_text(encoding="utf-8")
    declared = re.findall(r'^  \{ name: "([a-z-]+)", width: \d+, height: \d+ \},$', driver, flags=re.MULTILINE)
    assert tuple(declared) == MOBILE_VIEWPORTS


def check_mobile_result(section, result):
    """A viewport section proves exactly its own case; the phone section, the docked player and iPhone controls."""
    assert result["evidence"] == "mobile/touch emulation; not physical iPhone Safari"
    if section == "phone":
        assert result["cases"] == []
        assert result["dockedPlayer"]["popOutUsable"] is True
        assert result["iphoneControls"] == {"deviceVolume": True, "notificationTest": True, "serverActionsHidden": True}
    else:
        assert [case["name"] for case in result["cases"]] == [section]
        assert "dockedPlayer" not in result


def report_mobile_time(capfd, section, begun):
    """Say how much of its deadline a section used; visible from xdist workers too, for tuning the deadline."""
    with capfd.disabled():
        sys.stderr.write(f"Mobile section {section}: {time.monotonic() - begun:.1f} s of {MOBILE_CHILD_SECONDS} s\n")


@pytest.mark.timeout(600)
@pytest.mark.e2e_early
@pytest.mark.parametrize("section", MOBILE_SECTIONS)
def test_mobile_touch_viewports_keep_navigation_settings_and_chat_reachable(environment, tmp_path, capfd, section):
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
    # One directory per section: workers running other sections in parallel must not overwrite each other's files.
    shots = Path(os.environ.get("HZ_SCREENSHOTS", tmp_path / "screenshots")) / section
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
        begun = time.monotonic()
        try:
            driven = subprocess.run(
                [*run, "exec", "node", "e2e/mobile-responsive.mjs", url, str(shots), section],
                cwd=REPO,
                capture_output=True,
                text=True,
                check=False,
                timeout=MOBILE_CHILD_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            sanitize_timeout(error)
            raise error.with_traceback(None) from None
        check_driver(driven.returncode, redact(driven.stderr, token))
        result = json.loads(driven.stdout.strip().splitlines()[-1])
        report_mobile_time(capfd, section, begun)
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

    check_mobile_result(section, result)


def drive_ui_script(env, tmp_path, script):
    """Run one Playwright script of web/e2e against a real `hz ui` and return its last line of JSON."""
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
            [*run, "exec", "node", f"e2e/{script}", url, str(shots)],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
        assert driven.returncode == 0, driven.stderr
        return json.loads(driven.stdout.strip().splitlines()[-1])
    finally:
        ui.terminate()
        ui.wait(30)
        if ui.stderr is not None:
            ui.stderr.close()


@pytest.mark.timeout(600)
def test_docked_media_stays_below_every_overlay_in_mounted_chromium(environment, tmp_path):
    """A focused real-browser hit test of media against menus, status popovers and Settings."""
    env, _recorded = environment
    result = drive_ui_script(env, tmp_path, "media-layers.mjs")

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


@pytest.mark.timeout(600)
def test_sound_picker_stays_open_and_plays_the_pick_in_mounted_chromium(environment, tmp_path):
    """Open the picker, pick a soundscape: it plays at once and the picker stays; the old flow closed it silent."""
    env, _recorded = environment
    result = drive_ui_script(env, tmp_path, "sound-picker.mjs")

    assert result == {"stayedOpen": True, "played": True, "switched": True, "closes": ["Escape", "outside"]}
