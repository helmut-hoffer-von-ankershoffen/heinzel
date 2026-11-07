"""What `hz app install` and `uninstall` print, byte for byte after stripping ANSI: one golden file per case.

The installer runs for real against a fake home and a fake system Applications folder; only the release download
(three chunks on a fake clock), the code seal and the running check are faked. Nothing is launched.
`UPDATE_GOLDEN=1` rewrites the golden files.
"""

import os
import plistlib
import re
import struct
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from heinzel.heads import _desktop_installation as desktop
from heinzel.heads import _desktop_messages as copy
from heinzel.heads._desktop_report import SILENT, Reporter
from heinzel.heads.cli import _app

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).parent / "golden" / "app_install"
VERSION = "0.9.29"
SIZE = 48_199_998
"""Three equal chunks, shown as 48.2 MB."""
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class Clock:
    """Time that moves only when the fake download says so."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _fetcher(clock: Clock):
    def fetch(version: str, directory: Path, reporter: Reporter = SILENT) -> Path:
        with reporter.phase(copy.RESOLVE.format(tag=f"v{version}")):
            clock.now += 1
        with reporter.phase(copy.FETCH_MANIFEST):
            pass
        with reporter.phase(copy.DOWNLOAD.format(megabytes=SIZE / 1e6), SIZE) as advance:
            for _chunk in range(3):
                clock.now += 2
                advance(SIZE // 3)
        for step in (copy.VERIFY_CHECKSUM, copy.VERIFY_APP, copy.CONFIRM_ASSETS):
            with reporter.phase(step):
                pass
        archive = directory / "archive"
        archive.write_text(version)
        return archive

    return fetch


def _extract(archive: Path, staging: Path) -> Path:
    version = archive.read_text()
    bundle = staging / desktop.BUNDLE_NAME
    macos = bundle / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (bundle / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "org.heinzelmaennchen.desktop",
                "CFBundleExecutable": "Heinzelmaennchen",
                "CFBundleShortVersionString": version,
            }
        )
    )
    executable = macos / "Heinzelmaennchen"
    executable.write_bytes(struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0))
    executable.chmod(0o700)
    return bundle


@pytest.fixture
def mac(monkeypatch, tmp_path):
    """A fake home and a fake system Applications folder this user may write to, unless a case locks it."""
    home = tmp_path / "home"
    home.mkdir()
    system = tmp_path / "system" / "Applications"
    system.mkdir(parents=True, mode=0o755)
    executable = home / "hz"
    executable.touch(mode=0o700)
    clock = Clock()
    monkeypatch.setenv("HOME", str(home))
    for name in ("CI", "NO_COLOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(desktop, "SYSTEM_APPLICATIONS", system)
    monkeypatch.setattr(desktop, "_check_platform", lambda: None)
    monkeypatch.setattr(desktop, "fetch_archive", _fetcher(clock))
    monkeypatch.setattr(desktop, "extract_bundle", _extract)
    monkeypatch.setattr(desktop, "_prepare_for_gatekeeper", lambda _bundle, **_options: None)
    monkeypatch.setattr(desktop, "_in_use", lambda _bundle: False)
    monkeypatch.setattr(_app, "__version__", VERSION)
    monkeypatch.setattr(_app, "desktop_executable", lambda _kernel: executable)
    yield home, system, executable, clock
    system.chmod(0o755)


def _run(mac, args: list[str], *, tty: bool) -> str:
    home, _system, _executable, clock = mac
    options = {
        "force_terminal": tty,
        "force_interactive": False,
        "width": 200,
        "get_time": clock,
        "color_system": "standard" if tty else None,
    }
    group = _app.desktop_group(None, Console(**options), Console(stderr=True, **options))  # type: ignore[arg-type]
    result = CliRunner().invoke(group, args)
    assert result.exit_code == 0, result.output
    if not tty:
        assert "\x1b" not in result.output
    root = str(home.parent)
    return "\n".join(line.rstrip() for line in ANSI.sub("", result.output).replace(root, "<tmp>").splitlines())


def _golden(name: str, text: str) -> None:
    path = GOLDEN / f"{name}.txt"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.write_text(text + "\n", encoding="utf-8")
    assert text == path.read_text(encoding="utf-8").rstrip("\n")


@pytest.mark.parametrize("tty", [True, False], ids=["tty", "plain"])
def test_install_as_an_administrator(mac, tty):
    _golden(f"install-admin-{'tty' if tty else 'plain'}", _run(mac, ["install"], tty=tty))


def test_install_as_a_standard_user(mac):
    _home, system, _executable, _clock = mac
    system.chmod(0o555)
    _golden("install-standard-user", _run(mac, ["install"], tty=False))


def test_install_keeping_the_quarantine_mark(mac):
    _golden("install-keep-quarantine", _run(mac, ["install", "--keep-quarantine"], tty=False))


def test_update(mac):
    home, _system, executable, _clock = mac
    desktop.install_desktop("0.9.28", executable, home=home)
    _golden("update", _run(mac, ["install"], tty=False))


def test_already_current(mac):
    home, _system, executable, _clock = mac
    desktop.install_desktop(VERSION, executable, home=home)
    _golden("current", _run(mac, ["install"], tty=False))


@pytest.mark.parametrize("tty", [True, False], ids=["tty", "plain"])
def test_uninstall(mac, tty):
    home, _system, executable, _clock = mac
    desktop.install_desktop(VERSION, executable, home=home)
    _golden(f"uninstall-{'tty' if tty else 'plain'}", _run(mac, ["uninstall"], tty=tty))
