"""Heinzel Notifier (ADR 0043): copied from the bundle, verified, replaced when it differs, launched through `open`.

A fake app bundle and a fake runner: no real app is installed and nothing is launched.
"""

import hashlib
import importlib.util
import subprocess
from pathlib import Path

import pytest

from heinzel.modules.system.notify._macos import (
    EXECUTABLE,
    SOURCES,
    NotifierApp,
    bundle_version,
    source_version,
    tree_digest,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]


def fake_app(path: Path, version: str = "abc", binary: bytes = b"\xcf\xfa\xed\xfe") -> Path:
    (path / "Contents" / "MacOS").mkdir(parents=True)
    (path / "Contents" / "MacOS" / "notifier").write_bytes(binary)
    (path / "Contents" / "Info.plist").write_text(
        f'<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleVersion</key><string>{version}</string>'
        "</dict></plist>",
        encoding="utf-8",
    )
    return path


class Runner:
    """Answers `open` by writing `said` to the file given with --stdout; records every command."""

    def __init__(self, said: str = "posted", code: int = 0) -> None:
        self.said, self.code = said, code
        self.commands: list[list[str]] = []

    def __call__(self, command, timeout=None):
        command = list(command)
        self.commands.append(command)
        if "--stdout" in command:
            Path(command[command.index("--stdout") + 1]).write_text(f"{self.said}\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, self.code, "", "LaunchServices said no")


@pytest.fixture
def bundled(tmp_path) -> Path:
    return fake_app(tmp_path / "wheel" / "HeinzelNotifier.app")


def test_it_installs_a_verified_copy_and_keeps_it_while_it_matches(tmp_path, bundled):
    app = NotifierApp(tmp_path / "home", bundled=bundled, runner=Runner())
    assert app.inspect().state == "missing"
    assert app.ensure().state == "installed"
    assert app.path == tmp_path / "home" / "Applications" / "Heinzel Notifier.app"
    assert tree_digest(app.path) == tree_digest(bundled)
    assert (app.path / EXECUTABLE).stat().st_mode & 0o111
    assert app.ensure().state == "current"
    assert app.inspect().state == "current"


def test_a_copy_that_differs_is_replaced(tmp_path, bundled):
    app = NotifierApp(tmp_path / "home", bundled=bundled, runner=Runner())
    fake_app(app.path, version="old", binary=b"old")
    assert app.inspect().state == "outdated"
    assert app.ensure().state == "installed"
    assert bundle_version(app.path) == "abc"
    assert not list(app.path.parent.glob(".heinzel-notifier-*"))  # the staging directory is gone


def test_without_a_bundle_an_installed_app_is_still_used(tmp_path):
    app = NotifierApp(tmp_path / "home", bundled=tmp_path / "none.app", runner=Runner())
    assert not app.available
    assert app.ensure().state == "missing"
    fake_app(app.path)
    assert app.available
    assert app.ensure().state == "outdated"


def test_it_posts_through_launchservices_with_arguments_only(tmp_path, bundled):
    runner = Runner()
    app = NotifierApp(tmp_path / "home", bundled=bundled, runner=runner)
    assert app.post('" & do shell script "x"', "Read it.", "http://127.0.0.1:8737/doctor") == "posted"
    command = runner.commands[0]
    assert command[:4] == ["/usr/bin/open", "-n", "-g", "-W"]
    assert command[command.index("--args") - 1] == str(app.path)
    assert command[command.index("--args") + 1 :] == [
        "--title",
        '" & do shell script "x"',
        "--message",
        "Read it.",
        "--open",
        "http://127.0.0.1:8737/doctor",
    ]


def test_what_the_app_said_comes_back(tmp_path, bundled):
    def said(runner: Runner) -> str:
        return NotifierApp(tmp_path / "home", bundled=bundled, runner=runner).status()

    assert said(Runner("authorized")) == "authorized"
    assert said(Runner("denied")) == "denied"
    assert said(Runner(code=124)).startswith("waiting: macOS is asking")
    assert said(Runner(code=1)) == "open failed: LaunchServices said no"
    assert said(Runner("")) == "no answer from Heinzel Notifier"


def test_quarantine_is_read_with_xattr(tmp_path, bundled):
    runner = Runner(code=0)
    app = NotifierApp(tmp_path / "home", bundled=bundled, runner=runner)
    assert app.quarantined()
    assert runner.commands[0][:3] == ["/usr/bin/xattr", "-p", "com.apple.quarantine"]
    assert not NotifierApp(tmp_path / "home", bundled=bundled, runner=Runner(code=1)).quarantined()


def test_the_build_script_and_the_runtime_agree_on_the_version():
    spec = importlib.util.spec_from_file_location("notifier_build", ROOT / "scripts" / "notifier.py")
    assert spec is not None
    assert spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert script._version() == source_version(SOURCES)
    assert len(source_version()) == 16
    assert (
        source_version()
        == hashlib.sha256(
            b"".join(
                n.encode() + b"\0" + (SOURCES / n).read_bytes() + b"\0"
                for n in ("Info.plist", "icon.swift", "main.swift")
            )
        ).hexdigest()[:16]
    )


def test_the_swift_source_opens_only_the_web_app_on_this_machine():
    source = (SOURCES / "main.swift").read_text(encoding="utf-8")
    assert 'url.scheme == "http", url.host == "127.0.0.1", url.port != nil' in source
    assert "Process(" not in source  # it never runs anything
    assert "NSAppleScript" not in source


def test_a_failed_swap_puts_the_old_app_back(tmp_path, bundled, monkeypatch):
    app = NotifierApp(tmp_path / "home", bundled=bundled, runner=Runner())
    fake_app(app.path, version="old", binary=b"old")
    original = Path.rename

    def refuse_the_new_one(self: Path, target: Path) -> Path:
        if self.name == "Heinzel Notifier.app" and target == app.path:
            msg = "disk full"
            raise OSError(28, msg)
        return original(self, target)

    monkeypatch.setattr(Path, "rename", refuse_the_new_one)
    assert app.ensure().state == "outdated"
    assert bundle_version(app.path) == "old"


def test_a_symlink_changes_the_digest(tmp_path, bundled):
    before = tree_digest(bundled)
    (bundled / "Contents" / "Resources").mkdir()
    (bundled / "Contents" / "Resources" / "link").symlink_to("/etc/passwd")
    assert tree_digest(bundled) != before


def test_the_app_never_waits_forever_for_the_persons_answer():
    assert 'asyncAfter(deadline: .now() + 60) { finish("waiting", 3) }' in (SOURCES / "main.swift").read_text("utf-8")
