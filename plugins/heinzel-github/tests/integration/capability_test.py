"""The adapter bound through configuration, used by a requester through the CLI, with a fake GitHub. Offline."""

import json
import logging
from datetime import datetime

import pytest
from typer.testing import CliRunner

from heinzel import CodeHost, Config, Issue, Kernel, Module, PullRequest, Tracker, Window
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

TOKEN = "ghp_integration-token-never-shown"
BOUND = Config(files=(), data={"capabilities": {"code-host": "github", "tracker": "github"}})
SINCE, UNTIL = "2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00"


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
def github(fake_github):
    fake_github.serve(
        "/repos/acme/widgets/pulls",
        [fake_github.pull(7, updated_at="2026-10-21T00:00:00Z", merged_at="2026-10-20T00:00:00Z", labels=("ui",))],
        [fake_github.pull(6, updated_at="2026-09-01T00:00:00Z", merged_at="2026-09-01T00:00:00Z")],
    )
    fake_github.serve(
        "/repos/acme/widgets/issues",
        [
            fake_github.issue(3, updated_at="2026-10-05T00:00:00Z", assignee="grace"),
            fake_github.issue(4, updated_at="2026-10-06T00:00:00Z", pull_request=True),
        ],
    )
    return fake_github


def test_a_requester_reads_merged_pull_requests_through_the_bound_adapter(github_kernel, github):
    code, pulls = hz(github_kernel(BOUND, digest=digest()), "digest", "merged", "acme/widgets", SINCE, UNTIL)
    assert code == 0, pulls
    assert pulls == [
        {
            "repository": "acme/widgets",
            "number": 7,
            "title": "Pull 7",
            "url": "https://github.com/acme/widgets/pull/7",
            "author": "ada",
            "merged_at": "2026-10-20T00:00:00Z",
            "labels": ["ui"],
        }
    ]
    assert len(github.requests) == 2


def test_a_requester_reads_issues_through_the_bound_adapter(github_kernel, github):
    code, issues = hz(github_kernel(BOUND, digest=digest()), "digest", "issues", "acme/widgets", SINCE, UNTIL)
    assert code == 0, issues
    assert [(i["key"], i["assignee"]) for i in issues] == [("acme/widgets#3", "grace")]


def test_the_token_is_sent_to_github_and_appears_in_no_output(github_kernel, github, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HZ_CREDENTIAL_GITHUB_GITHUB_TOKEN", TOKEN)
    kernel = github_kernel(BOUND, digest=digest())
    assert hz(kernel, "digest", "issues", "acme/widgets", SINCE, UNTIL)[0] == 0
    assert github.requests[0].headers["Authorization"] == f"Bearer {TOKEN}"
    for command in (("system", "doctor"), ("system", "info"), ("system", "capability", "list"), ("github", "info")):
        code, output = hz(kernel, *command)
        assert code == 0, command
        assert TOKEN not in json.dumps(output), command
    assert TOKEN not in caplog.text


def test_a_rejected_token_fails_the_command_with_the_fix_and_without_the_token(
    github_kernel, fake_github, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HZ_CREDENTIAL_GITHUB_GITHUB_TOKEN", TOKEN)
    fake_github.fail("/repos/acme/widgets/pulls", 401, {"message": f"Bad credentials {TOKEN}"})
    code, output = hz(github_kernel(BOUND, digest=digest()), "digest", "merged", "acme/widgets", SINCE, UNTIL)
    assert code != 0
    assert "hz system credential set github-token --service github" in str(output)
    assert TOKEN not in str(output)
    assert TOKEN not in caplog.text


def test_a_token_from_the_environment_is_stripped_of_a_trailing_line_break(github_kernel, github, monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_GITHUB_GITHUB_TOKEN", "tok\r\n")
    code, issues = hz(github_kernel(BOUND, digest=digest()), "digest", "issues", "acme/widgets", SINCE, UNTIL)
    assert code == 0, issues
    assert [r.headers["Authorization"] for r in github.requests] == ["Bearer tok"]


def test_a_token_with_a_line_break_inside_fails_with_the_fix_and_never_shows_it(
    github_kernel, github, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    secret = "ghp_first\r\nX-Injected: second"
    monkeypatch.setenv("HZ_CREDENTIAL_GITHUB_GITHUB_TOKEN", secret)
    code, output = hz(github_kernel(BOUND, digest=digest()), "digest", "merged", "acme/widgets", SINCE, UNTIL)
    assert code != 0
    assert "hz system credential set github-token --service github" in str(output)
    for part in ("ghp_first", "X-Injected", "second"):
        assert part not in str(output)
        assert part not in caplog.text
    assert github.requests == []


def test_unbound_the_requester_fails_and_doctor_names_the_binding(github_kernel, fake_github):
    kernel = github_kernel(digest=digest())
    code, output = hz(kernel, "digest", "merged", "acme/widgets", SINCE, UNTIL)
    assert code != 0
    # The CLI head renders errors as Rich markup, which swallows the `[capabilities]` table name (a core issue).
    assert 'code_host = "github" (installed: github)' in str(output)
    assert fake_github.requests == []
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    capability = {c["name"]: c for c in report["checks"]["system.capability"]}
    assert capability["code-host:read for digest"]["status"] == "degraded"
    github = {c["name"]: c for c in report["checks"]["github"]}
    assert github["bindings"]["status"] == "degraded"


def test_doctor_reports_the_bindings_and_a_missing_token_without_contacting_github(github_kernel, fake_github):
    code, report = hz(github_kernel(BOUND), "system", "doctor")
    assert code == 0
    github = {c["name"]: c for c in report["checks"]["github"]}
    assert github["code-host"]["detail"] == "served by github"
    assert github["tracker"]["detail"] == "served by github"
    assert "anonymously" in github["api"]["detail"]
    credential = {c["name"]: c for c in report["checks"]["system.credential"]}
    token = credential["credential github/github-token"]
    assert token["status"] == "degraded"
    assert token["fix"] == "hz system credential set github-token --service github"
    assert fake_github.requests == []


def test_capability_list_shows_the_adapter_with_the_host_of_its_api(github_kernel):
    code, report = hz(github_kernel(BOUND), "system", "capability", "list")
    assert code == 0
    assert {c["name"]: c["bound"] for c in report["capabilities"]}["tracker"] == "github"
    ours = sorted((a["capability"], a["modes"], a["hosts"]) for a in report["adapters"] if a["adapter"] == "github")
    assert ours == [("code-host", ["read"], ["api.github.com"]), ("tracker", ["read"], ["api.github.com"])]


def test_a_project_file_can_neither_bind_nor_redirect_the_token(github_kernel, github, tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    (user / "config.toml").write_text('[capabilities]\ncode_host = "github"\ntracker = "github"\n', encoding="utf-8")
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user))
    project = tmp_path / "project"
    project.mkdir()
    (project / "hz.toml").write_text('[github]\napi_url = "https://evil.example/api"\n', encoding="utf-8")
    monkeypatch.setenv("HZ_CREDENTIAL_GITHUB_GITHUB_TOKEN", TOKEN)
    kernel = github_kernel(Config.load(project), digest=digest())
    assert hz(kernel, "digest", "issues", "acme/widgets", SINCE, UNTIL)[0] == 0
    assert all(r.url.startswith("https://api.github.com/") for r in github.requests)
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    names = [c["name"] for c in report["checks"]["system.doctor"]]
    assert "project setting github.api_url" in names
    assert "evil.example" not in json.dumps(report["checks"]["system.doctor"])
