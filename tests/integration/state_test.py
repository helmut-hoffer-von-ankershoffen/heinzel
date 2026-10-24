"""`hz system state` through the CLI head, pushing with real git to a local bare repository. Offline.

The personal state repository is a bare repository on disk, which git treats like any remote; nothing reaches the
network, and no `gh` is needed to push.
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heinzel.heads.cli import build
from heinzel.kernel import Settings, boot
from heinzel.modules.system.state import _service

pytestmark = pytest.mark.integration


def hz(*args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(build(boot()), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, {"output": result.output}


def git(*args: str | Path) -> str:
    return subprocess.run(["git", *map(str, args)], check=True, capture_output=True, text=True, timeout=30).stdout


@pytest.fixture
def remote(tmp_path, monkeypatch) -> Path:
    """Mira's personal state repository, empty, as a bare repository; git's own identity is someone else's."""
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Mira Example")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "mira.example@acme.example")
    monkeypatch.setattr(_service, "JITTER", 0)  # the daemon's random wait, so a tick doesn't take a minute
    bare = tmp_path / "heinzel-state-mira.git"
    git("init", "-q", "--bare", bare)
    assert hz("system", "config", "set", "system.state.personal", str(bare))[0] == 0
    assert hz("system", "config", "set", "report.user", "mira")[0] == 0
    return bare


def test_the_first_push_creates_this_machines_orphan_branch_and_the_next_only_fast_forwards(remote):
    code, first = hz("system", "state", "push")
    again = hz("system", "state", "push")[1]
    branch = first["branch"]
    files = git("-C", remote, "ls-tree", "-r", "--name-only", branch).split()
    manifest = json.loads(git("-C", remote, "show", f"{branch}:hz-state.json"))
    author = git("-C", remote, "log", "-1", "--format=%an <%ae> / %cn <%ce>", branch).strip()

    assert code == 0, first
    assert branch.startswith("hz/mira/")
    assert (first["pushed"], first["lines"]) == (True, 2)
    assert again["pushed"] is False
    assert git("-C", remote, "branch", "--list").split() == [branch]  # nothing on main, nothing else
    assert git("-C", remote, "rev-list", "--max-parents=0", branch).strip() == first["commit"]  # an orphan
    assert sorted(files) == sorted(["hz-state.json", f"presence/{_month()}.jsonl", f"adoption/{_month()}.jsonl"])
    assert manifest["user"] == "mira"
    assert manifest["machine"] == branch.rsplit("/", 1)[1]
    assert author == "mira <mira@hz.invalid> / mira <mira@hz.invalid>"


def test_new_lines_go_out_as_one_more_commit_on_top(remote):
    first = hz("system", "state", "push")[1]
    segment = Settings().state_dir / "state" / "presence" / f"{_month()}.jsonl"
    segment.write_text(segment.read_text() + '{"v":1,"date":"' + _month() + '-01","hz":"0.4.0","kind":"active"}\n')

    code, second = hz("system", "state", "push")

    assert code == 0, second
    assert (second["pushed"], second["lines"]) == (True, 1)
    assert git("-C", remote, "rev-parse", f"{second['commit']}^").strip() == first["commit"]


def test_a_commit_this_machine_didnt_make_stops_pushing_until_the_branch_is_gone_and_reset(remote, tmp_path):
    branch = hz("system", "state", "push")[1]["branch"]
    other = tmp_path / "someone-else"
    git("clone", "-q", "-b", branch, remote, other)
    (other / "presence" / f"{_month()}.jsonl").write_text('{"v":1,"date":"2026-01-01","hz":"0.0.1","kind":"active"}\n')
    git("-C", other, "commit", "-qam", "not mira's machine")
    git("-C", other, "push", "-q", "origin", branch)

    refused = hz("system", "state", "push")
    status = hz("system", "state", "status")[1]
    git("-C", remote, "branch", "-q", "-D", branch)
    reset = hz("system", "state", "reset")
    code, pushed = hz("system", "state", "push")

    assert refused[0] == 1
    assert "HZS001" in refused[1]["output"]
    assert status["refused"]
    assert reset[0] == 0
    assert code == 0, pushed
    assert pushed["lines"] == 2
    assert "not mira's machine" not in git("-C", remote, "log", "--format=%s", branch)


def test_the_daemon_tick_runs_the_push_job(remote):
    code, tick = hz("system", "daemon", "tick", "--job", "system.state:push")
    status = hz("system", "state", "status")[1]

    assert code == 0, tick
    assert [run["disposition"] for run in tick["runs"]] == ["rendered"]
    assert status["last_push"] is not None
    assert all(stream["pending"] == 0 for stream in status["streams"])


def test_a_command_notes_presence_and_the_daemons_own_commands_dont():
    presence = Settings().state_dir / "state" / "presence"

    hz("system", "daemon", "status")
    unattended = presence.exists() and any(presence.iterdir())
    hz("system", "info")
    lines = (presence / f"{_month()}.jsonl").read_text().splitlines()
    hz("system", "info")

    assert not unattended
    assert len(lines) == 1
    assert json.loads(lines[0])["kind"] == "active"
    assert (presence / f"{_month()}.jsonl").read_text().splitlines() == lines


def test_without_a_repository_nothing_leaves_and_status_says_so():
    code, status = hz("system", "state", "status")
    refused = hz("system", "state", "push")

    assert code == 0
    assert status["repository"] is None
    assert refused[0] == 1
    assert "Nothing leaves this machine" in refused[1]["output"]


def _month() -> str:
    return f"{datetime.now().astimezone():%Y-%m}"  # the local date, as the service writes it


def text(*args: str) -> str:
    result = CliRunner().invoke(build(boot()), list(args))
    assert result.exit_code == 0, result.output
    return " ".join(result.output.split())


def test_the_text_output_says_where_state_goes_what_is_pending_and_what_went_out(remote):
    before = text("system", "state", "status")
    pushed = text("system", "state", "push")
    current = text("system", "state", "push")
    after = text("system", "state", "status")
    doctor = hz("system", "doctor")[1]

    assert "not pushed yet" in before
    assert "hz system state push" in before
    assert pushed.startswith("Pushed 2 new line(s) in 2 segment(s) to hz/mira/")
    assert "is current; nothing to push" in current
    assert "last push:" in after
    [check] = doctor["checks"]["system.state"]
    assert (check["name"], check["status"]) == ("state repository", "up")


def test_a_failing_push_shows_in_status(remote, tmp_path):
    remote.rename(tmp_path / "gone.git")

    failed = hz("system", "state", "push")
    status = text("system", "state", "status")

    assert failed[0] == 1
    assert "failing (1 attempt(s))" in status
