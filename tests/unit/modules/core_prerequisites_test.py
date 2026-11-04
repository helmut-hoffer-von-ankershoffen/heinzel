"""What Core's own modules declare as external tools (ADR 0112), and that no generic code names a vendor."""

import re
from pathlib import Path

import pytest

from heinzel import Config, Homebrew, ManualInstall, PrerequisiteLet
from heinzel.modules.system import notify

pytestmark = pytest.mark.unit

SOURCE = Path(__file__).resolve().parents[3] / "src" / "heinzel"
GENERIC = [*(SOURCE / "modules/system/prerequisite").glob("*.py"), SOURCE / "heads/_prerequisites.py"]
VENDORS = re.compile(
    r"claude|codex|tailscale|github|atlassian|google|zoom|\bgws\b|\bacli\b|\bgh\b|\bgit\b", re.IGNORECASE
)


def declared(kernel):
    return {(record.qualname, let.name): let for record, let in kernel.lets(PrerequisiteLet)}


def selected(kernel, key):
    return kernel.call(declared(kernel)[key].needed)


def test_the_baseline_is_git_and_gh_declared_by_the_essential_system_module(make_kernel):
    found = declared(make_kernel(builtins=True))

    for name, package in (("git", "git"), ("gh", "gh")):
        let = found["system", name]
        assert (let.when, let.macos, let.needed) == ("install", Homebrew(package), None)
        assert isinstance(let.linux, ManualInstall)
        assert let.linux.url.startswith("https://")


def test_gh_names_its_separate_sign_in_so_installing_it_never_implies_one(make_kernel):
    assert "gh auth login" in declared(make_kernel(builtins=True))["system", "gh"].purpose


def test_only_the_selected_runners_cli_is_wanted(make_kernel):
    default = make_kernel(builtins=True)
    codex = make_kernel(builtins=True, config=Config(files=(), data={"capabilities": {"agent_runner": "codex"}}))
    command = make_kernel(builtins=True, config=Config(files=(), data={"capabilities": {"agent_runner": "command"}}))

    key_claude, key_codex = ("system.runner", "claude"), ("system.runner", "codex")
    assert (selected(default, key_claude), selected(default, key_codex)) == (True, False)
    assert (selected(codex, key_claude), selected(codex, key_codex)) == (False, True)
    assert (selected(command, key_claude), selected(command, key_codex)) == (False, False)


def test_a_custom_executable_path_is_the_owners_to_check_not_a_default_name_prerequisite(make_kernel):
    config = Config(files=(), data={"system": {"runner": {"claude_executable": "/opt/claude/bin/claude"}}})

    assert selected(make_kernel(builtins=True, config=config), ("system.runner", "claude")) is False


def test_the_runner_clis_install_as_casks_and_never_sign_in(make_kernel):
    found = declared(make_kernel(builtins=True))

    assert found["system.runner", "claude"].macos == Homebrew("claude-code", cask=True)
    assert found["system.runner", "codex"].macos == Homebrew("codex", cask=True)
    assert all(found["system.runner", name].when == "feature" for name in ("claude", "codex"))


@pytest.mark.parametrize(
    ("situation", "expected"),
    [
        (("linux", "wayland-0", False), True),
        (("linux", "", False), False),
        (("linux", "wayland-0", True), False),
        (("darwin", "wayland-0", False), False),
    ],
)
def test_notify_send_is_wanted_only_on_a_linux_desktop_that_shows_notifications(
    make_kernel, monkeypatch, situation, expected
):
    platform, display, quiet = situation
    for name in notify.SESSION:
        monkeypatch.delenv(name, raising=False)
    if display:
        monkeypatch.setenv("WAYLAND_DISPLAY", display)
    monkeypatch.setattr(notify.sys, "platform", platform)
    config = Config(files=(), data={"system": {"notify": {"quiet": quiet}}})

    assert selected(make_kernel(builtins=True, config=config), ("system.notify", "notify-send")) is expected


def test_notify_send_has_no_macos_recipe(make_kernel):
    let = declared(make_kernel(builtins=True))["system.notify", "notify-send"]

    assert let.macos is None
    assert "libnotify" in let.linux.instructions


@pytest.mark.parametrize("path", GENERIC, ids=lambda path: path.name)
def test_the_generic_engine_installer_and_prestep_name_no_vendor(path):
    assert not VENDORS.findall(path.read_text(encoding="utf-8"))
