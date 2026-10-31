"""The installer's prestep (ADR 0112): the baseline tools are checked, and installed where the machine allows."""

import hashlib
import json
import subprocess

import pytest

from heinzel import Config, HeinzelError, PrerequisiteService, Settings
from heinzel.heads import _prerequisites, install
from heinzel.heads._prerequisites import prepare
from heinzel.modules.system.prerequisite._host import ToolHost
from heinzel.modules.system.prerequisite._service import ToolPrerequisites
from tests._prerequisite_machine import BREW, Machine, ready_everywhere

pytestmark = pytest.mark.unit


@pytest.fixture
def settings() -> Settings:
    return Config.empty().settings(Settings, "hz")


def machine_with(monkeypatch: pytest.MonkeyPatch, machine: Machine) -> Machine:
    monkeypatch.setattr(ToolHost, "system", classmethod(lambda cls: machine.host()))
    return machine


def baseline(**present: str) -> dict[str, str]:
    return {f"/usr/bin/{name}": text for name, text in present.items()}


def test_working_tools_are_named_and_nothing_is_pending(settings, monkeypatch):
    machine_with(monkeypatch, Machine("linux", present=baseline(git="git version 2.40.1", gh="gh version 2.45.0")))
    written: list[str] = []

    assert prepare(settings, install=True, write=written.append) == []

    assert "".join(written) == "Tools ready: git 2.40.1, gh 2.45.0.\n"


def test_on_linux_a_missing_tool_is_explained_and_left_to_the_person(settings, monkeypatch):
    machine = machine_with(monkeypatch, Machine("linux", present=baseline(git="git version 2.40.1")))
    written: list[str] = []

    pending = prepare(settings, install=True, write=written.append)

    assert len(pending) == 1
    assert pending[0].startswith("  GitHub CLI (gh): Install the GitHub CLI for your distribution")
    assert "These tools are still missing; install them yourself" in "".join(written)
    assert machine.commands == [["/usr/bin/git", "--version"]]


def test_on_macos_with_homebrew_the_missing_tool_is_named_then_installed(settings, monkeypatch):
    machine = machine_with(monkeypatch, Machine(present={BREW: "", "/opt/homebrew/bin/git": "git version 2.40.1"}))
    machine.provides["gh"] = {"/opt/homebrew/bin/gh": "gh version 2.45.0"}
    written: list[str] = []

    pending = prepare(settings, install=True, write=written.append)

    text = "".join(written)
    assert pending == []
    assert text.startswith(
        "Installing the tools Heinzelmännchen needs with Homebrew:\n  GitHub CLI (gh): Homebrew installs it"
    )
    assert "Tools ready: git 2.40.1, gh 2.45.0." in text
    assert [BREW, "install", "--formula", "gh"] in machine.commands


def test_a_dry_run_only_looks(settings, monkeypatch):
    machine = machine_with(monkeypatch, Machine(present={BREW: "", "/opt/homebrew/bin/git": "git version 2.40.1"}))
    written: list[str] = []

    pending = prepare(settings, install=False, write=written.append)

    assert len(pending) == 1
    assert not [c for c in machine.commands if c[0] == BREW]
    assert "Installing" not in "".join(written)


def test_macos_without_homebrew_does_not_install_a_package_manager(settings, monkeypatch):
    machine = machine_with(monkeypatch, Machine(present=baseline(git="git version 2.40.1")))
    written: list[str] = []

    pending = prepare(settings, install=True, write=written.append)

    assert len(pending) == 1
    assert "Install Homebrew first" in pending[0]
    assert all(c[0] != "curl" and c[0] != "sh" for c in machine.commands)


def test_a_service_that_cannot_check_is_reported_without_failing_the_installation(settings, monkeypatch):
    machine_with(monkeypatch, Machine("linux"))

    def broken(self, *args, **kwargs):
        msg = "no probe"
        raise HeinzelError(msg)

    monkeypatch.setattr(ToolPrerequisites, "check", broken)
    written: list[str] = []

    assert prepare(settings, install=True, write=written.append) == []
    assert written == ["The tools Heinzelmännchen needs could not be checked: no probe\n"]


def test_without_the_prerequisite_module_the_installer_has_nothing_to_add(settings, monkeypatch):
    class NoService:
        def provides(self, kind: object) -> bool:
            assert kind is PrerequisiteService
            return False

    monkeypatch.setattr(_prerequisites, "boot", lambda settings: NoService())
    written: list[str] = []

    assert prepare(settings, install=True, write=written.append) == []
    assert written == []


def profile_file(tmp_path):
    wheel = tmp_path / "heinzel-0.9.0-py3-none-any.whl"
    wheel.write_bytes(b"local artifact")
    artifact = {
        "name": "heinzel",
        "version": "0.9.0",
        "filename": wheel.name,
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "url": str(wheel),
    }
    profile = tmp_path / "standard.json"
    profile.write_text(json.dumps({"format": 2, "core_version": "0.9.0", "sdk": 1, "artifacts": [artifact]}))
    return profile


def run_main(tmp_path, monkeypatch, *arguments: str) -> int:
    file = profile_file(tmp_path)
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/bin/uv")
    monkeypatch.setattr(install.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0))
    return install.main(["--profile", str(file), *arguments])


def test_a_manual_outcome_never_fails_the_installation_and_is_repeated_at_its_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ToolHost, "system", classmethod(lambda cls: ready_everywhere("gh")))

    status = run_main(tmp_path, monkeypatch, "--python", "/isolated/bin/python")

    err = capsys.readouterr().err
    assert status == 0
    assert err.index("GitHub CLI (gh)") < err.index("Heinzelmännchen is installed. Still missing:")
    assert err.rindex("GitHub CLI (gh)") > err.index("Heinzelmännchen is installed. Still missing:")
    assert "`hz system prerequisite check`" in err


def test_ready_tools_add_one_line_and_no_reminder(tmp_path, monkeypatch, capsys):
    status = run_main(tmp_path, monkeypatch, "--python", "/isolated/bin/python")

    err = capsys.readouterr().err
    assert status == 0
    assert "Tools ready: git 9.9.9, gh 9.9.9." in err
    assert "Heinzelmännchen is installed." not in err


def test_a_failed_installation_does_not_add_the_reminder(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ToolHost, "system", classmethod(lambda cls: ready_everywhere("gh")))
    file = profile_file(tmp_path)
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/bin/uv")
    monkeypatch.setattr(install.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 3))

    status = install.main(["--profile", str(file), "--python", "/isolated/bin/python"])

    assert status == 3
    assert "Heinzelmännchen is installed." not in capsys.readouterr().err


def test_a_dry_run_checks_the_tools_but_installs_nothing(tmp_path, monkeypatch, capsys):
    machine = machine_with(monkeypatch, Machine(present={BREW: ""}))

    assert run_main(tmp_path, monkeypatch, "--dry-run") == 0

    assert not [c for c in machine.commands if c[0] == BREW]
    assert "this run installs nothing" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("arguments", "installs"),
    [
        ((), True),
        (("--dry-run",), False),
        (("--no-tools",), False),
        (("--python", "/isolated/bin/python"), False),
    ],
)
def test_only_a_plain_run_may_install_the_tools(tmp_path, monkeypatch, arguments, installs):
    seen: list[bool] = []

    def record(settings, *, install, write):
        seen.append(install)
        return []

    monkeypatch.setattr(install, "prepare_tools", record)
    monkeypatch.setattr(install, "prepare", lambda *args: [tmp_path / "core"])
    monkeypatch.setattr(install, "_managed_install", lambda *args: 0)
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/bin/uv")
    monkeypatch.setattr(install.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0))

    assert install.main(["--profile", str(profile_file(tmp_path)), *arguments]) == 0

    assert seen == [installs]


def test_a_failing_homebrew_leaves_the_tool_pending_and_the_installation_untouched(tmp_path, monkeypatch, capsys):
    machine = machine_with(monkeypatch, Machine(present={BREW: "", "/opt/homebrew/bin/git": "git version 2.40.1"}))
    machine.brew_code = 1
    monkeypatch.setattr(install, "uv_executable", lambda: "/trusted/bin/uv")
    monkeypatch.setattr(install, "prepare", lambda *args: [tmp_path / "core"])
    monkeypatch.setattr(install, "_managed_install", lambda *args: 0)

    status = install.main(["--profile", str(profile_file(tmp_path))])

    err = capsys.readouterr().err
    assert status == 0
    assert "brew install gh" in err
    assert "Heinzelmännchen is installed. Still missing:" in err
