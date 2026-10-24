"""The adapters bound by configuration, used by a requester through the CLI, with a fake Atlassian site. Offline."""

import base64
import json
from datetime import datetime

import pytest
from typer.testing import CliRunner

from heinzel import Config, Document, Documents, Issue, Kernel, Module, Tracker, Window
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

SITE = "https://acme.atlassian.net"
EMAIL = "ada@acme.example"
TOKEN = "fake-token-integration-token-never-shown"
BASIC = base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()
TOKEN_VARIABLE = "HZ_CREDENTIAL_ATLASSIAN_ATLASSIAN_TOKEN"
BOUND = Config(
    files=(),
    data={
        "capabilities": {"tracker": "atlassian", "documents": "atlassian"},
        "atlassian": {"site_url": SITE, "email": EMAIL},
    },
)
SINCE, UNTIL = "2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00"


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
def site(fake_atlassian, monkeypatch):
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    issue = fake_atlassian.issue
    fake_atlassian.search(
        [issue(3, updated="2026-10-05T12:00:00.000+0200", assignee="Grace Hopper", labels=("ui",))],
        [issue(4, updated="2026-10-06T00:00:00.000+0000", status="Done")],
    )
    fake_atlassian.page("123456", title="Widget plan", storage="<h1>Goal</h1><p>Ship.</p>", version=3)
    return fake_atlassian


def test_a_requester_reads_issues_through_the_bound_adapter(atlassian_kernel, site):
    code, issues = hz(atlassian_kernel(BOUND, digest=digest()), "digest", "issues", "WID", SINCE, UNTIL)
    assert code == 0, issues
    assert issues == [
        {
            "key": "WID-3",
            "title": "Issue 3",
            "url": f"{SITE}/browse/WID-3",
            "state": "To Do",
            "updated_at": "2026-10-05T10:00:00Z",
            "assignee": "Grace Hopper",
            "labels": ["ui"],
        },
        {
            "key": "WID-4",
            "title": "Issue 4",
            "url": f"{SITE}/browse/WID-4",
            "state": "Done",
            "updated_at": "2026-10-06T00:00:00Z",
            "assignee": None,
            "labels": [],
        },
    ]
    assert len(site.requests) == 2
    assert all(r.headers["Authorization"] == f"Basic {BASIC}" for r in site.requests)


def test_a_requester_reads_a_page_through_the_bound_adapter(atlassian_kernel, site):
    ref = f"{SITE}/wiki/spaces/WID/pages/123456/Widget+plan"
    code, document = hz(atlassian_kernel(BOUND, digest=digest()), "digest", "page", ref)
    assert code == 0, document
    assert document == {
        "ref": ref,
        "title": "Widget plan",
        "text": "# Goal\n\nShip.\n",
        "url": f"{SITE}/wiki/spaces/WID/pages/123456/Widget plan",
        "revision": "3",
    }


def test_a_page_on_another_site_fails_without_a_request(atlassian_kernel, site):
    code, output = hz(atlassian_kernel(BOUND, digest=digest()), "digest", "page", "https://evil.example/wiki/x")
    assert code != 0
    assert "not a page on https://acme.atlassian.net" in str(output)
    assert site.requests == []


def test_the_token_appears_in_no_output(atlassian_kernel, site):
    kernel = atlassian_kernel(BOUND, digest=digest())
    assert hz(kernel, "digest", "issues", "WID", SINCE, UNTIL)[0] == 0
    for command in (
        ("system", "doctor"),
        ("system", "info"),
        ("system", "capability", "list"),
        ("system", "credential", "list"),
        ("atlassian", "info"),
    ):
        code, output = hz(kernel, *command)
        assert code == 0, (command, output)
        text = json.dumps(output)
        assert TOKEN not in text, command
        assert BASIC not in text, command


def test_a_rejected_token_fails_the_command_with_the_fix_and_without_secrets(
    atlassian_kernel, fake_atlassian, monkeypatch
):
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    fake_atlassian.fail("/rest/api/3/search/jql", 401, {"errorMessages": [f"Bad credentials {TOKEN} {BASIC}"]})
    code, output = hz(atlassian_kernel(BOUND, digest=digest()), "digest", "issues", "WID", SINCE, UNTIL)
    assert code != 0
    assert "hz system credential set atlassian-token" in str(output)
    assert TOKEN not in str(output)
    assert BASIC not in str(output)


def test_a_rate_limit_fails_with_the_time_to_retry(atlassian_kernel, fake_atlassian, monkeypatch):
    monkeypatch.setenv(TOKEN_VARIABLE, TOKEN)
    fake_atlassian.fail("/rest/api/3/search/jql", 429, {}, {"retry-after": "12"})
    code, output = hz(atlassian_kernel(BOUND, digest=digest()), "digest", "issues", "WID", SINCE, UNTIL)
    assert code != 0
    assert "retry after 12 seconds" in str(output)


def test_without_a_token_the_command_names_the_fix_and_sends_nothing(atlassian_kernel, fake_atlassian):
    code, output = hz(atlassian_kernel(BOUND, digest=digest()), "digest", "issues", "WID", SINCE, UNTIL)
    assert code != 0
    assert "hz system credential set atlassian-token" in str(output)
    assert fake_atlassian.requests == []


def test_doctor_without_setup_is_degraded_and_offline(atlassian_kernel, fake_atlassian):
    kernel = atlassian_kernel(Config(files=(), data={"capabilities": {"tracker": "atlassian"}}))
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    checks = {c["name"]: c for c in report["checks"]["atlassian"]}
    assert checks["site"]["status"] == "degraded"
    assert "atlassian.site_url" in checks["site"]["fix"]
    assert "atlassian.email" in checks["site"]["fix"]
    assert checks["tracker"]["detail"] == "served by atlassian"
    assert checks["documents"]["detail"].startswith("not bound")
    credential = {c["name"]: c for c in report["checks"]["system.credential"]}
    token = credential["credential atlassian/atlassian-token"]
    assert token["status"] == "degraded"
    assert token["fix"] == "hz system credential set atlassian-token --service atlassian"
    assert fake_atlassian.requests == []


def test_doctor_when_set_up_is_up_and_offline(atlassian_kernel, site):
    code, report = hz(atlassian_kernel(BOUND), "system", "doctor")
    assert code == 0
    checks = {c["name"]: c for c in report["checks"]["atlassian"]}
    assert checks["site"] == {**checks["site"], "status": "up", "detail": f"{SITE}, with a token; not contacted"}
    assert site.requests == []


def test_unbound_the_requester_fails_and_doctor_names_the_binding(atlassian_kernel, fake_atlassian):
    kernel = atlassian_kernel(digest=digest())
    code, output = hz(kernel, "digest", "issues", "WID", SINCE, UNTIL)
    assert code != 0
    # Other installed tracker adapters (heinzel-github in this workspace) follow in the list.
    assert 'tracker = "atlassian" (installed: atlassian' in str(output)
    assert fake_atlassian.requests == []
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    ours = {c["name"]: c for c in report["checks"]["atlassian"]}
    assert ours["bindings"]["status"] == "degraded"


def test_capability_list_shows_the_adapter_and_its_hosts(atlassian_kernel):
    code, report = hz(atlassian_kernel(BOUND), "system", "capability", "list")
    assert code == 0
    assert {c["name"]: c["bound"] for c in report["capabilities"]}["documents"] == "atlassian"
    ours = sorted((a["capability"], a["modes"], a["hosts"]) for a in report["adapters"] if a["adapter"] == "atlassian")
    assert ours == [("documents", ["read"], ["*.atlassian.net"]), ("tracker", ["read"], ["*.atlassian.net"])]


def test_a_project_file_can_neither_bind_nor_redirect_the_token(atlassian_kernel, site, tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    (user / "config.toml").write_text(
        f'[capabilities]\ntracker = "atlassian"\ndocuments = "atlassian"\n\n'
        f'[atlassian]\nsite_url = "{SITE}"\nemail = "{EMAIL}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user))
    project = tmp_path / "project"
    project.mkdir()
    (project / "hz.toml").write_text(
        '[capabilities]\ntracker = "evil"\n\n'
        '[atlassian]\nsite_url = "https://evil.atlassian.net"\nemail = "mallory@evil.example"\n',
        encoding="utf-8",
    )
    kernel = atlassian_kernel(Config.load(project), digest=digest())
    assert hz(kernel, "digest", "issues", "WID", SINCE, UNTIL)[0] == 0
    assert hz(kernel, "digest", "page", "123456")[0] == 0
    assert site.requests
    assert all(r.url.startswith(f"{SITE}/") for r in site.requests)
    assert all(r.headers["Authorization"] == f"Basic {BASIC}" for r in site.requests)
    code, _ = hz(kernel, "digest", "page", "https://evil.atlassian.net/wiki/spaces/WID/pages/1")
    assert code != 0
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    names = [c["name"] for c in report["checks"]["system.doctor"]]
    assert "project setting atlassian.site_url" in names
    assert "project setting atlassian.email" in names
    leaked = json.dumps(report["checks"]["system.doctor"])
    assert "evil.atlassian.net" not in leaked
    assert "mallory" not in leaked


def test_a_project_file_alone_cannot_set_the_site_or_email(atlassian_kernel, site, tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    (user / "config.toml").write_text('[capabilities]\ntracker = "atlassian"\n', encoding="utf-8")
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user))
    project = tmp_path / "project"
    project.mkdir()
    (project / "hz.toml").write_text(
        '[atlassian]\nsite_url = "https://evil.atlassian.net"\nemail = "mallory@evil.example"\n', encoding="utf-8"
    )
    code, output = hz(atlassian_kernel(Config.load(project), digest=digest()), "digest", "issues", "WID", SINCE, UNTIL)
    assert code != 0
    assert "atlassian.site_url" in str(output)
    assert "atlassian.email" in str(output)
    assert site.requests == []
