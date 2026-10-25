"""What your team recommends beside what you have, and accepting it (`org._sync`), over the built-in modules.

The team is a directory with an `hz.yaml`: `sync` reads recommendations through the team module's `OrgContext` and
writes accepted ones through `system.config`'s `ConfigEditor` into the test's own user configuration.
"""

import tomllib
from pathlib import Path

import pytest
from rich.console import Console

from heinzel import Config, Credentials, HeinzelError, Module, Status
from heinzel.kernel import CAPABILITIES, user_config_dir
from heinzel.modules.heinzel._contracts import read_contract
from heinzel.modules.org import SyncService
from heinzel.modules.org._sync import SyncItem, SyncReport, render_sync

pytestmark = pytest.mark.unit

CONTRACT = Path(__file__).parents[2] / "fixtures" / "workflows" / "weekly-digest.prose.md"
MANIFEST = """\
name: payments
recommend:
  plugins: [heinzel-test, heinzel-google]
  bindings: {documents: google, tracker: github, code-host: github, chat: slack}
  credentials: [vault/api-token, google/oauth-client]
  contracts: [weekly-digest, monthly, gone]
"""
STALE = "monthly@sha256:" + "0" * 64


def _unused(*_args: object, **_kwargs: object) -> None:
    """Never called: the tests only list adapters."""


Fake = type(
    "Fake",
    (),
    {
        method: _unused
        for capability in ("documents", "tracker", "code-host")
        for method in CAPABILITIES[capability].methods
    },
)
"""An adapter of the capabilities the tests bind; nothing calls it."""


def vendors() -> Module:
    module = Module("vendors", "Keeps a token and provides test adapters.")
    module.credential("api-token", purpose="Token for the vault", service="vault")
    module.provides("documents", modes={"read"}, name="google")(Fake)
    module.provides("tracker", modes={"read"}, name="github")(Fake)
    module.provides("tracker", modes={"read"}, name="jira")(Fake)
    module.provides("code-host", modes={"read"}, name="github")(Fake)
    return module


@pytest.fixture
def team(tmp_path) -> Path:
    checkout = tmp_path / "team"
    (checkout / "workflows").mkdir(parents=True)
    (checkout / "hz.yaml").write_text(MANIFEST)
    (checkout / "workflows" / "weekly-digest.prose.md").write_bytes(CONTRACT.read_bytes())
    (checkout / "workflows" / "monthly.prose.md").write_text(CONTRACT.read_text().replace("weekly-digest", "monthly"))
    return checkout


@pytest.fixture
def make(make_kernel, team):
    def build(**data: object) -> SyncService:
        tables = {"team": {"checkout": str(team), "repository": "https://github.com/acme/payments.git"}} | data
        kernel = make_kernel(builtins=True, vendors=vendors(), config=Config(files=(), data=tables))
        return kernel.resolve(SyncService)

    return build


def pin(team: Path, name: str) -> str:
    """The pin workflow compares: its own SHA-256 of the contract file."""
    return f"{name}@{read_contract(team / 'workflows' / f'{name}.prose.md', 'team').sha256}"


def states(report) -> dict[str, str]:
    return {item.id: item.state for item in report.items}


def user_file() -> dict:
    path = user_config_dir() / "config.toml"
    return tomllib.loads(path.read_text()) if path.exists() else {}


def test_without_a_team_there_is_nothing_to_sync(make_kernel):
    report = make_kernel(builtins=True).resolve(SyncService).report()
    assert not report.team
    assert report.next == ["Join your team first: hz team join <url>"]


def test_each_recommendation_is_shown_beside_what_you_have(make, monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_VAULT_API_TOKEN", "x")
    report = make(capabilities={"tracker": "jira"}, workflow={"schedule_team": ["weekly-digest", STALE]}).report()
    assert states(report) == {
        "plugin:heinzel-test": "applied",
        "plugin:heinzel-google": "by-hand",
        "binding:documents": "pending",
        "binding:tracker": "differs",
        "binding:code-host": "pending",
        "binding:chat": "by-hand",
        "credential:vault/api-token": "applied",
        "credential:google/oauth-client": "by-hand",
        "contract:weekly-digest": "applied",
        "contract:monthly": "differs",
        "contract:gone": "missing",
    }


def test_each_recommendation_names_its_origin(make):
    report = make().report()
    assert report.items[0].origin == "team payments (https://github.com/acme/payments.git), hz.yaml"
    assert report.items[-1].origin.endswith("hz.yaml, workflows/gone.prose.md")


def test_next_accepts_the_pending_ones_and_says_how_to_decide_the_rest(make):
    report = make(capabilities={"tracker": "jira"}, workflow={"schedule_team": [STALE]}).report()
    assert (
        report.next[0] == "Accept them: hz org sync --accept binding:documents,binding:code-host,contract:weekly-digest"
    )
    assert "plugin:heinzel-google: install it: hz system plugin add heinzel-google" in report.next
    assert any(
        line.startswith("binding:chat: install the plugin that provides the slack adapter") for line in report.next
    )
    assert any("changed since you pinned it; read it first: hz heinzel show team:monthly" in x for x in report.next)
    assert (
        "credential:google/oauth-client: install the plugin that declares google/oauth-client first; "
        "hz system doctor then says how"
    ) in report.next


def test_nothing_is_written_until_you_accept(make):
    make().sync(None)
    assert user_file() == {}


def test_an_accepted_binding_is_written_and_next_says_what_is_left(make):
    report = make().sync("binding:documents")
    assert user_file()["capabilities"] == {"documents": "google"}
    item = next(i for i in report.items if i.id == "binding:documents")
    assert (item.state, item.applied_now) == ("applied", True)
    assert (
        report.next[0]
        == "Accept them: hz org sync --accept binding:tracker,binding:code-host,contract:weekly-digest,contract:monthly"
    )


def test_a_binding_of_a_dashed_capability_uses_the_configuration_key(make):
    make().sync("binding:code-host")
    assert user_file()["capabilities"] == {"code_host": "github"}


def test_accepted_contracts_are_all_pinned_to_the_content_workflow_compares(make, team):
    make(workflow={"schedule_team": ["other"]}).sync("contract:weekly-digest,contract:monthly")
    assert user_file()["heinzel"]["schedule_team"] == ["other", pin(team, "weekly-digest"), pin(team, "monthly")]


def test_all_takes_only_what_you_have_no_choice_for_yet(make, team):
    report = make(capabilities={"tracker": "jira"}, workflow={"schedule_team": [STALE]}).sync("all")
    assert user_file()["capabilities"] == {"documents": "google", "code_host": "github"}
    assert user_file()["heinzel"]["schedule_team"] == [STALE, pin(team, "weekly-digest")]
    assert states(report)["binding:tracker"] == "differs"
    assert states(report)["contract:monthly"] == "differs"


def test_what_you_chose_otherwise_switches_only_by_its_id(make, team):
    service = make(capabilities={"tracker": "jira"}, workflow={"schedule_team": [STALE]})
    service.sync("binding:tracker,contract:monthly")
    assert user_file()["capabilities"] == {"tracker": "github"}
    assert user_file()["heinzel"]["schedule_team"] == [pin(team, "monthly")]


def test_accepting_what_sync_cannot_apply_says_why(make):
    report = make().sync("plugin:heinzel-google,contract:gone,plugin:heinzel-test")
    assert report.next[:3] == [
        "plugin:heinzel-test: you have it already",
        "plugin:heinzel-google: not applied by sync; install it: hz system plugin add heinzel-google",
        "contract:gone: not applied by sync; the team repository has no workflows/gone.prose.md; tell its owners",
    ]
    assert user_file() == {}


def test_a_binding_whose_adapter_is_not_installed_is_never_written(make):
    report = make().sync("binding:chat")
    assert "capabilities" not in user_file()
    assert report.next[0].startswith("binding:chat: not applied by sync; install the plugin that provides the slack")


def test_an_unknown_id_names_the_recommendations_there_are(make):
    with pytest.raises(HeinzelError, match="Nothing recommends binding:mail to you; the recommendations: binding:chat"):
        make().sync("binding:mail")


def test_doctor_reports_drift_as_one_check(make):
    [check] = make(capabilities={"tracker": "jira"}).health()
    assert check.name == "recommendations"
    assert check.status is Status.DEGRADED
    assert check.fix == "hz org sync"
    assert "binding:documents" in check.detail
    assert "binding:tracker" not in check.detail  # a choice of yours, not drift


def test_doctor_is_quiet_about_a_team_you_follow(make, team):
    (team / "hz.yaml").write_text("name: payments\nrecommend:\n  plugins: [heinzel-test]\n")
    assert [(c.name, c.status) for c in make().health()] == [("recommendations", Status.UP)]


def test_doctor_says_nothing_without_a_team(make_kernel):
    assert make_kernel(builtins=True).resolve(SyncService).health() == []


def test_a_failing_comparison_is_degraded_not_fatal(make, monkeypatch):
    service = make()

    def broken():
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(service, "report", broken)
    [check] = service.health()
    assert (check.status, check.detail) == (Status.DEGRADED, "can't compare: boom")


def test_an_unreadable_keychain_says_so_for_credentials(make, monkeypatch):
    def locked(self, let):
        msg = "locked"
        raise HeinzelError(msg)

    monkeypatch.setattr(Credentials, "has", locked)
    item = next(i for i in make().report().items if i.id == "credential:vault/api-token")
    assert item.action.startswith("can't tell whether it is stored (locked)")


def test_the_report_renders_each_recommendation_as_text():
    item = SyncItem(
        id="binding:documents",
        kind="binding",
        name="documents",
        recommended="[bold]google[/bold]",
        current=None,
        state="applied",
        origin="team payments, hz.yaml",
        action="sets capabilities.documents",
        applied_now=True,
    )
    console = Console(width=200, record=True, color_system=None)
    console.print(render_sync(SyncReport(team=True, items=[item], next=["Done."])))
    shown = console.export_text()
    assert "Recommended by team payments" in shown
    assert "[bold]google[/bold]" in shown
    assert "applied (now)" in shown
    assert "sets capabilities.documents" in shown
    assert "Done." in shown


def test_the_report_without_recommendations_prints_what_to_do():
    console = Console(width=200, record=True, color_system=None)
    console.print(render_sync(SyncReport(team=False, items=[], next=["Join your team first."])))
    assert console.export_text().strip() == "Join your team first."


def layer_module(name: str, parent: str | None = None, **defaults: object) -> Module:
    module = Module(name, f"The {name} layer.")
    module.layer(parent=parent)
    module.playbook(f"How {name} works.", defaults=defaults, setup=False)
    return module


def test_your_layers_bindings_join_the_teams_which_win_for_the_same_capability(make_kernel, team):
    company = layer_module("acme", **{"capabilities.tracker": "jira", "capabilities.mail": "acme", "acme.zone": "x"})
    tables = {"team": {"checkout": str(team), "repository": "https://github.com/acme/payments.git"}}
    kernel = make_kernel(builtins=True, vendors=vendors(), acme=company, config=Config(files=(), data=tables))
    items = {item.id: item for item in kernel.resolve(SyncService).report().items}
    assert items["binding:tracker"].recommended == "github"  # the team repository's
    assert items["binding:mail"].origin == "layer acme (heinzel-test), its playbook"
    assert not any(item.name == "acme.zone" for item in items.values())  # a module setting is hz setup's


def test_layers_recommend_even_before_you_join_a_team(make_kernel):
    kernel = make_kernel(
        builtins=True, vendors=vendors(), acme=layer_module("acme", **{"capabilities.tracker": "jira"})
    )
    report = kernel.resolve(SyncService).report()
    assert report.team
    assert [(i.id, i.state) for i in report.items] == [("binding:tracker", "pending")]


def test_a_binding_that_isnt_a_name_is_left_out(make_kernel):
    kernel = make_kernel(builtins=True, acme=layer_module("acme", **{"capabilities.tracker": ["jira"]}))
    assert kernel.resolve(SyncService).report().items == []


def test_two_spellings_of_one_capability_are_one_recommendation_the_most_specific_layers(make_kernel):
    company = layer_module("acme", **{"capabilities.code_host": "github"})
    team = layer_module("acme-ops", "acme", **{"capabilities.code-host": "forge"})
    kernel = make_kernel(builtins=True, vendors=vendors(), acme=company, **{"acme-ops": team})
    items = kernel.resolve(SyncService).report().items
    assert [(i.id, i.recommended) for i in items] == [("binding:code-host", "forge")]
