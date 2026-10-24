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


def test_list_shows_the_contract_as_scheduled(kernel):
    code, listed = hz(kernel, "workflow", "list")
    assert code == 0
    assert [(c["namespace"], c["name"], c["scheduled"], c["valid"]) for c in listed] == [
        ("me", "weekly-digest", True, True)
    ]


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
    empty = make_kernel(builtins=True, config=Config(files=(), data={"workflow": {"user_dir": str(tmp_path / "x")}}))
    code, text = hz(empty, "workflow", "list", json_output=False)
    assert "No contracts found" in str(text)
    code, text = hz(empty, "workflow", "check", json_output=False)
    assert (code, "0 contract(s) checked" in str(text)) == (0, True)


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
    response = TestClient(api.build(kernel)).get("/workflow/show", params={"name": "weekly-digest"})
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


def test_the_daemon_runs_a_contract_and_writes_a_receipt_with_the_draft(kernel):
    code, jobs = hz(kernel, "system", "daemon", "jobs")
    assert code == 0
    assert [j["job"] for j in jobs] == ["workflow:me-weekly-digest"]
    assert jobs[0]["timeout_seconds"] == timedelta(minutes=12).total_seconds()
    code, report = hz(kernel, "system", "daemon", "tick", "--job", "workflow:me-weekly-digest")
    assert code == 0, report
    run = report["runs"][0]
    assert run["disposition"] == "rendered"
    receipts = list((Path(kernel.settings.data_dir) / "receipts").rglob("*.json"))
    receipt = next(json.loads(p.read_text()) for p in receipts if "workflow:me-weekly-digest" in p.read_text())
    assert receipt["id"] == run["receipt"]
    assert Path(receipt["result"]["draft"]).is_file()
    assert receipt["result"]["agent"]["runner"] == "fake"


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
        "max_usd = 500.0\n",
        encoding="utf-8",
    )
    kernel = make_kernel(builtins=True, fakes=fakes(), config=Config.load(repository))
    assert hz(kernel, "workflow", "list") == (0, [])
    assert hz(kernel, "system", "daemon", "jobs") == (0, [])
    _, report = hz(kernel, "system", "doctor")
    ignored = sorted(c["name"] for c in report["checks"]["system.doctor"] if c["name"].startswith("project setting"))
    assert ignored == [
        "project setting workflow.max_usd",
        "project setting workflow.schedule_team",
        "project setting workflow.team_repository",
        "project setting workflow.user_dir",
    ]
