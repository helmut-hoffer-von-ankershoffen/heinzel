"""The adapter bound through configuration, used by a requester through the CLI, with a fake `gh`. Offline."""

import json
import logging
from datetime import datetime

import pytest
from typer.testing import CliRunner

from heinzel import CodeHost, Config, Issue, Kernel, Module, PullRequest, Tracker, Window
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

BOUND = Config(files=(), data={"capabilities": {"code-host": "github", "tracker": "github"}})
SINCE, UNTIL = "2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00"
PULLS, ISSUES = "repos/acme/widgets/pulls", "repos/acme/widgets/issues"
LEAKED = "ghp_" + "Q7w8E9r0" * 5  # made up, shaped like a classic token


def digest() -> Module:
    """A test-only requester, as a workflow plugin would declare it: capabilities, never a vendor."""
    module = Module("digest", "Test-only digest of a repository.", requires=["code-host:read", "tracker:read"])

    @module.operation
    def merged(code_host: CodeHost, repository: str, since: str, until: str) -> list[PullRequest]:
        """List merged pull requests."""
        window = Window(since=datetime.fromisoformat(since), until=datetime.fromisoformat(until))
        return code_host.merged_pull_requests(repository, window)

    @module.operation
    def issues(tracker: Tracker, project: str, since: str, until: str) -> list[Issue]:
        """List updated issues."""
        window = Window(since=datetime.fromisoformat(since), until=datetime.fromisoformat(until))
        return tracker.issues_updated(project, window)

    return module


def hz(kernel: Kernel, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


@pytest.fixture
def github(fake_gh_cli):
    fake_gh_cli.answer(PULLS, fixture="pulls.json")
    fake_gh_cli.answer(ISSUES, fixture="issues.json")
    return fake_gh_cli


def test_a_requester_reads_merged_pull_requests_through_gh(github_kernel, github):
    code, pulls = hz(github_kernel(BOUND, digest=digest()), "digest", "merged", "acme/widgets", SINCE, UNTIL)
    assert code == 0, pulls
    assert pulls == [
        {
            "repository": "acme/widgets",
            "number": 7,
            "title": "Render widgets in dark mode",
            "url": "https://github.com/acme/widgets/pull/7",
            "author": "ada",
            "merged_at": "2026-10-20T16:30:00Z",
            "labels": ["ui"],
        }
    ]
    (call,) = github.calls()  # a short page, and the second pull request was updated before the window
    assert call["argv"][:3] == ["api", "--hostname", "github.com"]


def test_a_requester_reads_issues_through_gh_without_the_pull_requests_mixed_in(github_kernel, github):
    code, issues = hz(github_kernel(BOUND, digest=digest()), "digest", "issues", "acme/widgets", SINCE, UNTIL)
    assert code == 0, issues
    assert [(i["key"], i["assignee"], i["labels"]) for i in issues] == [("acme/widgets#3", "grace", ["bug"])]


def test_an_enterprise_host_is_passed_to_gh_as_hostname_and_gh_host(github_kernel, github, monkeypatch):
    monkeypatch.setenv("HZ_GITHUB__HOST", "ghe.example.com")
    assert hz(github_kernel(BOUND, digest=digest()), "digest", "issues", "acme/widgets", SINCE, UNTIL)[0] == 0
    (call,) = github.calls()
    assert call["argv"][:3] == ["api", "--hostname", "ghe.example.com"]
    assert call["env"]["GH_HOST"] == "ghe.example.com"


def test_not_logged_in_fails_the_command_with_gh_auth_login(github_kernel, fake_gh_cli):
    fake_gh_cli.answer(PULLS, stderr="To get started with GitHub CLI, please run:  gh auth login\n", exit=4)
    code, output = hz(github_kernel(BOUND, digest=digest()), "digest", "merged", "acme/widgets", SINCE, UNTIL)
    assert code != 0
    assert "gh auth login --hostname github.com" in str(output)


def test_an_error_from_github_is_shown_redacted_and_clean(github_kernel, fake_gh_cli, caplog):
    caplog.set_level(logging.DEBUG)
    body = json.dumps({"message": f"Bad credentials {LEAKED}\u202e\x1b[2J"})
    fake_gh_cli.answer(PULLS, stdout=body, stderr="gh: Bad credentials (HTTP 401)\n", exit=1)
    code, output = hz(github_kernel(BOUND, digest=digest()), "digest", "merged", "acme/widgets", SINCE, UNTIL)
    assert code != 0
    assert "Bad credentials ***" in str(output)
    for hidden in (LEAKED, "\u202e", "\x1b"):
        assert hidden not in str(output)
        assert hidden not in caplog.text


def test_without_gh_the_command_and_doctor_say_how_to_install_it(github_kernel, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    kernel = github_kernel(BOUND, digest=digest())
    code, output = hz(kernel, "digest", "merged", "acme/widgets", SINCE, UNTIL)
    assert code != 0
    assert "https://cli.github.com" in str(output)
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    gh = {c["name"]: c for c in report["checks"]["github"]}["gh"]
    assert gh["status"] == "degraded"
    assert gh["fix"] == "install gh from https://cli.github.com, then: gh auth login --hostname github.com"


def test_doctor_checks_the_login_and_bindings_without_contacting_github(github_kernel, fake_gh_cli):
    code, report = hz(github_kernel(BOUND), "system", "doctor")
    assert code == 0
    github = {c["name"]: c for c in report["checks"]["github"]}
    assert github["gh"]["status"] == "up"
    assert github["gh"]["detail"] == f"gh ({fake_gh_cli.bin / 'gh'}) is logged in to github.com; GitHub not contacted"
    assert github["code-host"]["detail"] == "served by github"
    assert [c["argv"][:2] for c in fake_gh_cli.calls()] == [["auth", "token"]]  # local only, never `gh api`
    assert "credential github/github-token" not in json.dumps(report)


def test_doctor_names_gh_auth_login_when_gh_has_no_login(github_kernel, fake_gh_cli):
    fake_gh_cli.login()
    code, report = hz(github_kernel(BOUND), "system", "doctor")
    assert code == 0
    gh = {c["name"]: c for c in report["checks"]["github"]}["gh"]
    assert (gh["status"], gh["fix"]) == ("degraded", "gh auth login --hostname github.com")


def test_unbound_the_requester_fails_and_doctor_names_the_binding(github_kernel, fake_gh_cli):
    kernel = github_kernel(digest=digest())
    code, output = hz(kernel, "digest", "merged", "acme/widgets", SINCE, UNTIL)
    assert code != 0
    assert "hz system config set capabilities.code_host github" in str(output)
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    capability = {c["name"]: c for c in report["checks"]["system.capability"]}
    assert capability["code-host:read for digest"]["status"] == "degraded"
    github = {c["name"]: c for c in report["checks"]["github"]}
    assert github["bindings"]["status"] == "degraded"
    assert not [c for c in fake_gh_cli.calls() if c["argv"][0] == "api"]


def test_a_leftover_api_url_fails_with_the_setting_that_replaced_it(github_kernel, fake_gh_cli):
    config = Config(files=(), data={**BOUND.data, "github": {"api_url": "https://ghe.example.com/api/v3"}})
    code, output = hz(github_kernel(config, digest=digest()), "digest", "issues", "acme/widgets", SINCE, UNTIL)
    assert code != 0
    assert "github.host" in str(output)
    assert not [c for c in fake_gh_cli.calls() if c["argv"][0] == "api"]


def test_capability_list_shows_the_adapter_with_the_api_host_gh_reaches(github_kernel, monkeypatch):
    code, report = hz(github_kernel(BOUND), "system", "capability", "list")
    assert code == 0
    assert {c["name"]: c["bound"] for c in report["capabilities"]}["tracker"] == "github"
    ours = sorted((a["capability"], a["modes"], a["hosts"]) for a in report["adapters"] if a["adapter"] == "github")
    assert ours == [("code-host", ["read"], ["api.github.com"]), ("tracker", ["read"], ["api.github.com"])]
    monkeypatch.setenv("HZ_GITHUB__HOST", "ghe.example.com")
    report = hz(github_kernel(BOUND), "system", "capability", "list")[1]
    assert {a["hosts"][0] for a in report["adapters"] if a["adapter"] == "github"} == {"ghe.example.com"}


def test_a_project_file_can_neither_bind_nor_redirect_gh(github_kernel, github, tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    (user / "config.toml").write_text('[capabilities]\ncode_host = "github"\ntracker = "github"\n', encoding="utf-8")
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user))
    project = tmp_path / "project"
    project.mkdir()
    (project / "hz.toml").write_text('[github]\nhost = "evil.example"\n', encoding="utf-8")
    kernel = github_kernel(Config.load(project), digest=digest())
    assert hz(kernel, "digest", "issues", "acme/widgets", SINCE, UNTIL)[0] == 0
    assert all(c["env"]["GH_HOST"] == "github.com" for c in github.calls())
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    names = [c["name"] for c in report["checks"]["system.doctor"]]
    assert "project setting github.host" in names
    assert "evil.example" not in json.dumps(report["checks"]["system.doctor"])
