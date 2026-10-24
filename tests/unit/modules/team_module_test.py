"""The team module as the kernel sees it: its `OrgContext`, health check, settings, models and how results show."""

from datetime import date
from pathlib import Path

import pytest
from rich.console import Console

from heinzel import Config, Status
from heinzel.kernel import OrgContext
from heinzel.modules import team
from heinzel.modules.team import TeamContext, TeamService, TeamSettings
from heinzel.modules.team._models import Action, Adopted, Manifest, Member, MemberView, Roster, TeamPlan, TeamShow
from heinzel.modules.team._render import render_adopted, render_plan, render_show
from heinzel.modules.team._template import with_owner

pytestmark = pytest.mark.unit

TODAY = date(2026, 10, 24)
URL = "https://github.com/acme/payments.git"


def text(renderable) -> str:
    console = Console(width=200, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


class NoEditor:
    def write(self, key, value, scope="user"):
        raise AssertionError(key)

    def unset(self, key, scope="user"):
        raise AssertionError(key)


def context(tmp_path, **settings) -> TeamContext:
    return TeamContext(TeamService(TeamSettings(**settings), tmp_path, NoEditor(), run=lambda *_: ""))


def test_the_context_has_no_checkout_without_a_team(tmp_path):
    assert context(tmp_path).team_checkout() is None


def test_the_context_names_the_configured_checkout(tmp_path):
    assert context(tmp_path, checkout=tmp_path / "x", repository=URL).team_checkout() == tmp_path / "x"


def test_the_checkout_of_a_repository_alone_is_under_the_data_directory(tmp_path):
    assert context(tmp_path, repository=URL).team_checkout() == tmp_path / "team" / "payments"


def test_the_kernel_injects_the_team_context_as_the_org_context(make_kernel, tmp_path):
    config = Config(files=(), data={"team": {"checkout": str(tmp_path)}})
    kernel = make_kernel(builtins=True, config=config)
    assert kernel.resolve(OrgContext).team_checkout() == tmp_path


def test_the_base_context_has_no_checkout_of_its_own():
    with pytest.raises(NotImplementedError):
        OrgContext().team_checkout()


def test_a_project_file_cannot_point_at_another_team():
    assert TeamSettings.ignored_in_project == {"repository", "checkout"}


def test_a_repository_url_in_the_settings_is_checked():
    with pytest.raises(ValueError, match="transport"):
        TeamSettings(repository="ext::sh")


def health(make_kernel, **table):
    kernel = make_kernel(builtins=True, config=Config(files=(), data={"team": table}))
    return kernel.call(team.health)


def test_health_is_silent_without_a_team(make_kernel):
    assert health(make_kernel) == []


def test_health_reports_a_missing_checkout_with_the_command_to_get_it_back(make_kernel, tmp_path):
    [check] = health(make_kernel, checkout=str(tmp_path / "gone"), repository=URL)
    assert check.status is Status.DOWN
    assert check.fix == f"Clone it again: hz team join {URL}"


def test_health_reports_a_broken_manifest(make_kernel, tmp_path):
    (tmp_path / "hz.yaml").write_text("kind: [")
    [check] = health(make_kernel, checkout=str(tmp_path))
    assert check.status is Status.DOWN
    assert check.fix.startswith("fix the file in a pull request")


def test_health_is_up_for_a_valid_team(make_kernel, tmp_path):
    (tmp_path / "hz.yaml").write_text("name: payments\n")
    [check] = health(make_kernel, checkout=str(tmp_path))
    assert (check.status, check.detail) == (Status.UP, "payments, 0 member(s)")


def test_the_roster_knows_its_owners_by_flag_and_by_role():
    roster = Roster(
        members=[
            Member(handle="mira", roles=["engineer"], owner=True, since=TODAY),
            Member(handle="priya", roles=["engineering-leadership"], since=TODAY),
            Member(handle="theo", roles=["engineer"], since=TODAY),
            Member(handle="old", roles=["engineering-manager"], since=TODAY, until=date(2020, 1, 1)),
        ]
    )
    assert [m.handle for m in roster.owners(Manifest(name="payments"), TODAY)] == ["mira", "priya"]


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ({"handle": "Theo"}, "isn't a handle"),
        ({"handle": "x" * 40}, "isn't a handle"),
        ({"github": "-x"}, "isn't a GitHub login"),
        ({"roles": ["Engineer"]}, "isn't a role"),
        ({"roles": []}, "at least 1"),
        ({"email": "not an address"}, "isn't an e-mail address"),
        ({"name": "Theo Example"}, "Extra inputs"),
        ({"manager": "mira"}, "Extra inputs"),
    ],
)
def test_roster_entries_hold_only_valid_handles_logins_roles_and_nothing_personal(data, problem):
    entry = {"handle": "theo", "roles": ["engineer"], "since": "2026-10-24"} | data
    with pytest.raises(ValueError, match=problem):
        Member.model_validate(entry)


def test_a_login_twice_in_the_roster_is_refused_whatever_its_case():
    entries = [
        {"handle": h, "github": g, "roles": ["engineer"], "since": "2026-10-24"} for h, g in [("a", "X"), ("b", "x")]
    ]
    with pytest.raises(ValueError, match="GitHub logins appear more than once: x"):
        Roster.model_validate({"members": entries})


def test_an_owner_is_added_to_every_line_the_owners_approve_once():
    owners = "# comment\n/team/ @mira\n/people/theo.yaml @theo\n"
    assert (
        with_owner(with_owner(owners, "priya"), "Priya") == "# comment\n/team/ @mira @priya\n/people/theo.yaml @theo\n"
    )


def test_a_plan_shows_each_step_its_command_and_what_waits():
    plan = TeamPlan(
        operation="join",
        team="payments",
        repository=None,
        checkout=Path("/x"),
        handle="theo",
        actions=[
            Action(what="clone [bold]it[/bold]", command="git clone -- url /x", remote=False, done=True),
            Action(what="push", command="git push", remote=True),
            Action(what="make it yours", remote=False, confirm=True),
        ],
        member=MemberView(handle="theo", github="theo-example", roles=["engineer"], owner=False, since=TODAY),
        drafted=[MemberView(handle="ana", github=None, roles=["engineer"], owner=False, since=TODAY)],
        plugin="heinzel-acme-payments",
        pull_request="https://github.com/acme/payments/pull/7",
        next=["Then this."],
    )
    shown = text(render_plan(plan))
    assert "hz team join: payments" in shown
    assert "Checkout: /x" in shown
    assert "done   clone [bold]it[/bold]" in shown
    assert "to do  push  (remote)" in shown
    assert "to do  make it yours  (waits for your yes)" in shown
    assert "$ git clone -- url /x" in shown
    assert "You are theo (engineer)." in shown
    assert "heinzel-acme-payments" in shown
    assert "ana" in shown
    assert "Pull request: https://github.com/acme/payments/pull/7" in shown
    assert "Then this." in shown


def test_show_renders_the_team_its_members_and_problems():
    show = TeamShow(
        repository=URL,
        checkout=Path("/x"),
        exists=False,
        name="payments",
        plugin="heinzel-acme-payments",
        members=[MemberView(handle="mira", github="mira-example", roles=["engineer"], owner=True, since=TODAY)],
        problems=["hz.yaml is [red]broken[/red]"],
        next=["Do this."],
    )
    shown = text(render_show(show))
    assert "/x (missing)" in shown
    assert "heinzel-acme-payments" in shown
    assert "mira-example" in shown
    assert "hz.yaml is [red]broken[/red]" in shown
    assert "Do this." in shown


def test_show_without_a_team_prints_only_how_to_get_one():
    shown = text(render_show(TeamShow(repository=None, checkout=None, exists=False, next=["hz team join <url>"])))
    assert shown.strip() == "hz team join <url>"


def test_adopt_says_what_it_did_and_what_is_left():
    adopted = Adopted(repository=None, checkout=Path("/x"), written=["set team.checkout"], next=["Add hz.yaml."])
    shown = text(render_adopted(adopted))
    assert "/x (no remote)" in shown
    assert "Set team.checkout." in shown
    assert "Add hz.yaml." in shown


@pytest.mark.parametrize(
    ("recommend", "problem"),
    [
        ({"plugins": ["evil"]}, "isn't a plugin distribution"),
        ({"bindings": {"telepathy": "x"}}, "isn't a capability"),
        ({"bindings": {"tracker": "Evil Adapter"}}, "isn't a name of an adapter"),
        ({"credentials": ["token"]}, "isn't a credential name"),
        ({"credentials": ["github/token=ghp_x"]}, "isn't a credential name"),
        ({"contracts": ["../x"]}, "isn't a contract name"),
        ({"schedule": ["x"]}, "Extra inputs"),
    ],
)
def test_a_manifest_recommends_only_names_it_can_check(recommend, problem):
    with pytest.raises(ValueError, match=problem):
        Manifest.model_validate({"name": "payments", "recommend": recommend})
