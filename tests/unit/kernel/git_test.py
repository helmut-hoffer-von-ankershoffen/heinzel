"""The shared Git boundary refuses command transports and isolates subprocess identity and environment."""

import signal
import subprocess

import pytest

from heinzel.kernel import CommandFailed, GitRunner, _git, check_repository_url, git_command

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "url",
    [
        "ext::sh",
        "fd::1",
        "-upload-pack=evil",
        "https://token@example.test/repo",
        "ssh://-host/repo",
        "git@host:-repo",
        "https://example.test/repo?token=hidden",
        "https://example.test/repo\nextra",
    ],
)
def test_unsafe_repository_inputs_are_refused(url):
    with pytest.raises(ValueError, match=r".+"):
        check_repository_url(url)


@pytest.mark.parametrize(
    "url", ["https://example.test/org/repo.git", "git@example.test:org/repo", "/example/state.git"]
)
def test_checked_urls_are_single_arguments_with_transport_hardening(url):
    command = git_command("clone", "--", check_repository_url(url))
    assert command[-2:] == ["--", url]
    assert "protocol.ext.allow=never" in command
    assert "submodule.recurse=false" in command


def test_git_environment_drops_credentials_and_repository_redirection(monkeypatch):
    for key in [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
        "GIT_CONFIG_PARAMETERS",
        "HZ_CREDENTIAL_PRIVATE",
    ]:
        monkeypatch.setenv(key, "hidden")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/example/agent")
    environment = _git._environment()
    assert not any(value == "hidden" for value in environment.values())
    assert environment["SSH_AUTH_SOCK"] == "/example/agent"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_ASKPASS"] == ""


def test_git_runner_pins_identity_and_never_opens_stdin(monkeypatch):
    calls = []

    class Process:
        returncode = 0

        def communicate(self, timeout):
            return "output", ""

    def launch(command, **kwargs):
        calls.append((command, kwargs))
        return Process()

    monkeypatch.setattr(_git.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(_git.subprocess, "Popen", launch)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "private person")
    assert GitRunner(pinned=(("GIT_AUTHOR_NAME", "mira"),))(["git", "status"], None) == "output"
    command, options = calls[0]
    assert command == ["/usr/bin/git", "status"]
    assert options["env"]["GIT_AUTHOR_NAME"] == "mira"
    assert options["stdin"] == subprocess.DEVNULL
    assert options["start_new_session"] is True


def test_git_timeout_stops_the_entire_process_group(monkeypatch):
    killed = []

    class Process:
        pid = 123
        returncode = None
        attempts = 0

        def communicate(self, timeout):
            self.attempts += 1
            if self.attempts < 3:
                msg = "git"
                raise subprocess.TimeoutExpired(msg, timeout)
            return "", ""

    monkeypatch.setattr(_git.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(_git.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(_git.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(CommandFailed, match="took longer than 1 s"):
        GitRunner(timeout=1)(["git", "fetch"], None)
    assert killed == [(123, signal.SIGTERM), (123, signal.SIGKILL)]
