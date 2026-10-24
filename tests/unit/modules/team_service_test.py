"""Creating, joining and adding to a team repository (`team._service`), with a fake `git` and `gh`.

The fake runs no program: it records each command and answers like git and gh would, so these tests check which
commands run, in what order, what is written, and what a hostile repository can't do.
"""

import shutil
from datetime import date
from pathlib import Path

import pytest

from heinzel.kernel import HeinzelError, Participant, Person, Relation
from heinzel.modules.team import _template
from heinzel.modules.team._models import Member, TeamSettings
from heinzel.modules.team._repo import HARDENING, CommandFailed
from heinzel.modules.team._service import TeamService

pytestmark = pytest.mark.unit

TODAY = date(2026, 10, 24)
URL = "https://github.com/acme-example/hz-team-payments.git"


class Editor:
    """A ConfigEditor in memory."""

    def __init__(self, values: dict | None = None) -> None:
        self.values = dict(values or {})

    def write(self, key, value, scope="user"):
        self.values[key] = value
        return Path("config.toml")

    def unset(self, key, scope="user"):
        if key not in self.values:
            msg = f"{key} isn't set"
            raise HeinzelError(msg)
        del self.values[key]
        return Path("config.toml")


class Fake:
    """git and gh as far as the service uses them; git commands are recorded without the hardening options."""

    def __init__(self, remote: Path, login: str | None = "mira-example") -> None:
        self.remote = remote
        self.login = login
        self.origin: dict[str, str] = {}
        self.roster = "mira-example\nTheo-Example\n"
        self.commands: list[list[str]] = []
        self.fail: str | None = None
        self.status = ""

    def __call__(self, command, cwd):
        command = list(command)
        if command[0] == "git":
            assert command[1 : 1 + len(HARDENING)] == list(HARDENING)
            command = ["git", *command[1 + len(HARDENING) :]]
        self.commands.append(command)
        if self.fail and self.fail in " ".join(command):
            raise CommandFailed(command, f"fatal: {self.fail} refused\x1b[31m")
        return self._gh(command) if command[0] == "gh" else self._git(command)

    def _gh(self, command):
        match command:
            case ["gh", "api", "user", *_]:
                if self.login is None:
                    raise CommandFailed(command, "not signed in")
                return self.login + "\n"
            case ["gh", "api", "--paginate", *_]:
                return self.roster
            case ["gh", "repo", "create", repository, *_]:
                self.origin[command[command.index("--source") + 1]] = f"https://github.com/{repository}.git"
            case ["gh", "pr", "create", *_]:
                return "Creating pull request\nhttps://github.com/acme-example/hz-team-payments/pull/7\n"
        return ""

    def _git(self, command):
        match command:
            case ["git", "init", *_, directory]:
                Path(directory, ".git").mkdir(parents=True)
            case ["git", "clone", *_, url, directory]:
                shutil.copytree(self.remote, directory, symlinks=True)
                Path(directory, ".git").mkdir(exist_ok=True)
                self.origin[directory] = url
            case ["git", "-C", directory, "config", "--get", "remote.origin.url"]:
                if directory not in self.origin:
                    raise CommandFailed(command, "")
                return self.origin[directory] + "\n"
            case ["git", "-C", _, "symbolic-ref", *_]:
                return "origin/trunk\n"
            case ["git", "-C", _, "status", *_]:
                return self.status
        return ""

    def ran(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.commands if c[: len(prefix)] == list(prefix)]


def member(handle, login=None, roles=("engineer",), **fields) -> Member:
    return Member(handle=handle, github=login, roles=list(roles), since=TODAY, **fields)


@pytest.fixture
def remote(tmp_path) -> Path:
    """What the team repository holds: Mira's team payments, Theo on its roster, and Ana without a login."""
    mira = member("mira", "mira-example", ["engineering-manager"], owner=True)
    directory = tmp_path / "remote"
    files = _template.repository("payments", mira, [member("theo", "theo-example"), member("ana")])
    for relative, text in files.items():
        (directory / relative).parent.mkdir(parents=True, exist_ok=True)
        (directory / relative).write_text(text)
    return directory


def service(tmp_path, fake, editor=None, **settings) -> TeamService:
    return TeamService(TeamSettings(**settings), tmp_path / "data", editor or Editor(), run=fake, today=lambda: TODAY)


def joined(tmp_path, remote, login="theo-example", editor=None) -> tuple[TeamService, Fake, Path]:
    fake = Fake(remote, login=login)
    team = service(tmp_path, fake, editor)
    plan = team.join(URL, yes=True)
    assert plan.done
    assert plan.checkout is not None
    return team, fake, plan.checkout


# new ---------------------------------------------------------------------------------------------------------------


def test_new_without_a_remote_still_waits_for_yes_to_make_the_team_yours(tmp_path):
    editor = Editor()
    plan = service(tmp_path, Fake(tmp_path), editor).new("payments")
    assert not plan.done
    assert [a.what for a in plan.actions if a.confirm] == ["make it your team in your user configuration ([team])"]
    assert not (tmp_path / "data").exists()
    assert editor.values == {}


def test_new_without_a_remote_creates_the_repository_with_you_as_owner(tmp_path):
    editor = Editor()
    plan = service(tmp_path, Fake(tmp_path), editor).new("payments", yes=True)
    checkout = tmp_path / "data" / "team" / "payments"
    assert plan.done
    assert not any(action.remote for action in plan.actions)
    roster = (checkout / "team" / "roster.yaml").read_text()
    assert "handle: mira-example" in roster
    assert "owner: true" in roster
    assert "email" not in roster
    assert (checkout / "people" / "mira-example.yaml").is_file()
    assert "permissions: {}" in (checkout / ".github" / "workflows" / "hz-team.yml").read_text()
    assert editor.values == {"team.checkout": str(checkout)}
    assert plan.next == [
        (
            f"Push it once it has a home: gh repo create <owner>/payments --private --source {checkout} --push "
            f"&& hz team adopt {checkout}"
        )
    ]


def test_new_on_github_shows_the_remote_steps_and_does_nothing_without_yes(tmp_path):
    fake = Fake(tmp_path)
    plan = service(tmp_path, fake).new("payments", github="acme-example/hz-team-payments")
    assert not plan.done
    assert next(a.command for a in plan.actions if a.remote).startswith("gh repo create acme-example/hz-team-payments")
    assert [c[:2] for c in fake.commands] == [["gh", "api"]]
    assert not (tmp_path / "data").exists()
    assert plan.next[0].endswith("--role engineering-manager --yes")


def test_new_with_yes_creates_pushes_and_remembers_the_url(tmp_path):
    editor = Editor()
    fake = Fake(tmp_path)
    plan = service(tmp_path, fake, editor).new("payments", github="acme-example/hz-team-payments", yes=True)
    assert plan.done
    assert plan.repository == URL
    assert editor.values["team.repository"] == URL
    assert ["git", "-C", str(plan.checkout), "push", "--quiet", "-u", "origin", "main"] in fake.commands
    assert f"hz team join {URL}" in plan.next[0]
    assert "code-owner review" in plan.next[1]


def test_new_drafts_the_roster_from_a_github_team_for_review(tmp_path):
    plan = service(tmp_path, Fake(tmp_path)).new("payments", roster="github:acme-example/payments", yes=True)
    assert [(m.handle, m.github, m.roles) for m in plan.drafted] == [("theo-example", "Theo-Example", ["engineer"])]
    checkout = tmp_path / "data" / "team" / "payments"
    assert "handle: theo-example" in (checkout / "team" / "roster.yaml").read_text()
    assert "Theo-Example" not in (checkout / "CODEOWNERS").read_text()  # until they join: GitHub flags no-access owners
    assert "Review the drafted roster" in plan.next[0]


def test_a_drafted_login_that_would_take_your_handle_is_refused(tmp_path):
    with pytest.raises(HeinzelError, match="would get your handle theo-example"):
        service(tmp_path, Fake(tmp_path)).new("payments", handle="theo-example", roster="github:acme-example")


def test_a_roster_that_gh_cannot_read_says_which_command_to_run(tmp_path):
    fake = Fake(tmp_path)
    fake.fail = "orgs/acme-example/teams"
    with pytest.raises(HeinzelError, match=r"gh api --paginate orgs/acme-example/teams/payments/members") as failed:
        service(tmp_path, fake).new("payments", roster="github:acme-example/payments")
    assert "\x1b" not in str(failed.value)


def test_a_roster_with_something_that_is_no_login_is_refused(tmp_path):
    fake = Fake(tmp_path)
    fake.roster = "mira-example\n--evil\n"
    with pytest.raises(HeinzelError, match="isn't a GitHub login"):
        service(tmp_path, fake).new("payments", roster="github:acme-example/payments")


@pytest.mark.parametrize(
    ("arguments", "problem", "fix"),
    [
        ({"name": "Payments!"}, "isn't a team name", "hz team new payments"),
        ({"name": "payments", "github": "-oops/x"}, "isn't a GitHub repository", "hz team new payments --github"),
        ({"name": "payments", "roster": "gitlab:acme"}, "isn't a roster source", "hz team new payments --roster"),
        ({"name": "payments", "role": "boss"}, "isn't a role", "hz team new payments --role engineering-manager"),
        ({"name": "payments", "handle": "../../etc"}, "isn't a handle", "hz team new payments --handle mira-example"),
        ({"name": "payments", "login": "-x"}, "isn't a GitHub login", "hz team new payments --login"),
    ],
)
def test_new_refuses_invalid_arguments_with_a_command_that_would_work(tmp_path, arguments, problem, fix):
    with pytest.raises(HeinzelError, match=problem) as failed:
        service(tmp_path, Fake(tmp_path)).new(**arguments)
    assert fix in str(failed.value)


def test_new_without_any_login_asks_for_one(tmp_path):
    with pytest.raises(HeinzelError, match="--login"):
        service(tmp_path, Fake(tmp_path, login=None)).new("payments")


def test_new_refuses_a_second_team(tmp_path):
    with pytest.raises(HeinzelError, match="one team for now"):
        service(tmp_path, Fake(tmp_path), checkout=tmp_path).new("payments")


def test_new_refuses_an_existing_directory_or_link(tmp_path):
    (tmp_path / "data" / "team").mkdir(parents=True)
    (tmp_path / "data" / "team" / "payments").symlink_to(tmp_path / "nowhere")
    with pytest.raises(HeinzelError, match="hz team adopt"):
        service(tmp_path, Fake(tmp_path)).new("payments")


def test_a_failing_step_says_what_was_done_before_it_and_how_to_start_over(tmp_path):
    fake = Fake(tmp_path)
    fake.fail = "commit"
    checkout = tmp_path / "data" / "team" / "payments"
    with pytest.raises(HeinzelError, match=r"Couldn't commit them: fatal: commit refused.*Done before it: create") as e:
        service(tmp_path, fake).new("payments", yes=True)
    assert f"To recover: start over: rm -rf {checkout}" in str(e.value)


def test_a_failing_first_step_says_nothing_was_done(tmp_path):
    fake = Fake(tmp_path)
    fake.fail = "init"
    with pytest.raises(HeinzelError, match="Nothing was done before it"):
        service(tmp_path, fake).new("payments", yes=True)


# join --------------------------------------------------------------------------------------------------------------


def test_join_clones_and_finds_you_but_makes_the_team_yours_only_with_yes(tmp_path, remote):
    editor = Editor()
    fake = Fake(remote, login="Theo-Example")
    team = service(tmp_path, fake, editor)
    plan = team.join(URL)
    clone = fake.ran("git", "clone")[0]
    assert clone[-3:] == ["--", URL, str(plan.checkout)]
    assert "--no-recurse-submodules" in clone
    assert plan.member is not None
    assert plan.member.handle == "theo"
    assert not plan.done
    assert editor.values == {}
    assert plan.next == [f"The rest waits for your yes; to go ahead: hz team join {URL} --yes"]
    plan = team.join(URL, yes=True)  # the clone of the unconfirmed join is reused
    assert plan.done
    assert len(fake.ran("git", "clone")) == 1
    assert editor.values == {"team.checkout": str(plan.checkout), "team.repository": URL}
    assert not fake.ran("gh", "pr")


def test_joining_again_updates_the_checkout_and_asks_nothing(tmp_path, remote):
    team, fake, checkout = joined(tmp_path, remote)
    plan = team.join(URL.removesuffix(".git").replace("acme-example", "Acme-Example"))
    assert plan.done
    assert ["git", "-C", str(checkout), "pull", "--quiet", "--ff-only", "--no-recurse-submodules"] in fake.commands


def test_join_when_the_roster_does_not_list_you_suggests_proposing_and_names_candidates(tmp_path, remote):
    fake = Fake(remote, login="jonas-example")
    plan = service(tmp_path, fake).join(URL, yes=True)
    assert plan.done
    assert plan.member is None
    assert plan.identities == ["github:jonas-example"]
    assert plan.candidates == ["ana"]
    assert plan.next == [
        f"The roster of payments doesn't list github:jonas-example; ask to be added: hz team join {URL} --propose",
        f"If the roster lists you without a login: hz team join {URL} --propose --handle <ana>",
    ]


def test_the_suggested_commands_keep_the_login_you_gave(tmp_path, remote):
    plan = service(tmp_path, Fake(remote, login=None)).join(URL, login="jonas-example", yes=True)
    assert f"hz team join {URL} --login jonas-example --propose" in plan.next[0]


def test_propose_shows_the_pull_request_and_opens_it_with_yes(tmp_path, remote):
    fake = Fake(remote, login="jonas-example")
    team = service(tmp_path, fake)
    plan = team.join(URL, propose=True, role="product-manager")
    assert not plan.done
    assert [a.what for a in plan.actions if a.remote] == [
        "push hz/join-jonas-example",
        "open a pull request on acme-example/hz-team-payments",
    ]
    plan = team.join(URL, propose=True, role="product-manager", yes=True)
    assert plan.done
    assert plan.pull_request == "https://github.com/acme-example/hz-team-payments/pull/7"
    checkout = plan.checkout
    assert checkout is not None
    roster = (checkout / "team" / "roster.yaml").read_text()
    assert "handle: jonas-example" in roster
    assert "- product-manager" in roster
    assert (checkout / "people" / "jonas-example.yaml").is_file()
    assert "/people/jonas-example.yaml @jonas-example" in (checkout / "CODEOWNERS").read_text()
    switch = ["git", "-C", str(checkout), "switch", "--quiet", "-C", "hz/join-jonas-example", "origin/trunk"]
    assert switch in fake.commands
    assert fake.commands[-1] == ["git", "-C", str(checkout), "switch", "--quiet", "trunk"]


def test_propose_as_an_entry_without_a_login_gives_it_yours(tmp_path, remote):
    plan = service(tmp_path, Fake(remote, login="ana-example")).join(URL, propose=True, handle="ana", yes=True)
    assert plan.checkout is not None
    roster = (plan.checkout / "team" / "roster.yaml").read_text()
    assert roster.count("handle: ana") == 1
    assert "github: ana-example" in roster


def test_propose_as_an_entry_that_has_a_login_is_refused(tmp_path, remote):
    with pytest.raises(HeinzelError, match="has the login theo-example already"):
        service(tmp_path, Fake(remote, login="jonas-example")).join(URL, propose=True, handle="theo")


def test_a_failed_push_puts_the_checkout_back_and_says_an_owner_can_add_you(tmp_path, remote):
    fake = Fake(remote, login="jonas-example")
    fake.fail = "push --quiet"
    with pytest.raises(HeinzelError, match="an owner adds you: hz team add jonas-example --login jonas-example") as e:
        service(tmp_path, fake).join(URL, propose=True, yes=True)
    assert "Done before it: clone" in str(e.value)
    checkout = tmp_path / "data" / "team" / "hz-team-payments"
    assert fake.commands[-2:] == [
        ["git", "-C", str(checkout), "switch", "--quiet", "--discard-changes", "trunk"],
        ["git", "-C", str(checkout), "branch", "--quiet", "-D", "hz/join-jonas-example"],
    ]


def test_a_failed_pull_request_keeps_the_pushed_branch_and_says_how_to_open_it(tmp_path, remote):
    fake = Fake(remote, login="jonas-example")
    fake.fail = "pr create"
    with pytest.raises(
        HeinzelError,
        match="open its pull request: gh pr create --repo acme-example/hz-team-payments --head hz/join-jonas-example",
    ):
        service(tmp_path, fake).join(URL, propose=True, yes=True)
    assert fake.commands[-1][-2:] == ["--discard-changes", "trunk"]


def test_proposals_to_another_host_leave_the_branch_for_a_merge_request(tmp_path, remote):
    other = "ssh://git@git.example.invalid/acme/payments.git"
    fake = Fake(remote, login="jonas-example")
    plan = service(tmp_path, fake).join(other, propose=True, yes=True)
    assert plan.done
    assert plan.pull_request is None
    assert "leave hz/join-jonas-example for a merge request on " + other in [a.what for a in plan.actions]
    assert not fake.ran("gh", "pr")


def test_join_with_a_login_given_skips_asking_gh(tmp_path, remote):
    fake = Fake(remote, login=None)
    plan = service(tmp_path, fake).join(URL, login="theo-example", yes=True)
    assert plan.member is not None
    assert not fake.ran("gh", "api")


def test_propose_without_any_login_asks_who_you_are(tmp_path, remote):
    with pytest.raises(HeinzelError, match="--propose --login <login>"):
        service(tmp_path, Fake(remote, login=None)).join(URL, propose=True)


def test_propose_refuses_a_taken_handle(tmp_path, remote):
    team = service(tmp_path, Fake(remote, login="Theo"))
    with pytest.raises(HeinzelError, match="--propose --handle theo-2"):
        team.join(URL, propose=True)


def test_a_login_already_on_the_roster_under_another_handle_is_refused(tmp_path, remote):
    roster = remote / "team" / "roster.yaml"
    left = "- handle: old\n  github: jonas-example\n  roles:\n  - engineer\n  since: 2020-01-01\n  until: 2021-01-01\n"
    roster.write_text(roster.read_text() + left)
    with pytest.raises(HeinzelError, match="has the login jonas-example already, as old"):
        service(tmp_path, Fake(remote, login="jonas-example")).join(URL, propose=True)


def test_join_refuses_another_team_while_one_is_configured(tmp_path, remote):
    team = service(tmp_path, Fake(remote), checkout=tmp_path / "x", repository="https://github.com/acme/other.git")
    with pytest.raises(HeinzelError, match=r"hz system config unset team\.checkout && hz system config unset team"):
        team.join(URL)


def test_join_refuses_a_directory_that_holds_something_else(tmp_path, remote):
    (tmp_path / "data" / "team" / "hz-team-payments").mkdir(parents=True)
    with pytest.raises(HeinzelError, match="exists already"):
        service(tmp_path, Fake(remote)).join(URL)


@pytest.mark.parametrize(
    ("arguments", "problem"),
    [
        ({"url": "ext::sh -c id"}, "spaces"),
        ({"url": URL, "role": "chef"}, "isn't a role"),
        ({"url": URL, "login": "-x"}, "isn't a GitHub login"),
        ({"url": URL, "handle": "Theo"}, "isn't a handle"),
    ],
)
def test_join_refuses_invalid_arguments_before_running_anything(tmp_path, remote, arguments, problem):
    fake = Fake(remote)
    with pytest.raises(HeinzelError, match=problem):
        service(tmp_path, fake).join(**arguments)
    assert fake.commands == []


def test_a_repository_without_a_manifest_is_not_a_team_and_its_clone_is_removed(tmp_path, remote):
    (remote / "hz.yaml").unlink()
    with pytest.raises(HeinzelError, match=r"has no hz\.yaml"):
        service(tmp_path, Fake(remote)).join(URL)
    assert not (tmp_path / "data" / "team" / "hz-team-payments").exists()


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("kind: org\nname: platform\n", "kind"),
        ("name: ../../etc\n", "isn't a team name"),
        ("name: payments\nbindings: {tracker: evil}\n", "Extra inputs"),
        ("name: payments\nschedule: [weekly-digest]\n", "Extra inputs"),
        ("name: &a payments\nowners: [*a]\n", "anchors and aliases"),
        ("name: payments\nplugin: {name: evil}\n", "isn't a plugin distribution"),
    ],
)
def test_a_hostile_manifest_is_refused_and_applies_nothing(tmp_path, remote, text, problem):
    (remote / "hz.yaml").write_text(text)
    editor = Editor()
    fake = Fake(remote)
    with pytest.raises(HeinzelError, match=problem):
        service(tmp_path, fake, editor).join(URL, yes=True)
    assert editor.values == {}
    assert [c[:2] for c in fake.commands] == [["git", "clone"]]


def test_a_roster_entry_with_a_name_or_anything_else_is_refused(tmp_path, remote):
    roster = remote / "team" / "roster.yaml"
    roster.write_text(roster.read_text().replace("- handle: theo\n", "- handle: theo\n  name: Theo Example\n"))
    with pytest.raises(HeinzelError, match="Extra inputs"):
        service(tmp_path, Fake(remote)).join(URL)


def test_a_manifest_that_is_a_link_out_of_the_checkout_is_not_read(tmp_path, remote):
    secret = tmp_path / "secret.yaml"
    secret.write_text("name: stolen\n")
    (remote / "hz.yaml").unlink()
    (remote / "hz.yaml").symlink_to(secret)
    with pytest.raises(HeinzelError, match="symbolic link"):
        service(tmp_path, Fake(remote)).join(URL)


def test_a_people_directory_linked_elsewhere_is_never_written_through(tmp_path, remote):
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.rmtree(remote / "people")
    (remote / "people").symlink_to(outside)
    with pytest.raises(HeinzelError, match="symbolic link"):
        service(tmp_path, Fake(remote, login="jonas-example")).join(URL, propose=True, yes=True)
    assert list(outside.iterdir()) == []


def test_an_oversized_roster_is_refused(tmp_path, remote):
    (remote / "team" / "roster.yaml").write_bytes(b"x" * (300 * 1024))
    with pytest.raises(HeinzelError, match="larger than"):
        service(tmp_path, Fake(remote)).join(URL)


def test_a_roster_that_is_not_text_is_refused(tmp_path, remote):
    (remote / "team" / "roster.yaml").write_bytes(b"\xff\xfe")
    with pytest.raises(HeinzelError, match="isn't UTF-8"):
        service(tmp_path, Fake(remote)).join(URL)


def test_a_roster_that_is_a_directory_cannot_be_read(tmp_path, remote):
    (remote / "team" / "roster.yaml").unlink()
    (remote / "team" / "roster.yaml").mkdir()
    with pytest.raises(HeinzelError, match="Can't read"):
        service(tmp_path, Fake(remote)).join(URL)


def test_a_roster_with_a_handle_twice_is_invalid(tmp_path, remote):
    roster = remote / "team" / "roster.yaml"
    roster.write_text(roster.read_text() + "- handle: theo\n  roles: [engineer]\n  since: 2026-10-24\n")
    with pytest.raises(HeinzelError, match="more than once: theo"):
        service(tmp_path, Fake(remote)).join(URL)


# add ---------------------------------------------------------------------------------------------------------------


def test_an_owner_adds_an_owner_with_a_pull_request_and_codeowners_lists_them(tmp_path, remote):
    team, fake, checkout = joined(tmp_path, remote, login="mira-example")
    plan = team.add("priya", login="priya-example", role="principal", owner=True, yes=True)
    assert plan.done
    assert plan.pull_request
    assert ["git", "-C", str(checkout), "switch", "--quiet", "-C", "hz/add-priya", "origin/trunk"] in fake.commands
    roster = (checkout / "team" / "roster.yaml").read_text()
    assert "owner: true" in roster.split("handle: priya")[1]
    owners = (checkout / "CODEOWNERS").read_text()
    assert "/team/ @mira-example @priya-example" in owners
    assert "/people/priya.yaml" not in owners
    assert not (checkout / "people" / "priya.yaml").exists()


def test_add_without_yes_only_shows_the_steps(tmp_path, remote):
    team, fake, _ = joined(tmp_path, remote, login="mira-example")
    before = len(fake.commands)
    plan = team.add("priya")
    assert not plan.done
    assert plan.next[0].endswith("hz team add priya --role engineer --yes")
    assert not [c for c in fake.commands[before:] if {"fetch", "switch", "push", "pr"} & set(c)]


def test_add_says_who_you_are_with_by_when_gh_is_someone_else(tmp_path, remote):
    team, _, _ = joined(tmp_path, remote, login="theo-example")
    with pytest.raises(HeinzelError, match="ask one of: mira"):
        team.add("priya")
    plan = team.add("priya", by="mira-example")
    assert plan.next[0].endswith("hz team add priya --role engineer --by mira-example --yes")


def test_add_without_any_login_asks_who_you_are(tmp_path, remote):
    team, fake, _ = joined(tmp_path, remote, login="mira-example")
    fake.login = None
    with pytest.raises(HeinzelError, match="--by <your login>"):
        team.add("priya")


@pytest.mark.parametrize(
    ("arguments", "problem"),
    [
        ({"handle": "theo"}, "has a member theo already"),
        ({"handle": "priya", "login": "Theo-Example"}, "has the login Theo-Example already, as theo"),
        ({"handle": "priya", "role": "chef"}, "isn't a role"),
        ({"handle": "priya", "login": "-x"}, "isn't a GitHub login"),
        ({"handle": "priya", "by": "-x"}, "isn't a GitHub login"),
        ({"handle": "priya", "owner": True}, "--owner --login <login>"),
    ],
)
def test_add_refuses_what_would_not_work(tmp_path, remote, arguments, problem):
    team, _, _ = joined(tmp_path, remote, login="mira-example")
    with pytest.raises(HeinzelError, match=problem):
        team.add(**arguments)


def test_add_without_a_team_says_how_to_get_one(tmp_path, remote):
    with pytest.raises(HeinzelError, match="No team is configured"):
        service(tmp_path, Fake(remote)).add("priya")


def test_add_to_a_team_without_a_remote_commits_on_the_spot(tmp_path):
    team = service(tmp_path, Fake(tmp_path))
    team.new("payments", yes=True)
    plan = team.add("priya", login="priya-example")
    assert plan.done
    assert not any(action.remote for action in plan.actions)
    assert "handle: priya" in (tmp_path / "data" / "team" / "payments" / "team" / "roster.yaml").read_text()


def test_the_roster_changing_between_plan_and_write_is_caught_and_undone(tmp_path, remote):
    team, fake, checkout = joined(tmp_path, remote, login="mira-example")
    original = fake.__call__

    def racing(command, cwd):
        if "fetch" in command:
            roster = checkout / "team" / "roster.yaml"
            roster.write_text(roster.read_text() + "- handle: priya\n  roles: [engineer]\n  since: 2026-10-24\n")
        return original(command, cwd)

    team._run = racing
    with pytest.raises(HeinzelError, match="has a member priya already"):
        team.add("priya", yes=True)
    assert fake.commands[-1] == ["git", "-C", str(checkout), "branch", "--quiet", "-D", "hz/add-priya"]


# show and adopt ----------------------------------------------------------------------------------------------------


def test_show_without_a_team_says_how_to_get_one(tmp_path):
    shown = service(tmp_path, Fake(tmp_path)).show()
    assert shown.checkout is None
    assert shown.next[0] == "Join the team you were invited to: hz team join <url>"


def test_show_lists_active_members(tmp_path, remote):
    team, _, _ = joined(tmp_path, remote)
    shown = team.show()
    assert shown.name == "payments"
    assert [(m.handle, m.owner) for m in shown.members] == [("mira", True), ("theo", False), ("ana", False)]


def test_show_reports_a_broken_manifest(tmp_path, remote):
    team, _, checkout = joined(tmp_path, remote)
    (checkout / "hz.yaml").write_text("kind: [")
    assert team.show().problems[0].startswith("hz.yaml in")


def test_show_reports_a_missing_checkout_with_the_command_to_get_it_back(tmp_path):
    shown = service(tmp_path, Fake(tmp_path), repository=URL).show()
    assert shown.problems == [f"{tmp_path / 'data' / 'team' / 'hz-team-payments'} doesn't exist"]
    assert shown.next == [f"Clone it again: hz team join {URL}"]


def test_show_of_a_missing_checkout_without_a_url_says_how_to_leave(tmp_path):
    shown = service(tmp_path, Fake(tmp_path), checkout=tmp_path / "gone").show()
    assert shown.next == ["Leave it: hz system config unset team.checkout && hz system config unset team.repository"]


def test_left_members_are_not_shown(tmp_path, remote):
    roster = remote / "team" / "roster.yaml"
    mira, theo = roster.read_text().split("- handle: theo")
    left = theo.replace("since: '2026-10-24'", "since: '2026-10-24'\n  until: '2026-01-01'", 1)
    roster.write_text(mira + "- handle: theo" + left)
    team, _, _ = joined(tmp_path, remote, login="mira-example")
    assert [m.handle for m in team.show().members] == ["mira", "ana"]


def test_adopt_records_a_checkout_and_removes_the_deprecated_setting(tmp_path, remote):
    editor = Editor({"workflow.team_repository": str(remote)})
    (remote / ".git").mkdir()
    fake = Fake(remote)
    fake.origin[str(remote.resolve())] = URL
    adopted = service(tmp_path, fake, editor).adopt(remote)
    assert adopted.repository == URL
    assert editor.values == {"team.checkout": str(remote.resolve()), "team.repository": URL}
    assert adopted.written == ["set team.checkout", "set team.repository", "removed workflow.team_repository"]
    assert adopted.next == []


def test_adopt_of_a_legacy_checkout_says_what_is_left(tmp_path, remote):
    (remote / ".git").mkdir()
    (remote / "hz.yaml").unlink()
    adopted = service(tmp_path, Fake(remote), Editor()).adopt(remote)
    assert adopted.repository is None
    assert adopted.written == ["set team.checkout"]
    assert "HZ_WORKFLOW__TEAM_REPOSITORY" in adopted.next[0]
    assert "has no hz.yaml yet" in adopted.next[1]


def test_adopt_refuses_what_is_not_a_checkout(tmp_path, remote):
    with pytest.raises(HeinzelError, match="isn't a Git checkout"):
        service(tmp_path, Fake(remote)).adopt(remote)


def test_adopt_refuses_a_second_team(tmp_path, remote):
    (remote / ".git").mkdir()
    with pytest.raises(HeinzelError, match="one team for now"):
        service(tmp_path, Fake(remote), checkout=tmp_path / "other").adopt(remote)


def test_adopt_refuses_an_origin_hz_would_not_clone(tmp_path, remote):
    (remote / ".git").mkdir()
    fake = Fake(remote)
    fake.origin[str(remote.resolve())] = "ext::sh -c id"
    with pytest.raises(HeinzelError, match="remote set-url origin"):
        service(tmp_path, fake).adopt(remote)


def test_manifest_is_none_without_a_team(tmp_path, remote):
    assert service(tmp_path, Fake(remote)).manifest() is None


def test_manifest_is_the_joined_teams(tmp_path, remote):
    team, _, _ = joined(tmp_path, remote)
    manifest = team.manifest()
    assert manifest is not None
    assert manifest.name == "payments"


def test_a_checkout_with_uncommitted_changes_is_left_alone(tmp_path, remote):
    team, fake, _ = joined(tmp_path, remote, login="mira-example")
    fake.status = " M workflows/mine.prose.md\n"
    with pytest.raises(HeinzelError, match="has uncommitted changes") as failed:
        team.add("priya", yes=True)
    assert "stash" in str(failed.value)
    assert not fake.ran("git", "-C", str(tmp_path / "data" / "team" / "hz-team-payments"), "fetch")


def test_the_roster_commit_takes_only_the_roster_files(tmp_path, remote):
    team, fake, checkout = joined(tmp_path, remote, login="mira-example")
    team.add("priya", yes=True)
    [commit] = fake.ran("git", "-C", str(checkout), "commit")
    assert commit[-3:] == ["--", "team/roster.yaml", "CODEOWNERS"]


def test_claiming_an_entry_whose_login_turned_up_elsewhere_is_refused_and_undone(tmp_path, remote):
    fake = Fake(remote, login="ana-example")
    team = service(tmp_path, fake)
    checkout = tmp_path / "data" / "team" / "hz-team-payments"
    original = fake.__call__

    def racing(command, cwd):
        if "fetch" in command:
            roster = checkout / "team" / "roster.yaml"
            entry = "- handle: ana2\n  github: ana-example\n  roles: [engineer]\n  since: 2026-10-24\n"
            roster.write_text(roster.read_text() + entry)
        return original(command, cwd)

    team._run = racing
    with pytest.raises(HeinzelError, match="has the login ana-example already, as ana2"):
        team.join(URL, propose=True, handle="ana", yes=True)
    assert fake.commands[-1] == ["git", "-C", str(checkout), "branch", "--quiet", "-D", "hz/join-ana"]


def test_add_never_takes_over_an_entry_without_a_login(tmp_path, remote):
    team, _, _ = joined(tmp_path, remote, login="mira-example")
    with pytest.raises(HeinzelError, match="has a member ana already"):
        team.add("ana", login="ana-example", yes=True)


class Org:
    """A directory: Mira's reports are Theo (on the roster), Ana by e-mail, and Kim, guessed from 1:1s."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.asked: list[str] = []

    def person(self, person):
        self.asked.append(person)
        if self.fail:
            msg = "no calendar is bound"
            raise HeinzelError(msg)
        reports = [
            Relation(who=Participant(name="Theo", email="theo@acme.example"), login="theo-example"),
            Relation(who=Participant(name="Kim", email="kim@acme.example"), guess=True, evidence=["a weekly 1:1"]),
        ]
        return Person(who=Participant(name="Mira"), login=person, reports=reports)

    def reports(self, person):
        return []


def test_draft_adds_the_reports_the_roster_lacks_and_marks_guesses(tmp_path, remote):
    team, fake, checkout = joined(tmp_path, remote, login="mira-example")
    org = Org()
    plan = team.draft(org)
    assert org.asked == ["mira-example"]
    assert [(m.handle, m.github) for m in plan.drafted] == [("kim", None)]
    assert plan.guesses == ["kim: a weekly 1:1"]
    assert not plan.done
    plan = team.draft(org, yes=True)
    assert plan.done
    roster = (checkout / "team" / "roster.yaml").read_text()
    assert "email: kim@acme.example" in roster
    assert "Kim" not in roster
    assert ["git", "-C", str(checkout), "switch", "--quiet", "-C", "hz/draft-roster", "origin/trunk"] in fake.commands


def test_draft_with_nobody_new_says_the_roster_is_complete(tmp_path, remote):
    roster = remote / "team" / "roster.yaml"
    roster.write_text(
        roster.read_text() + "- handle: kim\n  email: kim@acme.example\n  roles: [engineer]\n  since: 2026-10-24\n"
    )
    team, _, _ = joined(tmp_path, remote, login="mira-example")
    plan = team.draft(Org())
    assert plan.done
    assert plan.next == ["The roster of payments has everyone the directory knows reports to mira-example."]


def test_draft_says_why_the_directory_could_not_be_read(tmp_path, remote):
    team, _, _ = joined(tmp_path, remote, login="mira-example")
    plan = team.draft(Org(fail=True))
    assert plan.next[0] == "The directory couldn't be read: no calendar is bound"
