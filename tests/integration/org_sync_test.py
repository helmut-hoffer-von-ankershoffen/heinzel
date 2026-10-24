"""`hz org sync` and doctor's drift checks through the CLI head, with a team checkout on disk. Offline."""

import json
import tomllib

import pytest
from typer.testing import CliRunner

from heinzel.heads.cli import build
from heinzel.kernel import boot, user_config_dir

pytestmark = pytest.mark.integration

MANIFEST = "name: payments\nrecommend:\n  bindings: {documents: google}\n  plugins: [heinzel-google]\n"


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


def hz(*args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(boot()), [*args, "--json"])
    return result.exit_code, json.loads(result.output)


def test_sync_shows_recommendations_and_writes_only_what_you_accept(team):
    code, report = hz("org", "sync")
    assert code == 0
    assert [(i["id"], i["state"]) for i in report["items"]] == [
        ("plugin:heinzel-google", "by-hand"),
        ("binding:documents", "pending"),
    ]
    assert "capabilities" not in tomllib.loads((user_config_dir() / "config.toml").read_text())
    code, report = hz("org", "sync", "--accept", "binding:documents")
    assert code == 0
    assert tomllib.loads((user_config_dir() / "config.toml").read_text())["capabilities"] == {"documents": "google"}
    code, report = hz("org", "sync")
    assert [(i["id"], i["state"]) for i in report["items"]][1] == ("binding:documents", "applied")


def test_doctor_reports_drift_from_the_team(team):
    _, doctor = hz("system", "doctor")
    names = json.dumps(doctor)
    assert "recommendation binding:documents" in names
    assert "hz org sync --accept binding:documents" in names
