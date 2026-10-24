"""The real `Gh` runner starting a fake `gh` executable: arguments, environment, deadline, bounds, version, login."""

import json
import signal
import subprocess
import time
from pathlib import Path

import pytest
from heinzel_github import Gh, GithubSettings, _gh

from heinzel import HeinzelError

pytestmark = pytest.mark.integration

PATH = "repos/acme/widgets/pulls"


def call(*extra: str) -> list[str]:
    return ["api", PATH, *extra]


def gh(**settings) -> Gh:
    return Gh(GithubSettings(**settings))


def test_gh_gets_exactly_the_arguments_and_its_answer_comes_back(fake_gh_cli):
    fake_gh_cli.answer(PATH, fixture="pulls.json")
    result = gh().run(call("--raw-field", "title=$(touch pwned); `id`"), host="github.com", timeout=10)
    assert result.returncode == 0
    assert [item["number"] for item in json.loads(result.stdout)] == [7, 6]
    (api,) = fake_gh_cli.api_calls()
    assert api["argv"] == call("--raw-field", "title=$(touch pwned); `id`")
    assert api["cwd"] == str(Path.home())
    assert not list(Path.home().glob("pwned"))


def test_gh_sees_an_allowlisted_environment_pinned_to_the_host(fake_gh_cli, monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_GITHUB_GITHUB_TOKEN", "made-up-secret")
    monkeypatch.setenv("GH_DEBUG", "api")
    monkeypatch.setenv("GODEBUG", "http2debug=2")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "made-up-other-secret")
    monkeypatch.setenv("GH_TOKEN", "made-up-gh-token")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("PATH", f"{fake_gh_cli.bin}:relative/bin:.")
    fake_gh_cli.answer(PATH, stdout="[]")
    gh().run(call(), host="ghe.example.com", timeout=10)
    env = fake_gh_cli.api_calls()[0]["env"]
    assert env["GH_HOST"] == "ghe.example.com"
    assert env["GH_PROMPT_DISABLED"] == "1"
    assert env["GH_TOKEN"] == "made-up-gh-token"  # gh's own login, for gh
    assert env["LC_ALL"] == "C"
    assert env["PATH"] == str(fake_gh_cli.bin)
    for name in ("HZ_CREDENTIAL_GITHUB_GITHUB_TOKEN", "GH_DEBUG", "GODEBUG", "AWS_SECRET_ACCESS_KEY"):
        assert name not in env


def test_an_error_keeps_the_exit_code_stderr_and_body(fake_gh_cli):
    fake_gh_cli.answer(PATH, stdout='{"message": "Not Found"}', stderr="gh: Not Found (HTTP 404)\n", exit=1)
    result = gh().run(call(), host="github.com", timeout=10)
    assert (result.returncode, result.stderr) == (1, "gh: Not Found (HTTP 404)\n")
    assert result.stdout == b'{"message": "Not Found"}'


def test_gh_is_stopped_at_the_deadline(fake_gh_cli):
    fake_gh_cli.answer(PATH, stdout="[]", sleep=30)
    result = gh().run(call(), host="github.com", timeout=1)
    assert result.returncode is None
    assert fake_gh_cli.api_calls()  # it had started


def test_gh_is_stopped_when_its_output_passes_the_cap(fake_gh_cli, monkeypatch):
    monkeypatch.setattr(_gh, "MAX_BYTES", 1000)
    fake_gh_cli.answer(PATH, stdout="[", pad=200_000)
    result = gh().run(call(), host="github.com", timeout=10)
    assert result.truncated
    assert len(result.stdout) == 1001


def test_gh_still_running_past_the_cap_is_stopped_at_once(fake_gh_cli, monkeypatch):
    monkeypatch.setattr(_gh, "MAX_BYTES", 1000)
    fake_gh_cli.answer(PATH, stdout="[", pad=2000, linger=30)
    started = time.monotonic()
    result = gh().run(call(), host="github.com", timeout=20)
    assert result.truncated
    assert time.monotonic() - started < 10


def test_signalling_a_gh_that_is_gone_is_harmless(fake_gh_cli):
    process = subprocess.Popen([str(fake_gh_cli.bin / "gh"), "--version"], stdout=subprocess.DEVNULL)
    process.wait()
    _gh._signal(process, signal.SIGKILL)


def test_only_the_end_of_stderr_is_kept(fake_gh_cli):
    fake_gh_cli.answer(PATH, stdout="[]", stderr="a" * _gh.MAX_ERROR_BYTES + "the end\n")
    result = gh().run(call(), host="github.com", timeout=10)
    assert len(result.stderr) == _gh.MAX_ERROR_BYTES
    assert result.stderr.endswith("the end\n")
    assert not result.truncated


def test_the_version_is_asked_once_per_kernel(fake_gh_cli):
    fake_gh_cli.answer(PATH, stdout="[]")
    runner = gh()
    assert runner.version() == (2, 103, 2)
    runner.run(call(), host="github.com", timeout=10)
    runner.run(call(), host="github.com", timeout=10)
    assert [c["argv"][0] for c in fake_gh_cli.calls()] == ["--version", "api", "api"]


def test_a_gh_older_than_the_minimum_is_refused_and_asked_again(fake_gh_cli):
    fake_gh_cli.version("gh version 2.20.1 (2022-11-07)\n")
    runner = gh()
    with pytest.raises(HeinzelError, match=r"is gh 2\.20\.1; the GitHub adapter needs 2\.40\.0 or later"):
        runner.run(call(), host="github.com", timeout=10)
    fake_gh_cli.version("gh version 2.41.0 (2024-01-01)\n")
    assert runner.version() == (2, 41, 0)
    assert not fake_gh_cli.api_calls()


def test_a_version_gh_does_not_name_is_not_held_against_it(fake_gh_cli):
    fake_gh_cli.version("a build without a version\n")
    fake_gh_cli.answer(PATH, stdout="[]")
    runner = gh()
    assert runner.version() is None
    assert runner.run(call(), host="github.com", timeout=10).returncode == 0


def test_a_version_that_does_not_come_is_a_clear_error(fake_gh_cli, monkeypatch):
    monkeypatch.setattr(_gh, "VERSION_TIMEOUT", 0.5)
    fake_gh_cli.version("gh version 2.103.2\n", sleep=30)
    with pytest.raises(HeinzelError, match="did not answer"):
        gh().version()


def test_the_configured_gh_may_be_an_absolute_path(fake_gh_cli, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    fake_gh_cli.answer(PATH, stdout="[]")
    runner = gh(gh=str(fake_gh_cli.bin / "gh"))
    assert runner.executable() == str(fake_gh_cli.bin / "gh")
    assert runner.run(call(), host="github.com", timeout=10).returncode == 0


def test_a_gh_in_a_relative_path_entry_is_never_run(fake_gh_cli, monkeypatch):
    monkeypatch.chdir(fake_gh_cli.bin)
    monkeypatch.setenv("PATH", ".")
    assert gh().executable() is None


def test_the_login_comes_from_the_hosts_file_or_a_token_variable_gh_reads(fake_gh_cli, monkeypatch):
    assert gh().logged_in()
    assert not gh(host="ghe.example.com").logged_in()
    monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "made-up")
    assert gh(host="ghe.example.com").logged_in()
    fake_gh_cli.login()
    assert not gh().logged_in()
    monkeypatch.setenv("GITHUB_TOKEN", "made-up")
    assert gh().logged_in()
    assert fake_gh_cli.calls() == []  # no command, so none that prints a token


def test_without_a_hosts_file_there_is_no_login(no_gh_login):
    assert not gh().logged_in()


def test_without_gh_on_the_path_it_is_missing_and_says_where_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert gh().executable() is None
    with pytest.raises(HeinzelError, match=r"not on PATH; install it from https://cli\.github\.com"):
        gh().run(call(), host="github.com", timeout=10)


def test_a_gh_that_cannot_start_is_a_clear_error(tmp_path, monkeypatch, install_gh):
    install_gh(tmp_path, shebang="/nonexistent/interpreter")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    with pytest.raises(HeinzelError, match="Could not start the GitHub CLI"):
        gh().version()
