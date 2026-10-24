"""What your team recommends beside what you have, and accepting it (`org._sync`), over the built-in modules.

The team is a directory with an `hz.yaml`: `sync` reads recommendations through the team module's `OrgContext` and
writes accepted ones through `system.config`'s `ConfigEditor` into the test's own user configuration.
"""

import hashlib
import tomllib
from pathlib import Path

import pytest
from rich.console import Console

from heinzel import Config, HeinzelError, Module, Status
from heinzel.kernel import user_config_dir
from heinzel.modules.org import SyncService
from heinzel.modules.org._sync import SyncItem, SyncReport, render_sync

pytestmark = pytest.mark.unit

CONTRACT = Path(__file__).parents[2] / "fixtures" / "workflows" / "weekly-digest.prose.md"
MANIFEST = """\
name: payments
recommend:
  plugins: [heinzel-test, heinzel-google]
  bindings: {documents: google, tracker: github, code-host: github}
  credentials: [vault/api-token, google/oauth-client]
  contracts: [weekly-digest, gone]
"""


def vault() -> Module:
    module = Module("vault", "Keeps a token.")
    module.credential("api-token", purpose="Token for the vault")
    return module


@pytest.fixture
def team(tmp_path) -> Path:
    checkout = tmp_path / "team"
    (checkout / "workflows").mkdir(parents=True)
    (checkout / "hz.yaml").write_text(MANIFEST)
    (checkout / "workflows" / "weekly-digest.prose.md").write_bytes(CONTRACT.read_bytes())
    return checkout


@pytest.fixture
def make(make_kernel, team):
    def build(**data: object) -> SyncService:
        tables = {"team": {"checkout": str(team), "repository": "https://github.com/acme/payments.git"}} | data
        kernel = make_kernel(builtins=True, vault=vault(), config=Config(files=(), data=tables))
        return kernel.resolve(SyncService)

    return build


def digest() -> str:
    return "sha256:" + hashlib.sha256(CONTRACT.read_bytes()).hexdigest()


def states(report) -> dict[str, str]:
    return {item.id: item.state for item in report.items}


def user_file() -> dict:
    path = user_config_dir() / "config.toml"
    return tomllib.loads(path.read_text()) if path.exists() else {}


def test_without_a_team_there_is_nothing_to_sync(make_kernel):
    report = make_kernel(builtins=True).resolve(SyncService).report()
    assert not report.team
    assert report.next == ["Join your team first: hz team join <url>"]


def test_each_recommendation_is_shown_beside_what_you_have_with_its_origin(make, monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_VAULT_API_TOKEN", "x")
    report = make(capabilities={"tracker": "jira"}, workflow={"schedule_team": ["weekly-digest"]}).report()
    assert states(report) == {
        "plugin:heinzel-test": "applied",
        "plugin:heinzel-google": "by-hand",
        "binding:documents": "pending",
        "binding:tracker": "differs",
        "binding:code-host": "pending",
        "credential:vault/api-token": "applied",
        "credential:google/oauth-client": "by-hand",
        "contract:weekly-digest": "applied",
        "contract:gone": "missing",
    }
    origin = report.items[0].origin
    assert origin == "team payments (https://github.com/acme/payments.git), hz.yaml"
    assert report.items[-1].origin.endswith("hz.yaml, workflows/gone.prose.md")
    assert report.next[0] == "Accept them: hz org sync --accept binding:documents,binding:tracker,binding:code-host"
    assert "hz system plugin add heinzel-google" in report.next
    assert "hz system credential set oauth-client --service google" in report.next


def test_nothing_is_written_until_you_accept(make):
    make().sync(None)
    assert user_file() == {}


def test_an_accepted_binding_is_written_to_your_user_file(make):
    report = make().sync("binding:documents")
    assert user_file()["capabilities"] == {"documents": "google"}
    item = next(i for i in report.items if i.id == "binding:documents")
    assert (item.state, item.applied_now) == ("applied", True)


def test_a_binding_of_a_dashed_capability_uses_the_configuration_key(make):
    make().sync("binding:code-host")
    assert user_file()["capabilities"] == {"code_host": "github"}


def test_an_accepted_contract_is_pinned_to_the_file_you_saw(make):
    make(workflow={"schedule_team": ["other"]}).sync("contract:weekly-digest")
    assert user_file()["workflow"]["schedule_team"] == ["other", f"weekly-digest@{digest()}"]


def test_a_contract_pinned_to_other_content_differs_and_accepting_repins_it(make):
    service = make(workflow={"schedule_team": ["weekly-digest@sha256:" + "0" * 64]})
    assert states(service.report())["contract:weekly-digest"] == "differs"
    service.sync("contract:weekly-digest")
    assert user_file()["workflow"]["schedule_team"] == [f"weekly-digest@{digest()}"]


def test_all_accepts_what_can_be_applied_and_leaves_the_rest_to_you(make):
    report = make().sync("all")
    assert set(user_file()["capabilities"]) == {"documents", "tracker", "code_host"}
    assert states(report)["plugin:heinzel-google"] == "by-hand"
    assert states(report)["contract:gone"] == "missing"
    assert report.next == [
        "hz system plugin add heinzel-google",
        "hz system credential set api-token --service vault",
        "hz system credential set oauth-client --service google",
    ]


def test_an_unknown_id_names_the_recommendations_there_are(make):
    with pytest.raises(HeinzelError, match="recommends no binding:chat; its recommendations: binding:code-host"):
        make().sync("binding:chat")


def test_doctor_reports_drift_with_the_command_that_follows_it(make):
    checks = {c.name: c for c in make(capabilities={"tracker": "jira"}).health()}
    drift = checks["recommendation binding:tracker"]
    assert drift.status is Status.DEGRADED
    assert drift.detail.startswith("your team recommends binding tracker → github; you have jira (team payments")
    assert drift.fix == "hz org sync --accept binding:tracker"
    assert checks["recommendation plugin:heinzel-google"].fix == "hz system plugin add heinzel-google"
    assert "recommendation contract:gone" not in checks


def test_doctor_is_quiet_about_a_team_you_follow(make, team):
    (team / "hz.yaml").write_text("name: payments\nrecommend:\n  plugins: [heinzel-test]\n")
    assert [(c.name, c.status) for c in make().health()] == [("recommendations", Status.UP)]


def test_an_invalid_manifest_recommends_nothing(make, team):
    (team / "hz.yaml").write_text("name: payments\nrecommend:\n  bindings: {telepathy: evil}\n")
    assert make().report().items == []


def test_the_report_renders_each_recommendation_as_text():
    console = Console(width=200, record=True, color_system=None)
    item = SyncItem(
        id="binding:documents",
        kind="binding",
        name="documents",
        recommended="[bold]google[/bold]",
        current=None,
        state="applied",
        origin="team payments",
        action="sets capabilities.documents",
        applied_now=True,
    )
    console.print(render_sync(SyncReport(team=True, items=[item], next=["Done."])))
    shown = console.export_text()
    assert "[bold]google[/bold]" in shown
    assert "applied (now)" in shown
    assert "Done." in shown
    console = Console(width=200, record=True, color_system=None)
    console.print(render_sync(SyncReport(team=False, items=[], next=["Join your team first."])))
    assert console.export_text().strip() == "Join your team first."
