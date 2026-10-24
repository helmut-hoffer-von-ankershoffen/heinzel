"""`hz workflow` through the CLI head, and contracts compiled into daemon jobs (ADR 0021).

The adapters are test-only and the agent runner is a fake: no test runs a real agent or reaches the network.
"""

import json
import shutil
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import AgentResult, Config, Issue, Kernel, Module, PullRequest, Settings, Window
from heinzel.heads import api
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

EXAMPLE = Path(__file__).parents[1] / "fixtures" / "workflows" / "weekly-digest.prose.md"


class Host:
    """A code host and tracker in memory."""

    def merged_pull_requests(self, repository: str, window: Window) -> list[PullRequest]:
        return [
            PullRequest(
                repository=repository, number=7, title="Fix login", url="https://code.invalid/7", merged_at=window.until
            )
        ]

    def issues_updated(self, project: str, window: Window) -> list[Issue]:
        return [
            Issue(
                key=f"{project}-1", title="Crash", url="https://track.invalid/1", state="Done", updated_at=window.until
            )
        ]


class Runner:
    """A fake agent runner that drafts from the prompt it gets."""

    prompts: ClassVar[list[str]] = []

    def run(self, prompt, *, contract, tools, timeout, budget) -> AgentResult:
        Runner.prompts.append(prompt)
        draft = "# Digest\n\n- Fix login (https://code.invalid/7)\n" if "Fix login" in prompt else "nothing"
        return AgentResult(runner="fake", status="completed", draft=draft, duration_seconds=0.5)


def fakes() -> Module:
    module = Module("fakes", "Test-only adapters.")
    module.provides("code-host", modes={"read"}, name="host")(Host)
    module.provides("tracker", modes={"read"}, name="host")(Host)
    module.provides("agent-runner", modes={"read"}, name="fake")(Runner)
    return module


def notes() -> Module:
    module = Module("notes", "Test-only publish operation.")

    @module.operation(read_only=False)
    def publish() -> str:
        """Publish a digest."""
        return "published"

    return module


BINDINGS = {"agent_runner": "fake", "code_host": "host", "tracker": "host"}


@pytest.fixture
def contracts(tmp_path) -> Path:
    directory = tmp_path / "workflows"
    directory.mkdir()
    shutil.copy(EXAMPLE, directory)
    return directory


@pytest.fixture
def kernel(make_kernel, contracts) -> Kernel:
    config = Config(files=(), data={"capabilities": BINDINGS, "workflow": {"user_dir": str(contracts)}})
    return make_kernel(builtins=True, fakes=fakes(), notes=notes(), config=config)


def hz(kernel: Kernel, *args: str, json_output: bool = True) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"] if json_output else list(args))
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def unwrapped(text: object) -> str:
    """Terminal output without any whitespace: the test terminal is 80 columns wide and wraps paths and commands."""
    return "".join(str(text).split())


def test_list_shows_the_contract_as_scheduled_and_the_directories(kernel, contracts):
    code, listed = hz(kernel, "workflow", "list")
    assert code == 0
    assert [(c["namespace"], c["name"], c["scheduled"], c["valid"]) for c in listed["contracts"]] == [
        ("me", "weekly-digest", True, True)
    ]
    assert listed["directories"] == [{"namespace": "me", "path": str(contracts), "exists": True}]


def test_list_in_a_terminal_shows_contracts_and_their_problems(kernel, contracts, make_kernel, tmp_path):
    (contracts / "broken.prose.md").write_text("no frontmatter")
    (contracts / "noisy.prose.md").write_text(
        EXAMPLE.read_text().replace("weekly-digest", "noisy").replace("### Steps", "### Notes")
    )
    code, text = hz(kernel, "workflow", "list", json_output=False)
    assert code == 0
    assert "me:broken" in str(text)
    assert "1 error(s)" in str(text)
    assert "1 warning(s)" in str(text)
    code, text = hz(kernel, "workflow", "show", "broken", json_output=False)
    assert code == 0
    assert "HZC001" in str(text)


def test_first_contact_says_what_a_contract_is_where_it_goes_and_what_to_run(make_kernel, tmp_path):
    """The empty states of `list` and `check`: a new user learns the directory and the next command from them."""
    missing = tmp_path / "x"
    empty = make_kernel(builtins=True, config=Config(files=(), data={"workflow": {"user_dir": str(missing)}}))
    code, text = hz(empty, "workflow", "list", json_output=False)
    flat = unwrapped(text)
    assert code == 0
    assert "No contracts yet" in str(text)
    assert "A contract is a <name>.prose.md file" in str(text)
    assert str(missing) in flat
    assert "doesnotexistyet" in flat
    assert "hz workflow new my-first-workflow" in str(text)
    assert "docs/WORKFLOWS.md" in flat
    code, text = hz(empty, "workflow", "check", json_output=False)
    assert (code, "No contracts to check" in str(text), "doesnotexistyet" in unwrapped(text)) == (0, True, True)
    assert "hz workflow new my-first-workflow" in str(text)
    code, listed = hz(empty, "workflow", "list")
    assert listed == {
        "directories": [{"namespace": "me", "path": str(missing), "exists": False}],
        "contracts": [],
        "next": [
            {"command": "hz workflow new my-first-workflow", "why": "write a starter contract, with comments"},
            {"command": "hz workflow check", "why": "every problem by file and line"},
        ],
    }


def test_new_writes_a_valid_starter_that_shows_as_ready_and_dry_runs(make_kernel, tmp_path):
    """Write my first contract, then try it without spending: on a fresh installation with only the runner bound."""
    missing = tmp_path / "x"
    config = Config(files=(), data={"capabilities": {"agent_runner": "fake"}, "workflow": {"user_dir": str(missing)}})
    fresh = make_kernel(builtins=True, fakes=fakes(), config=config)
    code, text = hz(fresh, "workflow", "new", "standup-notes", json_output=False)
    flat = unwrapped(text)
    assert code == 0, text
    assert f"Wrote{missing / 'standup-notes.prose.md'}" in flat
    for command in (
        "hz workflow check --path",
        "hz workflow show me:standup-notes",
        "hz workflow run me:standup-notes --dry-run",
        "hz workflow run me:standup-notes",
    ):
        assert unwrapped(command) in flat
    code, report = hz(fresh, "workflow", "check")
    assert (code, report["diagnostics"]) == (0, [])
    code, shown = hz(fresh, "workflow", "show", "standup-notes")
    assert (code, shown["ready"], shown["schedule"], shown["draft_count"]) == (0, True, None, 0)
    assert shown["next"][0]["command"] == "hz workflow run me:standup-notes --dry-run"
    code, text = hz(fresh, "workflow", "show", "standup-notes", json_output=False)
    assert "Ready" in str(text)
    assert "yes: every requirement is served" in str(text)
    code, text = hz(fresh, "workflow", "run", "standup-notes", "--dry-run", json_output=False)
    assert code == 0, text
    assert "nothing was spent" in " ".join(str(text).split())
    assert "hzworkflowrunme:standup-notes" in unwrapped(text)
    code, text = hz(fresh, "workflow", "new", "standup-notes", json_output=False)
    assert code == 1
    assert "hz workflow new standup-notes-2" in str(text)
    code, text = hz(fresh, "workflow", "new", "Standup Notes", json_output=False)
    assert code == 1
    assert "hz workflow new standup-notes" in str(text)


def test_new_for_the_team_writes_into_the_repository_and_says_how_to_contribute(make_kernel, tmp_path):
    user = tmp_path / "me"
    team = tmp_path / "team"
    team.mkdir()
    without = make_kernel(builtins=True, config=Config(files=(), data={"workflow": {"user_dir": str(user)}}))
    code, text = hz(without, "workflow", "new", "retro-agenda", "--team", json_output=False)
    assert code == 1
    assert "hz system config set workflow.team_repository /path/to/checkout" in str(text)
    table = {"user_dir": str(user), "team_repository": str(team)}
    kernel = make_kernel(builtins=True, config=Config(files=(), data={"workflow": table}))
    code, result = hz(kernel, "workflow", "new", "retro-agenda", "--team")
    assert code == 0, result
    assert result["file"] == str(team / "workflows" / "retro-agenda.prose.md")
    commands = [s["command"] for s in result["next"]]
    assert f"git -C {team} add workflows/retro-agenda.prose.md" in commands
    assert "hz system config set workflow.schedule_team '[\"retro-agenda\"]'" in commands
    code, shown = hz(kernel, "workflow", "show", "team:retro-agenda")
    assert (code, shown["namespace"], shown["scheduled"]) == (0, "team", False)
    code, listed = hz(kernel, "workflow", "list")
    assert [(d["namespace"], d["exists"]) for d in listed["directories"]] == [("team", True), ("me", False)]
    assert listed["next"] == [{"command": "hz workflow show team:retro-agenda", "why": "is it ready?"}]
    code, text = hz(kernel, "workflow", "list", json_output=False)
    assert "hz workflow show team:retro-agenda" in str(text)


def test_drafts_lists_what_runs_saved_and_shows_the_latest(kernel, tmp_path):
    code, text = hz(kernel, "workflow", "drafts", json_output=False)
    assert code == 0
    assert "No drafts yet under" in str(text)
    assert "hz workflow run <name>" in str(text)
    assert hz(kernel, "workflow", "run", "weekly-digest")[0] == 0
    code, listed = hz(kernel, "workflow", "drafts")
    assert code == 0
    assert [d["contract"] for d in listed["drafts"]] == ["me:weekly-digest"]
    assert Path(listed["drafts"][0]["record"]).is_file()
    assert listed["next"][0]["command"] == "hz workflow drafts --name me:weekly-digest --latest"
    code, text = hz(kernel, "workflow", "drafts", "--name", "weekly-digest", "--latest", json_output=False)
    assert code == 0
    assert str(text).startswith("# Digest")
    assert hz(kernel, "workflow", "drafts", "--name", "other")[1]["drafts"] == []
    code, text = hz(kernel, "workflow", "run", "weekly-digest", json_output=False)
    assert "hzworkflowdrafts--nameme:weekly-digest" in unwrapped(text)
    response = TestClient(api.build(kernel), base_url="http://localhost").get("/workflow/drafts")
    assert response.status_code == 404  # drafts hold fetched data; the CLI and MCP list them, not a local web server


def old_draft(kernel: Kernel) -> tuple[Path, Path]:
    """A draft and its record from 2020, as a render then would have named them: long past 90 days."""
    directory = Path(kernel.settings.data_dir) / "workflow" / "drafts" / "me-weekly-digest"
    directory.mkdir(parents=True, exist_ok=True)
    draft, record = directory / "20200101T070000Z.md", directory / "20200101T070000Z.record.json"
    draft.write_text("# Old digest")
    record.write_text("{}")
    return draft, record


def test_prune_lists_then_deletes_expired_drafts_and_doctor_reports_them_first(kernel):
    assert hz(kernel, "workflow", "run", "weekly-digest")[0] == 0
    draft, record = old_draft(kernel)
    _, doctor = hz(kernel, "system", "doctor")
    check = {c["name"]: c for c in doctor["checks"]["workflow"]}["workflow retention"]
    assert check["status"] == "degraded"
    assert check["detail"].startswith("1 draft(s) and 1 record(s)")
    assert draft.exists()  # doctor reads
    code, planned = hz(kernel, "workflow", "prune", "--dry-run")
    assert code == 0
    assert (planned["dry_run"], planned["files"], planned["retention_days"]) == (True, [str(draft), str(record)], 90)
    assert draft.exists()
    code, text = hz(kernel, "workflow", "prune", "--dry-run", json_output=False)
    assert code == 0
    assert "Woulddelete1draft(s)and1record(s)" in unwrapped(text)
    assert unwrapped(str(draft)) in unwrapped(text)
    code, done = hz(kernel, "workflow", "prune")
    assert (code, done["files"]) == (0, [str(draft), str(record)])
    assert not draft.exists()
    assert not record.exists()
    assert [d["contract"] for d in hz(kernel, "workflow", "drafts")[1]["drafts"]] == ["me:weekly-digest"]
    assert hz(kernel, "workflow", "prune")[1]["files"] == []
    response = TestClient(api.build(kernel), base_url="http://localhost").post("/workflow/prune")
    assert response.status_code == 404  # deleting your files is not for a page that can reach a local server


def test_retention_zero_keeps_drafts_forever_and_says_so(make_kernel, contracts):
    workflow = {"user_dir": str(contracts), "retention_days": 0}
    kernel = make_kernel(builtins=True, fakes=fakes(), config=Config(files=(), data={"workflow": workflow}))
    draft, _ = old_draft(kernel)
    code, text = hz(kernel, "workflow", "prune", json_output=False)
    assert code == 0
    assert "Drafts and records are kept forever" in str(text)
    assert "hz system config set workflow.retention_days 90" in str(text)
    assert draft.exists()


def test_the_daemon_prunes_on_its_tick_and_the_receipt_counts_without_naming_files(kernel):
    draft, _ = old_draft(kernel)
    code, report = hz(kernel, "system", "daemon", "tick", "--job", "workflow:prune")
    assert code == 0, report
    assert report["runs"][0]["disposition"] == "rendered"
    assert not draft.exists()
    receipts = [json.loads(p.read_text()) for p in (Path(kernel.settings.data_dir) / "receipts").glob("*.json")]
    [receipt] = [r for r in receipts if r["job"] == "workflow:prune"]
    assert {k: receipt["result"][k] for k in ("retention_days", "drafts", "records")} == {
        "retention_days": 90,
        "drafts": 1,
        "records": 1,
    }
    assert "files" not in receipt["result"]


def test_show_reports_the_compiled_view(kernel):
    code, shown = hz(kernel, "workflow", "show", "weekly-digest")
    assert code == 0
    assert shown["job"] == "workflow:me-weekly-digest"
    assert shown["unmet"] == []
    assert {r["requirement"]: r["adapter"] for r in shown["requirements"]} == {
        "code-host:read": "host",
        "tracker:read": "host",
        "agent-runner:read": "fake",
    }
    code, text = hz(kernel, "workflow", "show", "weekly-digest", json_output=False)
    assert code == 0
    assert "workflow:me-weekly-digest" in str(text)


def test_check_exits_non_zero_on_errors_for_a_teams_ci(kernel, contracts, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert hz(kernel, "workflow", "check")[0] == 0
    broken = tmp_path / "repo" / "workflows" / "broken.prose.md"
    broken.parent.mkdir(parents=True)
    broken.write_text(EXAMPLE.read_text().replace("weekly-digest", "broken").replace("tracker:read", "tracker:write"))
    code, report = hz(kernel, "workflow", "check", "--path", str(tmp_path / "repo"))
    assert code == 1
    assert [(d["code"], d["line"], d["field"]) for d in report["diagnostics"]] == [("HZC002", 13, "hz.requires.1")]
    code, text = hz(kernel, "workflow", "check", "--path", str(broken), json_output=False)
    assert code == 1
    assert "broken.prose.md:13  HZC002" in str(text).replace("\n", "")


def test_run_dry_run_prints_the_packet_without_an_agent(kernel):
    Runner.prompts.clear()
    code, result = hz(kernel, "workflow", "run", "weekly-digest", "--dry-run")
    assert code == 0
    assert result["dry_run"] is True
    assert result["packet"]["inputs"]["merged"]["data"][0]["title"] == "Fix login"
    assert Runner.prompts == []
    code, text = hz(kernel, "workflow", "run", "weekly-digest", "--dry-run", json_output=False)
    assert code == 0
    assert "--- prompt ---" in str(text)


def test_run_renders_and_saves_the_draft_nothing_is_published(kernel):
    code, result = hz(kernel, "workflow", "run", "weekly-digest")
    assert code == 0
    rendered = result["rendered"]
    assert Path(rendered["draft"]).read_text().startswith("# Digest")
    assert rendered["awaiting"] == ["notes.publish"]
    code, text = hz(kernel, "workflow", "run", "weekly-digest", json_output=False)
    assert code == 0
    assert "Awaiting approval" in str(text)


def test_a_name_in_both_places_needs_its_prefix_in_every_head(make_kernel, contracts, tmp_path):
    team = tmp_path / "team"
    (team / "workflows").mkdir(parents=True)
    shutil.copy(EXAMPLE, team / "workflows")
    table = {"user_dir": str(contracts), "team_repository": str(team)}
    kernel = make_kernel(
        builtins=True,
        fakes=fakes(),
        notes=notes(),
        config=Config(files=(), data={"capabilities": BINDINGS, "workflow": table}),
    )
    code, output = hz(kernel, "workflow", "show", "weekly-digest")
    assert code != 0
    assert "say team:weekly-digest or me:weekly-digest" in str(output)
    response = TestClient(api.build(kernel), base_url="http://localhost").get(
        "/workflow/show", params={"name": "weekly-digest"}
    )
    assert response.status_code >= 400
    assert "say team:weekly-digest" in response.text
    assert hz(kernel, "workflow", "show", "team:weekly-digest")[0] == 0


def test_contract_text_is_shown_literally_never_as_markup(make_kernel, tmp_path):
    directory = tmp_path / "tricky"
    directory.mkdir()
    (directory / "weekly-digest.prose.md").write_text(
        EXAMPLE.read_text().replace("title: Weekly app digest", 'title: "[link=https://evil.invalid]Click[/link]"')
    )
    config = Config(files=(), data={"capabilities": BINDINGS, "workflow": {"user_dir": str(directory)}})
    kernel = make_kernel(builtins=True, fakes=fakes(), notes=notes(), config=config)
    for command in ("list", "show"):
        args = ("workflow", command) if command == "list" else ("workflow", command, "weekly-digest")
        code, text = hz(kernel, *args, json_output=False)
        assert code == 0
        assert "[link=" in str(text).replace("\n", "").replace(" ", "").replace("│", "")


def test_compiled_jobs_appear_in_the_capability_matrix(kernel):
    code, report = hz(kernel, "system", "capability", "list")
    assert code == 0
    uses = {(use["requester"], use["requirement"]) for use in report["uses"]}
    assert ("workflow me-weekly-digest", "tracker:read") in uses
    assert ("workflow me-weekly-digest", "agent-runner:read") in uses


def test_an_unknown_contract_is_an_error(kernel):
    code, output = hz(kernel, "workflow", "show", "nope")
    assert code != 0
    assert "No contract 'nope'" in str(output)


def test_the_daemon_runs_a_contract_and_writes_a_receipt_naming_the_draft(kernel):
    code, jobs = hz(kernel, "system", "daemon", "jobs")
    assert code == 0
    assert [j["job"] for j in jobs] == ["workflow:prune", "workflow:me-weekly-digest"]
    assert jobs[1]["timeout_seconds"] == timedelta(minutes=12).total_seconds()
    code, report = hz(kernel, "system", "daemon", "tick", "--job", "workflow:me-weekly-digest")
    assert code == 0, report
    run = report["runs"][0]
    assert run["disposition"] == "rendered"
    receipts = list((Path(kernel.settings.data_dir) / "receipts").rglob("*.json"))
    receipt = next(json.loads(p.read_text()) for p in receipts if "workflow:me-weekly-digest" in p.read_text())
    assert receipt["id"] == run["receipt"]
    assert Path(receipt["result"]["draft"]).is_file()
    assert receipt["result"]["agent"]["runner"] == "fake"
    assert "draft" not in receipt["result"]["agent"]  # the receipt outlives the draft: its hash, never its text


def test_a_contract_whose_capability_is_unbound_fails_its_daemon_run(make_kernel, contracts):
    config = Config(files=(), data={"capabilities": {"agent_runner": "fake"}, "workflow": {"user_dir": str(contracts)}})
    kernel = make_kernel(builtins=True, fakes=fakes(), notes=notes(), config=config)
    code, report = hz(kernel, "system", "daemon", "tick", "--job", "workflow:me-weekly-digest")
    assert code == 1
    assert "code-host:read: no adapter bound" in report["runs"][0]["error"]
    code, doctor = hz(kernel, "system", "doctor")
    checks = {c["name"]: c for c in doctor["checks"]["workflow"]}
    assert checks["workflow me:weekly-digest"]["status"] == "degraded"


def test_disabling_the_module_removes_its_jobs(make_kernel, contracts):
    config = Config(
        files=(),
        data={
            "hz": {"disabled_modules": ["workflow"]},
            "capabilities": BINDINGS,
            "workflow": {"user_dir": str(contracts)},
        },
    )
    disabled = make_kernel(builtins=True, fakes=fakes(), config=config, settings_=config.settings(Settings, "hz"))
    code, jobs = hz(disabled, "system", "daemon", "jobs")
    assert code == 0
    assert jobs == []


def test_a_project_file_cant_point_discovery_at_its_own_contracts(make_kernel, tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user))
    repository = tmp_path / "repository"
    (repository / "contracts").mkdir(parents=True)
    shutil.copy(EXAMPLE, repository / "contracts")
    (repository / "hz.toml").write_text(
        '[workflow]\nuser_dir = "contracts"\nteam_repository = "."\nschedule_team = ["weekly-digest"]\n'
        "max_usd = 500.0\nretention_days = 1\n",
        encoding="utf-8",
    )
    kernel = make_kernel(builtins=True, fakes=fakes(), config=Config.load(repository))
    code, listed = hz(kernel, "workflow", "list")
    assert (code, listed["contracts"]) == (0, [])
    assert listed["directories"][0]["path"] == str(user / "workflows")
    assert [job["job"] for job in hz(kernel, "system", "daemon", "jobs")[1]] == ["workflow:prune"]
    _, report = hz(kernel, "system", "doctor")
    ignored = sorted(c["name"] for c in report["checks"]["system.doctor"] if c["name"].startswith("project setting"))
    assert ignored == [
        "project setting workflow.max_usd",
        "project setting workflow.retention_days",
        "project setting workflow.schedule_team",
        "project setting workflow.team_repository",
        "project setting workflow.user_dir",
    ]


def test_one_bad_schedule_leaves_the_other_jobs_and_matrix_rows_and_fails_check(kernel, contracts):
    fullwidth_zero = chr(0xFF10)
    bad = EXAMPLE.read_text().replace("weekly-digest", "bad-schedule").replace('"0 7', f'"{fullwidth_zero} 7')
    (contracts / "bad-schedule.prose.md").write_text(bad)
    code, report = hz(kernel, "workflow", "check")
    assert code == 1
    assert [(d["code"], d["field"]) for d in report["diagnostics"]] == [("HZC011", "hz.schedule")]
    assert [job["job"] for job in hz(kernel, "system", "daemon", "jobs")[1]] == [
        "workflow:prune",
        "workflow:me-weekly-digest",
    ]
    uses = {use["requester"] for use in hz(kernel, "system", "capability", "list")[1]["uses"]}
    assert "workflow me-weekly-digest" in uses
    assert "workflow me-bad-schedule" not in uses
