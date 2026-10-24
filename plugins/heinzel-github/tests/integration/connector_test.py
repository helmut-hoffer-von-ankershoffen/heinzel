"""The GitHub connector's sign-in, tools and live test through the CLI, with a fake `gh` on PATH. Offline."""

import json

import pytest
from typer.testing import CliRunner

from heinzel import Config, Kernel
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

BOUND = Config(files=(), data={"capabilities": {"code-host": "github", "tracker": "github"}})


def hz(kernel: Kernel, *args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    return result.exit_code, json.loads(result.output) if result.output.startswith("{") else {"output": result.output}


def github(report: dict) -> dict:
    return next(c for c in report["connectors"] if c["name"] == "github")


def test_signed_in_and_bound_it_is_connected_as_the_hosts_file_s_user(github_kernel, fake_gh_cli):
    code, report = hz(github_kernel(BOUND), "system", "connector", "list")
    assert code == 0, report
    connector = github(report)
    assert connector["state"] == "connected"
    assert connector["title"] == "GitHub"
    assert connector["login"] == {
        "signed_in": True,
        "account": "ada",
        "site": "github.com",
        "command": "gh auth login --hostname github.com",
    }
    assert {(t["capability"], t["method"], t["mode"]) for t in connector["tools"]} == {
        ("code-host", "merged_pull_requests", "read"),
        ("tracker", "issues_updated", "read"),
    }
    assert connector["connect"] == ["gh auth login --hostname github.com"]
    assert connector["bind"] == []
    assert connector["testable"] is True
    assert not fake_gh_cli.api_calls()  # listing stays offline


def test_without_a_login_it_is_not_signed_in_and_says_the_login_command(github_kernel, fake_gh_cli):
    fake_gh_cli.login()
    connector = github(hz(github_kernel(BOUND), "system", "connector", "list")[1])
    assert connector["state"] == "not-signed-in"
    assert connector["login"]["signed_in"] is False
    assert connector["connect"] == ["gh auth login --hostname github.com"]


def test_turned_off_it_is_not_bound_and_says_how_to_bind_it(github_kernel, fake_gh_cli):
    off = Config(files=(), data={"capabilities": {"code-host": "none", "tracker": "none"}})
    connector = github(hz(github_kernel(off), "system", "connector", "list")[1])
    assert connector["state"] == "not-bound"
    assert connector["bind"] == [
        "hz system config set capabilities.code_host github",
        "hz system config set capabilities.tracker github",
    ]


def test_the_live_test_asks_github_who_gh_is(github_kernel, fake_gh_cli):
    fake_gh_cli.answer("user", stdout=json.dumps({"login": "ada", "id": 1}))
    code, result = hz(github_kernel(BOUND), "system", "connector", "test", "github")
    assert code == 0, result
    assert result["passed"] is True
    assert result["login"]["account"] == "ada"
    assert result["latency_ms"] >= 0
    (call,) = fake_gh_cli.api_calls()
    assert call["argv"][:5] == ["api", "--hostname", "github.com", "--method", "GET"]
    assert call["argv"][-1] == "user"


def test_a_live_test_github_rejects_fails_with_the_login_command(github_kernel, fake_gh_cli):
    fake_gh_cli.answer(
        "user", stdout='{"message": "Bad credentials"}', stderr="gh: Bad credentials (HTTP 401)\n", exit=1
    )
    code, result = hz(github_kernel(BOUND), "system", "connector", "test", "github")
    assert code == 0, result
    assert result["passed"] is False
    (check,) = result["checks"]
    assert check["fix"] == "gh auth login --hostname github.com"
    assert result["login"]["signed_in"] is False


def test_doctor_does_not_run_the_live_test(github_kernel, fake_gh_cli):
    code, report = hz(github_kernel(BOUND), "system", "doctor")
    assert code == 0, report
    assert "gh api user" not in {c["name"] for c in report["checks"]["github"]}
    assert not fake_gh_cli.api_calls()
