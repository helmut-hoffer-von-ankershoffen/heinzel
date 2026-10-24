"""The real `Gh` runner starting a fake `gh` executable: arguments, environment, timeout, output cap. Offline."""

import json

import pytest
from heinzel_github import Gh, _gh

from heinzel import HeinzelError

pytestmark = pytest.mark.integration

PATH = "repos/acme/widgets/pulls"


def call(*extra: str) -> list[str]:
    return ["api", PATH, *extra]


def test_gh_gets_exactly_the_arguments_and_its_answer_comes_back(fake_gh_cli):
    fake_gh_cli.answer(PATH, fixture="pulls.json")
    result = Gh().run(call("--raw-field", "title=$(touch pwned); `id`"), host="github.com", timeout=10)
    assert result.returncode == 0
    assert [item["number"] for item in json.loads(result.stdout)] == [7, 6]
    assert fake_gh_cli.calls()[0]["argv"] == call("--raw-field", "title=$(touch pwned); `id`")
    assert not (fake_gh_cli.directory / "pwned").exists()


def test_gh_sees_the_host_and_no_credentials_or_debug_switches(fake_gh_cli, monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_GITHUB_GITHUB_TOKEN", "made-up-secret")
    monkeypatch.setenv("GH_DEBUG", "api")
    monkeypatch.setenv("GODEBUG", "http2debug=2")
    fake_gh_cli.answer(PATH, stdout="[]")
    Gh().run(call(), host="ghe.example.com", timeout=10)
    env = fake_gh_cli.calls()[0]["env"]
    assert env["GH_HOST"] == "ghe.example.com"
    assert env["GH_PROMPT_DISABLED"] == "1"
    assert "GH_DEBUG" not in env
    assert "GODEBUG" not in fake_gh_cli.calls()[0]["all_env"]
    assert "HZ_CREDENTIAL_GITHUB_GITHUB_TOKEN" not in env


def test_an_error_keeps_the_exit_code_stderr_and_body(fake_gh_cli):
    fake_gh_cli.answer(PATH, stdout='{"message": "Not Found"}', stderr="gh: Not Found (HTTP 404)\n", exit=1)
    result = Gh().run(call(), host="github.com", timeout=10)
    assert (result.returncode, result.stderr, result.stdout) == (
        1,
        "gh: Not Found (HTTP 404)\n",
        b'{"message": "Not Found"}',
    )


def test_stderr_is_capped(fake_gh_cli):
    fake_gh_cli.answer(PATH, stdout="[]", stderr="e" * (_gh.MAX_ERROR_BYTES + 10_000))
    result = Gh().run(call(), host="github.com", timeout=10)
    assert len(result.stderr) == _gh.MAX_ERROR_BYTES
    assert not result.truncated


def test_a_login_check_that_hangs_counts_as_not_logged_in(fake_gh_cli, monkeypatch):
    monkeypatch.setattr(_gh, "AUTH_TIMEOUT", 0.3)
    fake_gh_cli.routes["auth_sleep"] = 30
    fake_gh_cli.login("github.com")
    assert not Gh().logged_in("github.com")


def test_gh_is_stopped_at_the_timeout(fake_gh_cli):
    fake_gh_cli.answer(PATH, stdout="[]", sleep=30)
    result = Gh().run(call(), host="github.com", timeout=0.5)
    assert result.returncode is None


def test_gh_is_stopped_when_its_output_passes_the_cap(fake_gh_cli, monkeypatch):
    monkeypatch.setattr(_gh, "MAX_BYTES", 1000)
    fake_gh_cli.answer(PATH, stdout="[", pad=200_000, sleep=0)
    result = Gh().run(call(), host="github.com", timeout=10)
    assert result.truncated
    assert len(result.stdout) == 1001


def test_logged_in_reads_the_login_without_reading_the_token(fake_gh_cli):
    fake_gh_cli.login("github.com")
    assert Gh().logged_in("github.com")
    assert not Gh().logged_in("ghe.example.com")
    assert [c["argv"] for c in fake_gh_cli.calls()] == [
        ["auth", "token", "--hostname", "github.com"],
        ["auth", "token", "--hostname", "ghe.example.com"],
    ]


def test_without_gh_on_the_path_it_is_missing_and_says_where_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert Gh().executable() is None
    with pytest.raises(HeinzelError, match=r"not installed; install it from https://cli\.github\.com"):
        Gh().run(call(), host="github.com", timeout=10)
    with pytest.raises(HeinzelError, match="not installed"):
        Gh().logged_in("github.com")


def test_a_gh_that_cannot_start_is_a_clear_error(tmp_path, monkeypatch, install_gh):
    install_gh(tmp_path, shebang="/nonexistent/interpreter")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    with pytest.raises(HeinzelError, match="Could not start the GitHub CLI"):
        Gh().run(call(), host="github.com", timeout=10)
    assert not Gh().logged_in("github.com")
