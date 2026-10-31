"""Mounted footer: installing and removing the daemon from the app, on a fake launchd that never touches the host's.

The service is the real `DaemonService` with every effect replaced: a recording runner stands in for `launchctl` and
`uv`, the unit directory is temporary and the desktop session is a parameter. Nothing registers a login service here.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from heinzel import Settings
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from heinzel.modules.system.daemon._units import LABEL
from tests.e2e._browser_result import check_browser, require_browser, wait_browser
from tests.e2e._mounted import MountedServer
from tests.e2e.docs_test import GuardedCommand
from tests.e2e.durable_five_minute_journey_test import BROWSER_CACHE, web_build

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


class FakeLaunchd:
    """`launchctl` and `uv` as one recording runner; `bootstrap` waits for the browser to release it."""

    def __init__(self, bin_dir: Path, release: Path) -> None:
        self.bin_dir = bin_dir
        self.release = release
        self.loaded = False
        self.verbs: list[str] = []

    def __call__(self, command):
        if command[1:] == ["tool", "dir", "--bin"]:
            return subprocess.CompletedProcess(command, 0, f"{self.bin_dir}\n", "")
        verb = next(part for part in command[1:] if not part.startswith("-"))
        self.verbs.append(verb)
        if verb == "bootstrap":
            deadline = time.monotonic() + 60
            while not self.release.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.loaded = True
        elif verb == "bootout":
            self.loaded = False
        code = 0 if verb != "print" or self.loaded else 113
        return subprocess.CompletedProcess(command, code, "", "")


def _fake_service(make_kernel, tmp_path, session):
    """The kernel, the service on the fake launchd, the gate that holds `bootstrap` and the temporary unit directory."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "hz").touch()
    release = tmp_path / "release"
    launchd = FakeLaunchd(bin_dir, release)
    settings = Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
    )
    kernel = make_kernel(builtins=True, settings_=settings)
    units = tmp_path / "units"
    service = DaemonService(
        kernel,
        settings,
        DaemonSettings(unit_dir=units),
        runner=launchd,
        platform="darwin",
        uid=lambda: 501,
        which=lambda name: f"/bin/{name}",
        notifier_installed=lambda: True,
        desktop_session=lambda: session,
        install_notifier=lambda: None,
        birth_fence=lambda: None,
        process_birth=lambda: None,
    )
    kernel.bind(DaemonService, service)
    return kernel, launchd, release, units


@pytest.mark.timeout(600)
@pytest.mark.parametrize("engine", ["chromium", "webkit"])
@pytest.mark.parametrize("session", ["Aqua", "Background"])
def test_mounted_footer_installs_and_removes_the_daemon(make_kernel, tmp_path, monkeypatch, engine, session):
    web_build.ensure_built()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(BROWSER_CACHE))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    (tmp_path / "home").mkdir()
    kernel, launchd, release, units = _fake_service(make_kernel, tmp_path, session)

    with MountedServer(kernel, monkeypatch) as server:
        token = server.token
        command = [
            sys.executable,
            str(ROOT / "scripts/web_build.py"),
            "exec",
            "node",
            "e2e/daemon-install.mjs",
            server.url,
            engine,
            str(tmp_path),
            session,
        ]
        guarded = GuardedCommand(command, cwd=ROOT, env=os.environ.copy())
        try:
            returncode = require_browser(wait_browser(guarded, 240))
        finally:
            release.touch()  # never leave the fake launchd holding `bootstrap`
            guarded.stop()
        stdout, stderr = guarded.capture.text("stdout"), guarded.capture.text("stderr")
        check_browser(returncode, (stderr + "\n" + stdout).replace(token, "<redacted-session>"))
        result = json.loads(stdout.strip().splitlines()[-1])
        assert result["session"] == session
        if session == "Aqua":
            assert launchd.verbs.count("bootstrap") == 1  # one click, one install, whatever the double click did
            assert not (units / f"{LABEL}.plist").exists()  # and the uninstall at the end removed it again
        else:
            assert launchd.verbs == []  # the outside session changed nothing at all
            assert not units.exists()
