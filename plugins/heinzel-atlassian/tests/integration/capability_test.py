"""The adapters bound by configuration, used by a requester through the CLI, with the fake `acli` on PATH. Offline."""

import json
from datetime import datetime

import pytest
from typer.testing import CliRunner

from heinzel import Config, Document, Documents, Issue, Kernel, Module, Tracker, Window
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

SITE = "https://acme.atlassian.net"
BOUND = Config(
    files=(),
    data={"capabilities": {"tracker": "atlassian", "documents": "atlassian"}, "atlassian": {"site_url": SITE}},
)
SINCE, UNTIL = "2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00"
LEAKED = "ATATT3xFfGF0leaked-token-from-acli-never-shown"


def digest() -> Module:
    """A test-only requester, as a workflow plugin would declare it: capabilities, never a vendor."""
    module = Module("digest", "Test-only digest of a project.", requires=["tracker:read", "documents:read"])

    @module.operation
    def issues(tracker: Tracker, project: str, since: str, until: str) -> list[Issue]:
        """List updated issues."""
        window = Window(since=datetime.fromisoformat(since), until=datetime.fromisoformat(until))
        return tracker.issues_updated(project, window)

    @module.operation
    def page(documents: Documents, ref: str) -> Document:
        """Read a page."""
        return documents.read(ref)

    return module


def hz(kernel: Kernel, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


@pytest.fixture
def site(acli_on_path):
    acli_on_path.search(acli_on_path.sample("jira_workitem_search.json"))
    acli_on_path.answer(acli_on_path.PAGE, acli_on_path.sample("confluence_page_view.json"))
    return acli_on_path


def test_a_requester_reads_issues_through_the_bound_adapter(atlassian_kernel, site):
    code, issues = hz(atlassian_kernel(BOUND, digest=digest()), "digest", "issues", "WID", SINCE, UNTIL)
    assert code == 0, issues
    assert issues == [
        {
            "key": "WID-3",
            "title": "Widget export drops the last row",
            "url": f"{SITE}/browse/WID-3",
            "state": "In Progress",
            "updated_at": "2026-10-05T10:00:00Z",
            "assignee": "Wilma Widget",
            "labels": ["export", "ui"],
        },
        {
            "key": "WID-4",
            "title": "Document the widget API",
            "url": f"{SITE}/browse/WID-4",
            "state": "Done",
            "updated_at": "2026-10-06T00:00:00Z",
            "assignee": None,
            "labels": [],
        },
    ]
    (call,) = site.calls
    assert call.args[:4] == ["jira", "workitem", "search", "--jql"]


def test_a_requester_reads_a_page_through_the_bound_adapter(atlassian_kernel, site):
    ref = f"{SITE}/wiki/spaces/WID/pages/123456/Widget+plan"
    code, document = hz(atlassian_kernel(BOUND, digest=digest()), "digest", "page", ref)
    assert code == 0, document
    assert document == {
        "ref": ref,
        "title": "Widget plan",
        "text": "# Goal\n\nShip the widget.\n",
        "url": f"{SITE}/wiki/spaces/WID/pages/123456/Widget+plan",
        "revision": "3",
    }
    (call,) = site.calls
    assert call.args == ["confluence", "page", "view", "--id", "123456", "--body-format", "storage", "--json"]


def test_a_page_on_another_site_fails_without_running_acli(atlassian_kernel, site):
    code, output = hz(atlassian_kernel(BOUND, digest=digest()), "digest", "page", "https://evil.example/wiki/x")
    assert code != 0
    assert "not a page on https://acme.atlassian.net" in str(output)
    assert site.calls == []


def test_logged_out_acli_fails_the_command_with_the_login(atlassian_kernel, acli_on_path):
    acli_on_path.fail(acli_on_path.SEARCH, acli_on_path.UNAUTHORIZED)
    code, output = hz(atlassian_kernel(BOUND, digest=digest()), "digest", "issues", "WID", SINCE, UNTIL)
    assert code != 0
    assert "acli jira auth login" in str(output)


def test_a_token_acli_prints_appears_in_no_output(atlassian_kernel, acli_on_path):
    acli_on_path.fail(acli_on_path.SEARCH, f"Error: 401 for token {LEAKED} and Bearer {LEAKED}\n")
    code, output = hz(atlassian_kernel(BOUND, digest=digest()), "digest", "issues", "WID", SINCE, UNTIL)
    assert code != 0
    assert "acli jira auth login" in str(output)
    assert "leaked-token" not in str(output)


@pytest.mark.usefixtures("no_acli")
def test_without_acli_the_command_names_the_install_and_the_login(atlassian_kernel):
    code, output = hz(atlassian_kernel(BOUND, digest=digest()), "digest", "issues", "WID", SINCE, UNTIL)
    assert code != 0
    assert "not on PATH" in str(output)
    assert "acli jira auth login" in str(output)


@pytest.mark.usefixtures("no_acli")
def test_doctor_without_acli_or_site_is_degraded_and_offline(atlassian_kernel):
    kernel = atlassian_kernel(Config(files=(), data={"capabilities": {"tracker": "atlassian"}}))
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    checks = {c["name"]: c for c in report["checks"]["atlassian"]}
    assert checks["acli"]["status"] == "degraded"
    assert "acli jira auth login" in checks["acli"]["fix"]
    assert checks["site"]["status"] == "degraded"
    assert "atlassian.site_url" in checks["site"]["fix"]
    assert checks["tracker"]["detail"] == "served by atlassian"
    assert checks["documents"]["detail"].startswith("not bound")
    credentials = {c["name"] for c in report["checks"].get("system.credential", [])}
    assert not [name for name in credentials if "atlassian" in name]


def test_doctor_when_set_up_is_up_and_never_runs_acli(atlassian_kernel, site):
    code, report = hz(atlassian_kernel(BOUND), "system", "doctor")
    assert code == 0
    checks = {c["name"]: c for c in report["checks"]["atlassian"]}
    assert checks["acli"]["status"] == "up"
    assert "acli jira auth login" in checks["acli"]["detail"]
    assert checks["site"] == {**checks["site"], "status": "up", "detail": f"{SITE}; not contacted"}
    assert site.calls == []


def test_unbound_the_requester_fails_and_doctor_names_the_binding(atlassian_kernel, acli_on_path):
    kernel = atlassian_kernel(digest=digest())
    code, output = hz(kernel, "digest", "issues", "WID", SINCE, UNTIL)
    assert code != 0
    assert "hz system config set capabilities.tracker atlassian" in str(output)
    assert acli_on_path.calls == []
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    ours = {c["name"]: c for c in report["checks"]["atlassian"]}
    assert ours["bindings"]["status"] == "degraded"


def test_capability_list_shows_the_adapter_and_its_hosts(atlassian_kernel):
    code, report = hz(atlassian_kernel(BOUND), "system", "capability", "list")
    assert code == 0
    assert {c["name"]: c["bound"] for c in report["capabilities"]}["documents"] == "atlassian"
    ours = sorted((a["capability"], a["modes"], a["hosts"]) for a in report["adapters"] if a["adapter"] == "atlassian")
    hosts = ["acme.atlassian.net", "api.atlassian.com"]
    assert ours == [("documents", ["read"], hosts), ("tracker", ["read"], hosts)]


def test_a_project_file_can_neither_bind_nor_move_the_site(atlassian_kernel, site, tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    (user / "config.toml").write_text(
        f'[capabilities]\ntracker = "atlassian"\ndocuments = "atlassian"\n\n[atlassian]\nsite_url = "{SITE}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user))
    project = tmp_path / "project"
    project.mkdir()
    (project / "hz.toml").write_text(
        '[capabilities]\ntracker = "evil"\n\n[atlassian]\nsite_url = "https://evil.atlassian.net"\n',
        encoding="utf-8",
    )
    kernel = atlassian_kernel(Config.load(project), digest=digest())
    code, issues = hz(kernel, "digest", "issues", "WID", SINCE, UNTIL)
    assert code == 0
    assert all(i["url"].startswith(f"{SITE}/") for i in issues)  # type: ignore[union-attr,index] # JSON
    code, _ = hz(kernel, "digest", "page", "https://evil.atlassian.net/wiki/spaces/WID/pages/1")
    assert code != 0
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    names = [c["name"] for c in report["checks"]["system.doctor"]]
    assert "project setting atlassian.site_url" in names
    assert "evil.atlassian.net" not in json.dumps(report["checks"]["system.doctor"])


def test_a_project_file_alone_cannot_set_the_site(atlassian_kernel, site, tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    (user / "config.toml").write_text('[capabilities]\ntracker = "atlassian"\n', encoding="utf-8")
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user))
    project = tmp_path / "project"
    project.mkdir()
    (project / "hz.toml").write_text('[atlassian]\nsite_url = "https://evil.atlassian.net"\n', encoding="utf-8")
    code, output = hz(atlassian_kernel(Config.load(project), digest=digest()), "digest", "issues", "WID", SINCE, UNTIL)
    assert code != 0
    assert "atlassian.site_url" in str(output)
    assert site.calls == []
