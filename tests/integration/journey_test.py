"""`hz setup` as one journey and `hz join` as its front door, through the CLI head (ADR 0032, amended). Offline.

The team module is replaced by a test module with the same `join` operation, so no repository is cloned; the identity
step is the real org step with discovery faked.
"""

import json
import tomllib

import pytest
from pydantic import BaseModel, Field
from typer.testing import CliRunner

from heinzel import Config, ConfigEditor, HeinzelError, Kernel, Module, ModuleRecord, ModuleSettings, Prompter
from heinzel.heads.cli import _setup, build
from heinzel.kernel import Origin, ScriptedPrompter, discover, user_config_dir
from heinzel.modules.org import _discovery as discovery
from heinzel.modules.org._discovery import Discovered, Login

pytestmark = pytest.mark.integration

runner = CliRunner()
URL = "https://github.com/acme-example/hz-team-payments.git"


class NoteSettings(ModuleSettings):
    """Settings of the note module."""

    text: str = Field(default="", description="A note.")


def note() -> Module:
    module = Module("note", "Keeps a note.")
    module.settings(NoteSettings)

    @module.setup
    def setup(prompter: Prompter, editor: ConfigEditor) -> None:
        """Write a note."""
        editor.write("note.text", prompter.text("Note", key="text"))

    return module


class Plan(BaseModel):
    """What the stand-in `team join` returns: the fields `hz join` reads."""

    team: str | None = None
    done: bool = False
    member: dict | None = None
    candidates: list[str] = Field(default_factory=list)
    actions: list[dict] = Field(default_factory=list)
    next: list[str] = Field(default_factory=list)
    pull_request: str | None = None


def team(roster: dict[str, str], calls: list[dict]) -> Module:
    """A stand-in team module: `roster` maps GitHub logins to handles; unlisted people may be proposed."""
    module = Module("team", "Teams.")

    @module.operation(read_only=False, heads={"cli"})
    def join(  # noqa: PLR0913, PLR0917 - the real operation's options
        url: str,
        propose: bool = False,
        handle: str | None = None,
        role: str = "engineer",
        login: str | None = None,
        yes: bool = False,
    ) -> Plan:
        """Join a team."""
        calls.append({"url": url, "propose": propose, "handle": handle, "role": role, "login": login, "yes": yes})
        action = {"what": "clone it with your own access", "done": True}
        if login in roster:
            member = {"handle": roster[login], "roles": ["engineer"]}
            return Plan(team="payments", done=yes, member=member, actions=[action], next=["You are in payments."])
        if propose and yes:
            return Plan(team="payments", done=True, actions=[action], pull_request="https://example.com/pull/7")
        return Plan(team="payments", candidates=["theo"], actions=[action])

    return module


TEST = Origin(distribution="heinzel-test", version="9.9.9", builtin=False)


def kernel_of(settings, **modules: Module) -> Kernel:
    """The built-in modules but the real team (a stand-in replaces it), configured from the files as they are now.

    Each command gets a fresh kernel, as each `hz` process does, so it sees what the last one wrote.
    """
    builtins = [record for record in discover(plugins=False)[0] if record.qualname != "team"]
    records = [ModuleRecord(qualname=name, module=module, origin=TEST) for name, module in modules.items()]
    return Kernel(settings, [*builtins, *records], config=Config.load())


def user_config() -> dict:
    return tomllib.loads((user_config_dir() / "config.toml").read_text(encoding="utf-8"))


@pytest.fixture
def found(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "discover",
        lambda: Discovered(
            github=Login(id="Mira-Example"), timezone="Europe/Berlin", sources={"github": "gh", "timezone": "clock"}
        ),
    )


@pytest.fixture
def place(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # doctor reads the files again; no hz.toml from above the repository


def terminal(monkeypatch):
    monkeypatch.setattr(_setup, "has_terminal", lambda: True)


def scripted(kernel, answers) -> ScriptedPrompter:
    prompter = ScriptedPrompter(answers)
    kernel.bind(Prompter, prompter)
    return prompter


def test_who_you_are_comes_first_and_the_privacy_line_before_anything(settings, found, place, monkeypatch):
    terminal(monkeypatch)
    kernel = kernel_of(settings, note=note())
    prompter = scripted(kernel, ["engineer", "", "", "hello"])  # role, hours, set up note?, its text
    result = runner.invoke(build(kernel), ["setup"])
    assert result.exit_code == 0, result.output
    said = prompter.transcript
    assert said[0].startswith("hz keeps your profile and answers on this machine only, readable by you alone.")
    assert said.index(
        "org (required): Tell hz who you are; what your gh and gws logins and this machine say is found, "
        "the rest is asked once."
    ) < said.index("note (optional): Write a note.")
    assert not [line for line in said if "phase" in line.lower() or "identity" in line.lower()]
    assert user_config()["note"] == {"text": "hello"}


def test_a_run_continues_where_the_last_stopped_and_restart_starts_over(settings, found, place, monkeypatch):
    terminal(monkeypatch)

    def setup(answers: list[str], *args: str) -> tuple[int, list[str]]:
        kernel = kernel_of(settings, note=note())
        prompter = scripted(kernel, answers)
        return runner.invoke(build(kernel), ["setup", *args]).exit_code, prompter.transcript

    assert setup(["engineer", "", "\x03"])[0] == 130  # Ctrl-C at the note step
    code, said = setup(["", "again"])
    assert code == 0
    assert "Already done: org." in said
    assert not any(line.startswith("Your role") for line in said)
    code, said = setup([])
    assert code == 0
    assert any(line.startswith("Everything is set up (last on ") for line in said)
    code, said = setup(["", "y", "anew"], "--restart")  # keep org, change the note, its text
    assert code == 0
    assert "org is already set up. Change it? [y/N] " in said
    assert user_config()["note"] == {"text": "anew"}


def test_an_answers_file_sets_up_without_a_terminal_and_reads_back(settings, found, place, tmp_path):
    answers = tmp_path / "answers.toml"
    answers.write_text('[org]\nrole = "product-manager"\nworking_hours = "08-16"\n\n[note]\ntext = "hi"\n', "utf-8")
    kernel = kernel_of(settings, note=note())
    result = runner.invoke(build(kernel), ["setup", "--answers", str(answers), "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert [(s["module"], s["outcome"]) for s in report["steps"]] == [("org", "done"), ("note", "done")]
    assert report["doctor"] in {"up", "degraded"}  # what the configuration just written is like
    assert user_config()["org"]["role"] == "product-manager"
    assert user_config()["note"] == {"text": "hi"}
    printed = runner.invoke(build(kernel_of(settings, note=note())), ["setup", "--print-answers"])
    assert tomllib.loads(printed.stdout) == {
        "org": {"role": "product-manager", "working_hours": "08-16"},
        "note": {"text": "hi"},
    }


def test_without_a_terminal_a_missing_answer_names_its_key(settings, found, place, tmp_path):
    answers = tmp_path / "answers.toml"
    answers.write_text("[note]\ntext = 'hi'\n", "utf-8")
    result = runner.invoke(build(kernel_of(settings, note=note())), ["setup", "--answers", str(answers)])
    assert result.exit_code == 1
    assert "No answer for org.role" in result.output


def test_without_a_terminal_an_optional_step_without_answers_is_skipped(settings, found, place, tmp_path):
    answers = tmp_path / "answers.toml"
    answers.write_text('[org]\nrole = "engineer"\n', "utf-8")
    result = runner.invoke(build(kernel_of(settings, note=note())), ["setup", "--answers", str(answers), "--json"])
    assert [(s["module"], s["outcome"]) for s in json.loads(result.stdout)["steps"]] == [
        ("org", "done"),
        ("note", "skipped"),
    ]


def test_print_answers_before_any_run_says_what_to_do(settings, place):
    result = runner.invoke(build(kernel_of(settings)), ["setup", "--print-answers"])
    assert result.exit_code == 1
    assert "no answers recorded yet; run `hz setup` first" in result.output


def test_without_a_terminal_or_answers_setup_refuses_and_names_the_way_out(settings, place):
    result = runner.invoke(build(kernel_of(settings)), ["setup"])
    assert result.exit_code == 1
    assert "hz setup --answers answers.toml" in " ".join(result.output.split())


def test_join_finds_you_in_the_roster_and_sets_up_the_rest(settings, found, place, monkeypatch):
    terminal(monkeypatch)
    calls: list[dict] = []
    kernel = kernel_of(settings, team=team({"Mira-Example": "mira"}, calls), note=note())
    prompter = scripted(kernel, ["engineer", "", "", "", "hi"])  # role, hours, make it yours, set up note?, text
    result = runner.invoke(build(kernel), ["join", URL])
    assert result.exit_code == 0, result.output
    assert [(c["login"], c["yes"]) for c in calls] == [("Mira-Example", False), ("Mira-Example", True)]
    said = prompter.transcript
    assert said[0].startswith("hz keeps your profile")
    assert "The roster lists you as mira (engineer)." in said
    invite = next(line for line in said if line.startswith("  invite the next one"))
    assert invite.startswith("  invite the next one post the same line: uvx --from git+https://github.com/")
    assert invite.endswith(f"heinzel join {URL}")
    assert user_config()["note"] == {"text": "hi"}


def test_join_proposes_you_when_the_roster_lacks_you(settings, found, place, monkeypatch):
    terminal(monkeypatch)
    calls: list[dict] = []
    kernel = kernel_of(settings, team=team({}, calls))
    prompter = scripted(kernel, ["program-manager", "", "2", "y"])  # role, hours, none of these, ask the owners
    result = runner.invoke(build(kernel), ["join", URL])
    assert result.exit_code == 0, result.output
    assert calls[-1] == {
        "url": URL,
        "propose": True,
        "handle": "mira-example",
        "role": "program-manager",
        "login": "Mira-Example",
        "yes": True,
    }
    assert "Pull request: https://example.com/pull/7" in prompter.transcript


def test_join_runs_from_a_coding_agent_without_a_terminal(settings, found, place):
    calls: list[dict] = []
    kernel = kernel_of(settings, team=team({}, calls))
    args = ["join", URL, "--role", "engineer", "--as", "theo", "--propose", "--json"]
    with pytest.MonkeyPatch.context() as patch:
        # No GitHub login found: the flags say who you are; the clock still gives the time zone.
        patch.setattr(discovery, "discover", lambda: Discovered(timezone="Europe/Lisbon"))
        result = runner.invoke(build(kernel), args)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["team"]["pull_request"] == "https://example.com/pull/7"
    assert calls[-1]["handle"] == "theo"


def test_join_without_a_team_module_says_how_to_get_one(settings, found, place, monkeypatch):
    terminal(monkeypatch)
    kernel = kernel_of(settings)
    scripted(kernel, [])
    result = runner.invoke(build(kernel), ["join", URL])
    assert result.exit_code == 1
    assert "This hz has no team module" in result.output


def join_with(settings, roster, answers: list[str] | None, *args, **modules):
    """Run `hz join`; `answers` are what a person types, None when nobody is there (an answers file or flags)."""
    calls: list[dict] = []
    kernel = kernel_of(settings, team=team(roster, calls), **modules)
    prompter = scripted(kernel, answers) if answers is not None else None
    result = runner.invoke(build(kernel), ["join", URL, *args])
    return result, calls, prompter.transcript if prompter else []


def test_join_does_nothing_when_you_say_no(settings, found, place, monkeypatch):
    terminal(monkeypatch)
    result, calls, said = join_with(settings, {"Mira-Example": "mira"}, ["engineer", "", "n"], note=note())
    assert result.exit_code == 0, result.output
    assert [c["yes"] for c in calls] == [False]
    assert not any(line.startswith("note (") for line in said)  # not a member: nothing else is set up


def test_join_without_a_person_changes_nothing_without_an_explicit_yes(settings, found, place, tmp_path):
    answers = tmp_path / "answers.toml"
    answers.write_text('[org]\nrole = "engineer"\n', "utf-8")
    result, calls, _ = join_with(settings, {}, None, "--answers", str(answers), "--json")
    assert result.exit_code == 1
    assert [c["yes"] for c in calls] == [False]
    report = json.loads(result.stdout)
    assert (report["joined"], report["team"]) == (False, None)
    assert "Not joined: asking its owners to add you needs a yes; pass `--propose`" in report["error"]


def test_join_yes_alone_never_proposes_you(settings, found, place):
    result, calls, _ = join_with(settings, {}, None, "--role", "engineer", "--yes")
    assert result.exit_code == 1
    assert [c["propose"] for c in calls] == [False]
    assert "pass `--propose`" in " ".join(result.output.split())


def test_join_with_a_member_without_a_person_needs_yes(settings, found, place):
    result, calls, _ = join_with(settings, {"Mira-Example": "mira"}, None, "--role", "engineer")
    assert result.exit_code == 1
    assert [c["yes"] for c in calls] == [False]
    assert "Not joined: joining needs a yes; pass `--yes`" in " ".join(result.output.split())
    result, calls, _ = join_with(settings, {"Mira-Example": "mira"}, None, "--yes", "--json")
    assert result.exit_code == 0, result.output
    assert [c["yes"] for c in calls] == [False, True]
    assert json.loads(result.stdout)["joined"] is True


def test_ctrl_c_while_joining_stops_with_130(settings, found, place, monkeypatch):
    terminal(monkeypatch)
    result, calls, _ = join_with(settings, {"Mira-Example": "mira"}, ["engineer", "", "\x03"])
    assert result.exit_code == 130
    assert [c["yes"] for c in calls] == [False]


def test_a_failing_join_is_reported_in_json(settings, found, place, tmp_path):
    failing = Module("team", "Teams.")

    @failing.operation(read_only=False, heads={"cli"})
    def join(url: str, login: str | None = None, yes: bool = False) -> Plan:
        """Join a team."""
        msg = f"Couldn't clone {url} with your own access"
        raise HeinzelError(msg)

    answers = tmp_path / "answers.toml"
    answers.write_text('[org]\nrole = "engineer"\n', "utf-8")
    kernel = kernel_of(settings, team=failing)
    result = runner.invoke(build(kernel), ["join", URL, "--answers", str(answers), "--json"])
    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["error"].startswith("Couldn't clone")
    assert report["steps"][-1]["outcome"] == "failed"


def test_join_flags_change_a_profile_that_is_set_up_already(settings, found, place, monkeypatch):
    terminal(monkeypatch)
    join_with(settings, {}, ["engineer", "", "", "n"])  # set up org, then decline to ask the owners
    assert user_config()["org"]["role"] == "engineer"
    # Changing a complete profile asks everything but the role the flag gives: handle, time zone, hours, GitHub
    # login, coding agent; then the roster entry and whether to ask the owners.
    result, _, _ = join_with(settings, {}, ["", "", "", "", "", "", "n"], "--role", "program-manager")
    assert user_config()["org"]["role"] == "program-manager", result.output


def test_join_picks_the_roster_entry_that_is_you(settings, found, place, monkeypatch):
    terminal(monkeypatch)
    result, calls, _ = join_with(settings, {}, ["engineer", "", "theo", "y"])
    assert result.exit_code == 0, result.output
    assert (calls[-1]["handle"], calls[-1]["propose"]) == ("theo", True)


def test_join_stops_when_who_you_are_is_not_known(settings, place, tmp_path):
    answers = tmp_path / "answers.toml"
    answers.write_text('[org]\nrole = "engineer"\n', "utf-8")  # no handle: no login was found
    result, calls, _ = join_with(settings, {}, None, "--answers", str(answers), "--json")
    assert result.exit_code == 1
    assert calls == []
    assert "No answer for org.handle" in json.loads(result.stdout)["steps"][0]["detail"]


def test_join_with_your_team_already_asks_nothing_more(settings, found, place, monkeypatch):
    terminal(monkeypatch)
    calls: list[dict] = []
    joined = Module("team", "Teams.")

    @joined.operation(read_only=False, heads={"cli"})
    def join(url: str, login: str | None = None, yes: bool = False) -> Plan:
        """Join a team you are in already."""
        calls.append({"login": login, "yes": yes})
        return Plan(team="payments", done=True, member={"handle": "mira", "roles": ["engineer"]})

    kernel = kernel_of(settings, team=joined)
    scripted(kernel, ["engineer", ""])
    assert runner.invoke(build(kernel), ["join", URL]).exit_code == 0
    assert [c["yes"] for c in calls] == [False]


def test_join_without_the_org_module_still_joins(settings, place, monkeypatch):
    terminal(monkeypatch)
    without_org = settings.model_copy(update={"disabled_modules": frozenset({"org"})})
    result, calls, _ = join_with(without_org, {}, ["", "y"])  # none of the roster entries, then ask the owners
    assert result.exit_code == 0, result.output
    assert calls[-1]["login"] is None


def test_an_answers_file_asks_before_it_changes_what_a_person_set_up(settings, found, place, monkeypatch, tmp_path):
    terminal(monkeypatch)
    kernel = kernel_of(settings)
    scripted(kernel, ["engineer", ""])
    assert runner.invoke(build(kernel), ["setup"]).exit_code == 0
    answers = tmp_path / "answers.toml"
    answers.write_text('[org]\nrole = "program-manager"\n', "utf-8")
    kernel = kernel_of(settings)
    prompter = scripted(kernel, [""])  # keep it
    assert runner.invoke(build(kernel), ["setup", "org", "--answers", str(answers)]).exit_code == 0
    assert "org is already set up; the answers file would change it. Change it? [y/N] " in prompter.transcript
    assert user_config()["org"]["role"] == "engineer"
    printed = runner.invoke(build(kernel_of(settings)), ["setup", "--print-answers"]).stdout
    assert tomllib.loads(printed)["org"]["role"] == "engineer"  # kept: the answers that set it up still stand
