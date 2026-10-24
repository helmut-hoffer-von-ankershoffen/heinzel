"""`hz org sync` and doctor's drift check through the CLI head, with a team checkout on disk. Offline."""

import json
import tomllib

import pytest
from typer.testing import CliRunner

from heinzel.heads.cli import build
from heinzel.kernel import boot, user_config_dir

pytestmark = pytest.mark.integration

MANIFEST = "name: payments\nrecommend:\n  bindings: {agent-runner: command}\n  plugins: [heinzel-google]\n"


@pytest.fixture
def team(tmp_path, monkeypatch):
    checkout = tmp_path / "team"
    checkout.mkdir()
    (checkout / "hz.yaml").write_text(MANIFEST)
    config = user_config_dir()
    config.mkdir(parents=True, exist_ok=True)
    (config / "config.toml").write_text(f'[team]\ncheckout = "{checkout}"\n')
    monkeypatch.chdir(tmp_path)
    return checkout


def hz(*args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(build(boot()), [*args, "--json"])
    return result.exit_code, json.loads(result.output)


def user_file() -> dict:
    return tomllib.loads((user_config_dir() / "config.toml").read_text())


def test_sync_shows_recommendations_and_writes_only_what_you_accept(team):
    code, report = hz("org", "sync")
    assert code == 0
    assert [(i["id"], i["state"]) for i in report["items"]] == [
        ("plugin:heinzel-google", "by-hand"),
        ("binding:agent-runner", "pending"),  # bound only by the core's default, so no choice of yours
    ]
    assert "capabilities" not in user_file()
    code, report = hz("org", "sync", "--accept", "all")
    assert code == 0
    assert user_file()["capabilities"] == {"agent_runner": "command"}
    code, report = hz("org", "sync")
    assert report["items"][1]["state"] == "applied"


def test_doctor_reports_drift_from_the_team(team):
    _, doctor = hz("system", "doctor")
    [check] = [c for c in doctor["checks"]["org"] if c["name"] == "recommendations"]
    assert (check["status"], check["fix"]) == ("degraded", "hz org sync")
    assert "plugin:heinzel-google" in check["detail"]
