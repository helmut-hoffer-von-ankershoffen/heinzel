"""`hz report` and `hz graph` through the heads: telemetry in, a team graph of kept promises out (ADR 0054).

The scenario is the fixture team under `tests/fixtures/graph/team/`: Priya's and Costas's Heinzelmännchen keep their
rows of the tracker every Wednesday, Mira's drafts the weekly report from the rows every Friday. On Friday at 15:00
Priya's row waits for her approval, Costas's Heinzelmännchen has been silent since Monday, and the report waits on
both rows. The JSON the tests compare is the fixture the web app's graph view is built against.
"""

import json
import os
import shutil
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import Config, Kernel
from heinzel.heads import api, web
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "graph"
EXPECTED = FIXTURES / "weekly-report.jgf.json"
WEB_FIXTURE = ROOT / "web" / "src" / "test" / "fixtures" / "graph.jgf.json"
SCHEMA = json.loads((ROOT / "docs" / "schemas" / "team-graph-1.json").read_text(encoding="utf-8"))
FRIDAY = "2026-10-23T15:00+01:00"
TOKEN = "s3ssion-token-for-tests"


def hz(kernel: Kernel, *args: str) -> tuple[int, dict | list | str]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


@pytest.fixture
def team(tmp_path: Path) -> Path:
    checkout = tmp_path / "team"
    shutil.copytree(FIXTURES / "team", checkout)
    return checkout


@pytest.fixture
def kernel(make_kernel, team: Path) -> Kernel:
    config = Config(
        files=(),
        data={"team": {"checkout": str(team)}, "graph": {"time_zone": "Europe/Lisbon"}, "report": {"user": "costas"}},
    )
    return make_kernel(builtins=True, config=config)


def test_the_graph_of_the_scenario_is_the_committed_fixture(kernel):
    code, graph = hz(kernel, "graph", "--at", FRIDAY)
    assert code == 0, graph
    assert graph == json.loads(EXPECTED.read_text(encoding="utf-8"))


def test_the_web_apps_fixture_is_the_same_graph():
    assert json.loads(WEB_FIXTURE.read_text(encoding="utf-8")) == json.loads(EXPECTED.read_text(encoding="utf-8"))


def test_the_fixture_matches_the_committed_json_schema():
    jsonschema.validate(json.loads(EXPECTED.read_text(encoding="utf-8")), SCHEMA)


def test_the_graph_shows_where_the_flow_waits_and_each_promise(kernel):
    _, document = hz(kernel, "graph", "--at", FRIDAY)
    graph = document["graph"]
    assert graph["directed"] is True
    assert graph["type"] == "hz-team"
    (waiting,) = graph["metadata"]["waiting"]
    assert waiting["summary"] == graph["metadata"]["summary"] == "Weekly report waiting on 2 updates"
    states = {node: value["metadata"]["state"] for node, value in graph["nodes"].items()}
    assert states["heinzel:priya-tracker"] == "waiting_for_approval"
    assert states["heinzel:costas-tracker"] == "offline"
    assert states["contract:team/tracker-update#row"] == "offline"  # the worst of its rows
    assert states["contract:team/weekly-report#report"] == "fell_back"
    row = graph["nodes"]["contract:team/tracker-update#row/priya"]["metadata"]
    assert row["parent"] == "contract:team/tracker-update#row"
    assert row["deadline"] == "2026-10-23T16:00:00Z"  # the report's run on Friday at 17:00 in Lisbon
    roadmap = next(e for e in graph["edges"] if e["target"] == "https://docs.acme.example/d/roadmap")
    assert (roadmap["metadata"]["declared"], roadmap["metadata"]["observed"]) == (False, True)
    weeks = {w["week"]: w for w in graph["metadata"]["weeks"]}
    assert (weeks["2026-W42"]["on_time"], weeks["2026-W42"]["late"]) == (1, 1)
    assert weeks["2026-W43"]["fell_back"] == 1


def test_no_description_or_content_reaches_the_graph(kernel):
    _, document = hz(kernel, "graph", "--at", FRIDAY)
    assert "Drafted this week's row" not in json.dumps(document)
    assert "description" not in json.dumps(document)


def test_filters_cut_the_graph_around_what_they_select(kernel):
    _, document = hz(kernel, "graph", "--at", FRIDAY, "--heinzel", "priya-tracker")
    assert set(document["graph"]["nodes"]) == {
        "heinzel:priya-tracker",
        "contract:team/tracker-update#row/priya",
        "contract:team/tracker-update#row",
    }
    assert document["graph"]["metadata"]["filters"]["heinzel"] == "heinzel:priya-tracker"
    _, document = hz(kernel, "graph", "--at", FRIDAY, "--state", "offline")
    assert {"heinzel:costas-tracker", "contract:team/tracker-update#row"} <= set(document["graph"]["nodes"])
    assert "heinzel:priya-tracker" not in document["graph"]["nodes"]
    _, document = hz(kernel, "graph", "--at", FRIDAY, "--artifact", "contract:team/tracker-update#row")
    assert {"heinzel:priya-tracker", "heinzel:costas-tracker", "heinzel:mira-report"} <= set(document["graph"]["nodes"])


def test_a_filter_naming_nothing_says_what_there_is(kernel):
    code, output = hz(kernel, "graph", "--at", FRIDAY, "--heinzel", "theo-tracker")
    assert code == 1
    assert "No heinzel 'heinzel:theo-tracker'" in str(output)
    assert "heinzel:costas-tracker" in str(output)


def test_a_report_synced_into_the_team_repository_changes_the_graph(kernel, team):
    own = Path(os.environ["HZ_STATE_DIR"]) / "telemetry"
    own.mkdir(parents=True)
    shutil.copy(team / "telemetry" / "costas" / "costas-tracker.jsonl", own)  # the log on Costas's machine
    code, reported = hz(
        kernel,
        "report",
        "approved",
        "costas-tracker",
        "https://tracker.acme.example/boards/7#costas",
        "costas",
        "--contract",
        "team:tracker-update",
        "--part",
        "costas",
    )
    assert code == 0, reported
    assert reported["entry"]["user"] == "costas"
    assert reported["next"] == ["copy your logs into the team repository: hz report sync"]
    code, graph = hz(kernel, "graph")  # your own unsynced log counts for you at once
    assert graph["graph"]["nodes"]["heinzel:costas-tracker"]["metadata"]["state"] in {"on_time", "late"}  # kept, today
    code, synced = hz(kernel, "report", "sync")
    assert code == 0, synced
    (log,) = synced["logs"]
    assert log["path"] == str(team / "telemetry" / "costas" / "costas-tracker.jsonl")
    assert synced["problems"] == []
    copy = (team / "telemetry" / "costas" / "costas-tracker.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(copy[-1])["verb"] == "approved"


def test_sync_refuses_a_team_copy_its_log_doesnt_extend(kernel, team):
    code, _ = hz(kernel, "report", "heartbeat", "costas-tracker")
    assert code == 0
    code, synced = hz(kernel, "report", "sync")
    assert code == 0
    assert "one writer" in synced["problems"][0]
    assert synced["logs"] == []


def test_report_refuses_content_and_secrets(kernel):
    code, output = hz(kernel, "report", "published", "costas-tracker", "/Users/costas/draft.md")
    assert code == 1
    assert "https URL" in str(output)
    code, output = hz(
        kernel,
        "report",
        "read",
        "costas-tracker",
        "https://x.example/a",
        "--description",
        "token ghp_abcdefghijklmnopqrstuv1234",
    )
    assert code == 1
    assert "credential" in str(output)


def test_the_report_commands_are_cli_and_mcp_only_and_the_graph_is_on_the_web_head(kernel):
    rest = TestClient(api.build(kernel), base_url="http://localhost")
    assert rest.get("/graph", params={"at": FRIDAY}).status_code == 404
    assert rest.post("/report/heartbeat", params={"heinzel": "x"}).status_code == 404
    app = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")
    response = app.get("/api/graph", params={"at": FRIDAY}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200, response.text
    assert response.json() == json.loads(EXPECTED.read_text(encoding="utf-8"))
    assert app.post("/api/report/heartbeat", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


def test_the_graph_keeper_is_a_playbook_template_whose_contract_reads_the_graph(kernel):
    code, playbooks = hz(kernel, "heinzel", "playbooks")
    assert code == 0, playbooks
    (keeper,) = [p for p in playbooks["playbooks"] if p["module"] == "graph"]
    assert [t["template"] for t in keeper["templates"]] == ["graph:team-graph-keeper"]
    code, created = hz(kernel, "heinzel", "new", "keeper", "--template", "graph:team-graph-keeper")
    assert code == 0, created
    code, shown = hz(kernel, "heinzel", "show", "me:keeper")
    assert code == 0, shown
    assert shown["ready"] is True
    assert shown["diagnostics"] == []
    assert shown["publishes"] == []  # it nudges its own user; it publishes nothing
    assert {r["requirement"]: r["adapter"] for r in shown["requirements"]}["team-graph:read"] == "hz-graph"
    code, run = hz(kernel, "heinzel", "wake", "me:keeper", "--dry-run")
    assert code == 0, run
    graph = run["packet"]["inputs"]["graph"]["data"]["graph"]
    assert graph["type"] == "hz-team"
    assert "team-payments@acme.example" not in json.dumps(run["packet"])
