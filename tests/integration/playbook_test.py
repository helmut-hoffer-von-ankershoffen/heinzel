"""A plugin's playbook end to end, with a test plugin that drafts a team's weekly report (ADR 0037).

The test plugin `teamwork` brings what a real playbook plugin would: adapters, a playbook with asset types and
discovery hints, a template contract and recommended settings. Everything a person does is a CLI command; the daemon
tick is the real `DaemonService` with its clock and time zone pinned. The adapters and the agent runner are fakes:
nothing reaches the network or spends money.
"""

import json
import re
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from typing import ClassVar
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from heinzel import (
    AgentResult,
    AssetType,
    Config,
    DiscoveryHint,
    Document,
    Issue,
    Kernel,
    Module,
    PullRequest,
    Window,
)
from heinzel.heads.cli import _setup, build
from heinzel.kernel import TokenUsage, user_config_dir
from heinzel.modules.system.daemon import DaemonService, DaemonSettings

pytestmark = pytest.mark.integration

TEMPLATES = Path(__file__).parents[1] / "fixtures" / "playbook" / "templates"
ZONE = ZoneInfo("America/Los_Angeles")
"""Far from UTC on purpose: Friday 17:00 there is already Saturday in UTC."""
FRIDAY = datetime(2026, 10, 30, tzinfo=ZONE)
PREVIOUS = "# Team update, week 43\n\n## Shipped\n\n- We shipped the export.\n\n## Next\n\n- Search filters.\n"
TRACKER = "# Team tracker\n\nGoal: Search filters by November.\n"
DRAFT = (
    "# Team update, week 44\n\n## Shipped\n\n- We merged Search filters (https://code.invalid/7).\n\n"
    "## Next\n\n- Beta.\n"
)
ASSETS = ("team-repository=acme-labs/tidepool", "team-project=TIDE", "weekly-report=doc-previous")


class Host:
    """A code host and tracker in memory that remembers what it was asked."""

    calls: ClassVar[list[tuple[str, str, Window]]] = []

    def merged_pull_requests(self, repository: str, window: Window) -> list[PullRequest]:
        Host.calls.append(("merged_pull_requests", repository, window))
        return [
            PullRequest(
                repository=repository,
                number=7,
                title="Search filters",
                url="https://code.invalid/7",
                merged_at=window.until,
            )
        ]

    def issues_updated(self, project: str, window: Window) -> list[Issue]:
        Host.calls.append(("issues_updated", project, window))
        return [
            Issue(
                key=f"{project}-3", title="Beta", url="https://track.invalid/3", state="Done", updated_at=window.until
            )
        ]


class Docs:
    """Documents in memory, by reference."""

    texts: ClassVar[dict[str, tuple[str, str]]] = {
        "doc-previous": ("Team update, week 43", PREVIOUS),
        "doc-tracker": ("Team tracker", TRACKER),
    }

    def read(self, ref: str) -> Document:
        title, text = Docs.texts[ref]
        return Document(ref=ref, title=title, text=text, url=f"https://docs.invalid/{ref}")


class Runner:
    """A fake agent runner: drafts the same report every time and remembers the prompts and instructions."""

    calls: ClassVar[list[tuple[str, str]]] = []

    def run(self, prompt, *, contract, tools, timeout, budget) -> AgentResult:
        Runner.calls.append((prompt, contract))
        return AgentResult(
            runner="fake", status="completed", draft=DRAFT, duration_seconds=2.0, usage=TokenUsage(cost_usd=0.05)
        )


def teamwork() -> Module:
    """The test plugin: adapters, and a playbook for the team's weekly report."""
    module = Module("teamwork", "Test-only plugin with a weekly report playbook.")
    module.provides("code-host", modes={"read"}, name="host")(Host)
    module.provides("tracker", modes={"read"}, name="host")(Host)
    module.provides("documents", modes={"read"}, name="docs")(Docs)
    module.playbook(
        "Draft the team's weekly report every Friday from the week's work and the previous report.",
        assets=[
            AssetType(name="team-repository", description="The repository the team merges into."),
            AssetType(name="team-project", description="The tracker project the team plans in."),
            AssetType(
                name="weekly-report",
                description="The rolling document the team's reports live in; its latest one is the report as sent.",
                kept_by="weekly-report",
                hints=[
                    DiscoveryHint(read="documents.read", args={"ref": "doc-previous"}, match="(?i)^team update"),
                    DiscoveryHint(read="documents.read", args={"ref": "doc-tracker"}, match="(?i)^team update"),
                ],
            ),
            AssetType(
                name="team-tracker",
                description="The team's goals, milestones and risks.",
                hints=[DiscoveryHint(read="documents.read", args={"ref": "doc-tracker"}, summary="the team tracker")],
            ),
        ],
        templates=[TEMPLATES],
        defaults={"capabilities.code_host": "host", "capabilities.tracker": "host", "capabilities.documents": "docs"},
    )
    return module


def runner() -> Module:
    module = Module("fakes", "Test-only agent runner.")
    module.provides("agent-runner", modes={"read"}, name="fake")(Runner)
    return module


BINDINGS = {"agent_runner": "fake", "code_host": "host", "tracker": "host", "documents": "docs"}


@pytest.fixture
def make(make_kernel, tmp_path):
    Host.calls.clear()
    Runner.calls.clear()

    def build_kernel(bindings: dict[str, str] = BINDINGS) -> Kernel:
        data = {"capabilities": bindings, "workflow": {"user_dir": str(tmp_path / "workflows")}}
        return make_kernel(builtins=True, teamwork=teamwork(), fakes=runner(), config=Config(files=(), data=data))

    return build_kernel


@pytest.fixture
def kernel(make) -> Kernel:
    return make()


def hz(kernel: Kernel, *args: str, json_output: bool = True, stdin: str | None = None) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"] if json_output else list(args), input=stdin)
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def written(kernel: Kernel, *, schedule: bool = False) -> Path:
    """The weekly report written from the playbook's template, with the person's assets; where its drafts go."""
    args = [arg for pair in (*ASSETS, "team-tracker=doc-tracker") for arg in ("--asset", pair)]
    code, result = hz(kernel, "workflow", "new", "weekly-report", "--template", "teamwork:weekly-report", *args)
    assert code == 0, result
    path = Path(result["file"])
    if schedule:
        path.write_text(path.read_text().replace('# schedule: "0 17 * * FRI"', 'schedule: "0 17 * * FRI"'))
    return Path(kernel.settings.data_dir) / "workflow" / "drafts" / "me-weekly-report"


def daemon(kernel: Kernel, tmp_path: Path, now: datetime) -> DaemonService:
    return DaemonService(
        kernel, kernel.settings, DaemonSettings(unit_dir=tmp_path / "units"), clock=lambda: now, zone=ZONE
    )


def test_playbooks_list_asset_types_templates_and_recommended_settings(kernel):
    code, listed = hz(kernel, "workflow", "playbooks")
    assert code == 0, listed
    (playbook,) = listed["playbooks"]
    assert playbook["module"] == "teamwork"
    assert [a["name"] for a in playbook["assets"]] == [
        "team-repository",
        "team-project",
        "weekly-report",
        "team-tracker",
    ]
    assert playbook["templates"] == [
        {
            "template": "teamwork:weekly-report",
            "assets": ["team-repository", "team-project", "weekly-report", "team-tracker"],
            "keeps": ["weekly-report"],
            "command": "hz workflow new weekly-report --template teamwork:weekly-report",
        }
    ]
    assert [d["command"] for d in playbook["defaults"]] == [
        "hz system config set capabilities.code_host host",
        "hz system config set capabilities.tracker host",
        "hz system config set capabilities.documents docs",
    ]
    code, text = hz(kernel, "workflow", "playbooks", json_output=False)
    assert "recommends" in str(text)
    assert listed["next"][-1]["command"] == "hz teamwork setup"


def test_discover_finds_candidates_through_what_you_can_read(make):
    code, found = hz(make(), "workflow", "discover")
    assert code == 0, found
    assert [(c["asset"], c["ref"], c["title"]) for c in found["candidates"]] == [
        ("teamwork:weekly-report", "doc-previous", "Team update, week 43"),
        ("teamwork:team-tracker", "doc-tracker", "Team tracker"),
    ]
    assert found["problems"] == []
    code, found = hz(make({"agent_runner": "fake"}), "workflow", "discover")
    assert code == 0
    assert [p["hint"] for p in found["problems"]] == ["documents.read"] * 3
    assert "hz system config set capabilities.documents docs" in found["problems"][0]["problem"]
    code, text = hz(make(), "workflow", "discover", "--playbook", "nope", json_output=False)
    assert code == 1
    assert "No playbook 'nope'" in str(text)


def test_setup_applies_settings_only_on_yes_proposes_assets_and_writes_the_template(kernel, monkeypatch, tmp_path):
    monkeypatch.setattr(_setup, "stdin_is_terminal", lambda: True)
    monkeypatch.chdir(tmp_path)
    answers = [
        "",  # Apply them? [y/N]: no
        "1",  # which is your weekly-report: the only candidate
        "1",  # which is your team-tracker
        "",  # write the contract? yes
        "",  # its name: weekly-report
    ]
    code, text = hz(kernel, "teamwork", "setup", json_output=False, stdin="\n".join(answers) + "\n")
    assert code == 0, text
    config = user_config_dir() / "config.toml"
    assert "capabilities" not in (tomllib.loads(config.read_text()) if config.exists() else {})
    contract = (tmp_path / "workflows" / "weekly-report.prose.md").read_text(encoding="utf-8")
    assert 'args: {ref: "doc-previous"}' in contract
    assert 'args: {ref: "doc-tracker"}' in contract
    assert "{{asset:team-repository}}" in contract  # no hint: the person fills it in
    assert hz(kernel, "workflow", "check")[1]["diagnostics"] == []
    code, text = hz(kernel, "teamwork", "setup", json_output=False, stdin="y\n2\n2\n\n\n")
    assert code == 0, text
    assert tomllib.loads(config.read_text())["capabilities"] == {
        "code_host": "host",
        "tracker": "host",
        "documents": "docs",
    }
    assert "weekly-report exists; left as it is." in str(text)


def test_new_from_a_template_fills_assets_and_says_what_is_left(kernel):
    code, result = hz(kernel, "workflow", "new", "team-update", "--template", "weekly-report", "--asset", ASSETS[2])
    assert code == 0, result
    text = Path(result["file"]).read_text(encoding="utf-8")
    assert re.search(r"^name: team-update$", text, re.MULTILINE)
    assert result["next"][0]["command"] == "hz workflow discover"
    code, output = hz(kernel, "workflow", "new", "x", "--template", "nope", json_output=False)
    assert (code, "the templates are: teamwork:weekly-report" in str(output)) == (1, True)
    code, output = hz(
        kernel, "workflow", "new", "x", "--template", "weekly-report", "--asset", "y=z", json_output=False
    )
    assert (code, "has no placeholder for the asset 'y'" in str(output)) == (1, True)
    code, output = hz(kernel, "workflow", "new", "x", "--template", "weekly-report", "--asset", "y", json_output=False)
    assert (code, "must read type=reference" in str(output)) == (1, True)


def test_a_dry_run_reads_every_input_and_costs_nothing(kernel):
    drafts = written(kernel)
    code, dry = hz(kernel, "workflow", "run", "weekly-report", "--dry-run")
    assert code == 0, dry
    assert Runner.calls == []  # no agent session: nothing spent
    assert not drafts.exists()  # no draft, no record
    assert not list((Path(kernel.settings.data_dir) / "receipts").rglob("*.json"))
    assert sorted(dry["packet"]["inputs"]) == ["issues", "merged", "previous_report", "team_tracker"]
    assert dry["packet"]["inputs"]["previous_report"]["data"]["text"] == PREVIOUS
    assert {(name, arg) for name, arg, _ in Host.calls} == {
        ("merged_pull_requests", "acme-labs/tidepool"),
        ("issues_updated", "TIDE"),
    }
    assert all(window.until - window.since == timedelta(days=7) for *_, window in Host.calls)


def test_the_daemon_drafts_it_on_friday_at_17_local_with_lineage(kernel, tmp_path):
    """Nothing on Thursday 17:03 or Friday 16:58; the Friday 17:00 slot at the 17:03 tick, once."""
    drafts = written(kernel, schedule=True)
    assert hz(kernel, "system", "daemon", "jobs")[1][0]["job"] == "workflow:me-weekly-report"
    thursday = FRIDAY - timedelta(days=1)
    assert daemon(kernel, tmp_path, thursday.replace(hour=17, minute=3)).tick().runs == []
    assert daemon(kernel, tmp_path, FRIDAY.replace(hour=16, minute=58)).tick().runs == []
    report = daemon(kernel, tmp_path, FRIDAY.replace(hour=17, minute=3)).tick()
    assert [(r.job, r.disposition.value, r.error) for r in report.runs] == [
        ("workflow:me-weekly-report", "rendered", None)
    ]
    slot = report.runs[0].as_of
    assert (slot.astimezone(ZONE).strftime("%a %H:%M"), slot.utcoffset()) == ("Fri 17:00", timedelta(hours=-7))
    assert slot.astimezone(ZoneInfo("UTC")).strftime("%a %H:%M") == "Sat 00:00"
    assert daemon(kernel, tmp_path, FRIDAY.replace(hour=17, minute=8)).tick().runs == []

    ((prompt, instructions),) = Runner.calls  # the contract as instructions, every input as data
    assert "previous report's structure and tone" in instructions
    assert "We shipped the export." in prompt
    assert "Goal: Search filters by November." in prompt
    assert "https://code.invalid/7" in prompt

    (draft,) = drafts.glob("*.md")  # the draft, locally, with its lineage
    assert draft.read_text(encoding="utf-8") == DRAFT
    record = json.loads(draft.with_name(f"{draft.stem}.record.json").read_text(encoding="utf-8"))
    lineage = record["rendered"]["inputs"]
    assert sorted(lineage) == ["issues", "merged", "previous_report", "team_tracker"]
    assert all(value.startswith("sha256:") for value in lineage.values())
    assert record["packet"]["inputs"]["merged"]["arguments"]["repository"] == "acme-labs/tidepool"
    assert record["rendered"]["seconds"] >= 0


def test_show_reports_drafting_time_and_how_much_was_changed_before_sending(kernel, tmp_path):
    written(kernel)
    code, result = hz(kernel, "workflow", "run", "weekly-report")
    assert code == 0, result
    draft = result["rendered"]["draft"]
    code, shown = hz(kernel, "workflow", "show", "weekly-report")
    assert (shown["measurements"]["drafts"], shown["measurements"]["sent"]) == (1, 0)
    assert shown["measurements"]["recent"][0]["cost_usd"] == pytest.approx(0.05)
    assert shown["measurements"]["recent"][0]["drafting_seconds"] >= 0
    sent = tmp_path / "sent.md"
    sent.write_text(DRAFT.replace("- Beta.", "- Beta for two customers."), encoding="utf-8")
    code, recorded = hz(kernel, "workflow", "sent", "weekly-report", "--file", str(sent), "--edit-minutes", "6")
    assert code == 0, recorded
    assert 0 < recorded["changed"] < 0.2
    assert recorded["sent_sha256"].startswith("sha256:")
    assert "customers" not in Path(recorded["record"]).read_text(encoding="utf-8")  # the sent text isn't kept
    code, shown = hz(kernel, "workflow", "show", "weekly-report")
    measurements = shown["measurements"]
    assert (measurements["sent"], measurements["total_edit_minutes"]) == (1, 6)
    assert measurements["mean_changed"] == recorded["changed"]
    assert hz(kernel, "workflow", "drafts", "--name", "weekly-report")[1]["drafts"][0]["draft"] == draft
    code, text = hz(kernel, "workflow", "show", "weekly-report", json_output=False)
    assert "Success test" in str(text)
    assert "1 sent" in " ".join(str(text).split())
    code, text = hz(kernel, "workflow", "sent", "weekly-report", "--file", str(sent), json_output=False)
    assert code == 0
    assert "of the words changed" in str(text)
    code, approved = hz(kernel, "workflow", "sent", "weekly-report", "--ref", "doc-previous")
    assert code == 0, approved
    assert approved["sent"] == "documents:doc-previous"  # the approved version, read from where the asset lives
    assert approved["changed"] > recorded["changed"]
