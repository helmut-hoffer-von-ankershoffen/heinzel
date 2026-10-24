"""What the team's contracts promise the team: only valid team contracts, their artifact, reads and schedule."""

import shutil
from pathlib import Path

import pytest

from heinzel import Config, Settings, TeamPromises
from heinzel.modules.workflow import ContractPromises, WorkflowService, WorkflowSettings

pytestmark = pytest.mark.unit

TEAM = Path(__file__).parents[2] / "fixtures" / "graph" / "team" / "workflows"


def test_valid_team_contracts_promise_their_artifact_and_personal_or_broken_ones_nothing(make_kernel, tmp_path):
    team, me = tmp_path / "team", tmp_path / "me"
    shutil.copytree(TEAM, team / "workflows")
    (team / "workflows" / "broken.prose.md").write_text("---\nname: broken\n---\n", encoding="utf-8")
    shutil.copytree(TEAM, me)
    data = {"workflow": {"user_dir": str(me), "team_repository": str(team)}}
    kernel = make_kernel(builtins=True, config=Config(files=(), data=data))
    service = WorkflowService(
        kernel, kernel.resolve(WorkflowSettings), Config(files=(), data={}).settings(Settings, "hz")
    )
    promises = ContractPromises(service).promises()
    assert [p.contract for p in promises] == ["team:tracker-update", "team:weekly-report"]
    row, report = promises
    assert (row.writes, row.schedule, row.version, row.reads) == (
        "contract:team/tracker-update#row",
        "0 12 * * WED",
        "1.0.0",
        (),
    )
    assert (report.reads, report.publishes, report.title) == (
        ("contract:team/tracker-update#row",),
        ("teamwork.send-report",),
        "Weekly report",
    )
    assert kernel.resolve(TeamPromises).promises() == promises
