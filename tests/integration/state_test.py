"""`hz system state` through the CLI head, pushing with real git to a local bare repository. Offline.

The personal state repository is a bare repository on disk, which git treats like any remote; nothing reaches the
network, and no `gh` is needed to push.
"""

import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heinzel import HeinzelError
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


def test_foreign_fast_forward_with_identical_tree_is_refused(remote, tmp_path):
    first = hz("system", "state", "push")[1]
    other = tmp_path / "foreign"
    git("clone", "-q", "-b", first["branch"], remote, other)
    git("-C", other, "commit", "-q", "--allow-empty", "-m", "same data foreign owner")
    git("-C", other, "push", "-q")

    code, result = hz("system", "state", "push")

    assert code == 1
    assert "HZS001" in result["output"]
    assert "same data foreign owner" in git("-C", remote, "log", "-1", "--format=%s", first["branch"])


@pytest.mark.parametrize(
    "attack", ["extra", "symlink", "malformed", "oversized", "manifest", "binary", "missing-manifest"]
)
def test_remote_trees_are_refused_without_copying_any_foreign_file(remote, tmp_path, attack):
    first = hz("system", "state", "push")[1]
    state = _service.StateService.for_user()
    clone = state._sink().clone
    before = (clone / "presence" / f"{_month()}.jsonl").read_bytes()
    other = tmp_path / "foreign"
    git("clone", "-q", "-b", first["branch"], remote, other)
    target = other / "presence" / f"{_month()}.jsonl"
    if attack == "extra":
        (other / "unexpected").write_text("private foreign content")
    elif attack == "symlink":
        target.unlink()
        target.symlink_to(tmp_path / "outside")
    elif attack == "malformed":
        target.write_text('{"path":"/private/example"}\n')
    elif attack == "manifest":
        (other / "hz-state.json").write_text("[]")
    elif attack == "missing-manifest":
        (other / "hz-state.json").unlink()
    elif attack == "binary":
        target.write_bytes(b"\xff\n")
    else:
        target.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    git("-C", other, "add", "-A")
    git("-C", other, "commit", "-qm", "foreign tree")
    git("-C", other, "push", "-q")

    code, result = hz("system", "state", "push")

    assert code == 1
    assert "HZS001" in result["output"]
    assert (clone / "presence" / f"{_month()}.jsonl").read_bytes() == before
    assert not (clone / "unexpected").exists()


def _another_day_this_month() -> date:
    """A presence line the first push has not written: that push already noted today, and one per day is kept."""
    today = datetime.now().astimezone().date()
    return today.replace(day=2 if today.day == 1 else 1)


def test_a_failed_network_push_keeps_lines_pending_and_success_resets_backoff(remote):
    first = hz("system", "state", "push")[1]
    state = _service.StateService.for_user()
    state.streams.note_presence(_another_day_this_month(), "0.4.0")
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o700)

    assert hz("system", "state", "push")[0] == 1
    failed = state.status()
    hook.unlink()
    assert hz("system", "state", "push")[0] == 0
    succeeded = state.status()

    assert failed.failures == 1
    assert sum(s.pending for s in failed.streams) == 1
    assert succeeded.failures == 0
    assert all(s.pending == 0 for s in succeeded.streams)
    assert first["commit"] != git("-C", remote, "rev-parse", first["branch"]).strip()


def test_changing_handle_uses_a_new_orphan_branch_and_pins_remote_and_head(remote):
    first = hz("system", "state", "push")[1]
    clone = _service.StateService.for_user()._sink().clone
    assert hz("system", "config", "set", "report.user", "theo")[0] == 0
    code, second = hz("system", "state", "push")
    new_clone = _service.StateService.for_user()._sink().clone
    git("-C", new_clone, "remote", "set-url", "origin", "/nonexistent")
    git("-C", new_clone, "symbolic-ref", "HEAD", "refs/heads/unexpected")
    assert hz("system", "state", "push")[0] == 0

    assert code == 0
    assert clone != new_clone
    assert first["branch"].startswith("hz/mira/")
    assert second["branch"].startswith("hz/theo/")
    assert git("-C", new_clone, "symbolic-ref", "HEAD").strip() == "refs/heads/" + second["branch"]
    assert git("-C", new_clone, "remote", "get-url", "origin").strip() == str(remote)
    assert git("-C", remote, "rev-list", "--count", second["branch"]).strip() == "1"


def test_pushed_commit_timestamps_have_no_time_of_day(remote):
    branch = hz("system", "state", "push")[1]["branch"]
    dates = git("-C", remote, "log", "--format=%aI %cI", branch).split()
    assert all(
        datetime.fromisoformat(date).hour
        == datetime.fromisoformat(date).minute
        == datetime.fromisoformat(date).second
        == 0
        for date in dates
    )


def test_an_unconfigured_daemon_job_does_not_accumulate_receipts():
    code, tick = hz("system", "daemon", "tick", "--job", "system.state:push")
    [run] = tick["runs"]
    assert code == 0
    assert run["disposition"] == "skipped"
    assert run["receipt"] is None
    assert not list((Settings().data_dir / "receipts").glob("system.state*"))


@pytest.mark.parametrize("mutation", ["rewind", "deleted", "diverged"])
def test_remote_rewrites_and_deletion_are_refused(remote, tmp_path, mutation):
    state = _service.StateService.for_user()
    first = hz("system", "state", "push")[1]
    state.streams.note_presence(_another_day_this_month(), "0.4.0")
    second = hz("system", "state", "push")[1]
    assert first["commit"] != second["commit"]
    branch = first["branch"]
    if mutation == "deleted":
        git("-C", remote, "update-ref", "-d", "refs/heads/" + branch)
    elif mutation == "rewind":
        git("-C", remote, "update-ref", "refs/heads/" + branch, first["commit"])
    else:
        other = tmp_path / "foreign"
        git("clone", "-q", "-b", branch, remote, other)
        git("-C", other, "reset", "--hard", first["commit"])
        git("-C", other, "commit", "-q", "--allow-empty", "-m", "divergent foreign")
        foreign = git("-C", other, "rev-parse", "HEAD").strip()
        git("-C", remote, "fetch", "-q", other, branch)
        git("-C", remote, "update-ref", "refs/heads/" + branch, foreign)
    code, result = hz("system", "state", "push")
    assert code == 1
    assert "HZS001" in result["output"]
    assert state.status().refused
    assert state.health()[0].status.value == "down"


def test_a_crash_after_commit_before_receipt_never_claims_ownership(remote, monkeypatch):
    from heinzel.modules.system.state._sink import Sink

    def crash(self, record):
        msg = "interrupted before private receipt"
        raise OSError(msg)

    with monkeypatch.context() as patch:
        patch.setattr(Sink, "_save_ownership", crash)
        with pytest.raises(OSError, match="interrupted"):
            _service.StateService.for_user().push()
    code, result = hz("system", "state", "push")
    assert code == 1
    assert "HZS001" in result["output"]
    assert not git("-C", remote, "branch", "--list").strip()


def test_a_crash_after_server_acceptance_is_reconciled_using_owned_commit(remote, monkeypatch):
    from heinzel.modules.system.state._sink import Sink

    original = Sink._save_ownership

    def crash(self, record):
        if record.confirmed is not None:
            msg = "interrupted after server acceptance"
            raise OSError(msg)
        original(self, record)

    with monkeypatch.context() as patch:
        patch.setattr(Sink, "_save_ownership", crash)
        with pytest.raises(OSError, match="interrupted"):
            _service.StateService.for_user().push()
    code, pushed = hz("system", "state", "push")
    assert code == 0, pushed
    assert not pushed["pushed"]
    assert all(stream.pending == 0 for stream in _service.StateService.for_user().status().streams)


def test_push_and_reset_use_one_cross_process_lock(remote):
    import sys

    push_code = """
import sys
from heinzel.modules.system.state._service import StateService, _runner
state = StateService.for_user()
runner = _runner(state.user)
def run(command, cwd):
    if "push" in command:
        print("pushing", flush=True)
        sys.stdin.readline()
    return runner(command, cwd)
state._run = run
state.push()
print("pushed", flush=True)
"""
    reset_code = """
import fcntl, os
from heinzel.modules.system.state._service import StateService
state = StateService.for_user()
fd = os.open(state.streams.root / "push.lock", os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("locked", flush=True)
else:
    raise AssertionError("push has no process lock")
finally:
    os.close(fd)
state.reset()
print("reset", flush=True)
"""
    push = subprocess.Popen(
        [sys.executable, "-c", push_code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    reset = None
    try:
        assert push.stdout.readline().strip() == "pushing"
        reset = subprocess.Popen(
            [sys.executable, "-c", reset_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        assert reset.stdout.readline().strip() == "locked"
        output, error = push.communicate("continue\n", timeout=10)
        assert push.returncode == 0, error
        assert "pushed" in output
        output, error = reset.communicate(timeout=10)
        assert reset.returncode == 0, error
        assert "reset" in output
    finally:
        for process in [push, reset]:
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    state = _service.StateService.for_user()
    archives = list(state._sink().clone.parent.glob("*.moved-*"))
    assert len(archives) == 1
    receipt = json.loads((archives[0] / ".git" / "hz-owned.json").read_bytes())
    assert receipt["confirmed"] == git("-C", remote, "rev-parse", state.branch).strip()


def test_state_operations_require_private_identity_on_all_network_heads():
    from fastapi.testclient import TestClient

    from heinzel import CurrentUser
    from heinzel.heads import api
    from heinzel.heads._exposure import MCP_HTTP, exposed
    from heinzel.heads.api._auth import ApiBearer

    kernel = boot()
    user = kernel.resolve(CurrentUser)
    credential = ApiBearer.issue(user.owner(), "state-test")
    public = TestClient(api.build(kernel), base_url="http://localhost")
    private = TestClient(api.build(kernel, credentials=credential, audience="state-test"), base_url="http://localhost")
    auth = {"Authorization": f"Bearer {credential.secret}"}
    assert public.get("/system/state/status").status_code == 404
    assert public.post("/system/state/push").status_code == 404
    assert private.get("/system/state/status").status_code == 401
    assert private.get("/system/state/status", headers=auth).status_code == 200
    assert not any(item.record.qualname == "system.state" for item in exposed(kernel, MCP_HTTP))


def test_authenticated_api_streams_note_presence_while_ambient_api_does_not():
    from fastapi.testclient import TestClient

    from heinzel import CurrentUser
    from heinzel.heads import api
    from heinzel.heads.api._auth import ApiBearer

    kernel = boot()
    state = kernel.resolve(_service.StateService)
    public = TestClient(api.build(kernel), base_url="http://localhost")
    assert public.get("/system/info").status_code == 200
    assert not state.streams.segments("presence")
    credential = ApiBearer.issue(kernel.resolve(CurrentUser).owner(), "state-test")
    private = TestClient(api.build(kernel, credentials=credential, audience="state-test"), base_url="http://localhost")
    assert (
        private.post("/stream/system/info", headers={"Authorization": f"Bearer {credential.secret}"}).status_code == 200
    )
    assert len(state.streams.segments("presence")) == 1
    assert len(state.streams.segments("presence")[0].data.splitlines()) == 1


def test_an_unconfigured_day_has_no_state_receipts(tmp_path):
    from datetime import UTC, timedelta

    from heinzel.modules.system.daemon import DaemonService, DaemonSettings

    kernel = boot()
    now = datetime(2026, 10, 24, tzinfo=UTC)
    daemon = DaemonService(kernel, kernel.settings, DaemonSettings(), clock=lambda: now, zone=UTC)
    for _ in range(96):
        report = daemon.tick(job="system.state:push")
        assert all(run.disposition.value == "skipped" and run.receipt is None for run in report.runs)
        now += timedelta(minutes=15)
    assert not list(daemon.receipts_dir.rglob("*.json"))


def test_private_state_record_corruption_is_down_and_never_silently_retried(remote):
    state = _service.StateService.for_user()
    state.streams.machine()
    state._push_file().write_text('{"repository":true,"failures":"invalid"}')
    code, result = hz("system", "daemon", "tick", "--job", "system.state:push")
    assert code == 1
    [run] = result["runs"]
    assert run["disposition"] == "failed"
    assert run["receipt"]
    assert "HZS001" in run["error"]
    assert state.status().refused
    assert state.health()[0].status.value == "down"


def test_ambient_config_and_doctor_cannot_disclose_or_redirect_the_private_state_sink(remote):
    from fastapi.testclient import TestClient

    from heinzel.heads import api

    client = TestClient(api.build(boot()), base_url="http://localhost")
    report = client.get("/system/config/show")
    assert str(remote) not in report.text
    assert client.get("/system/config/get", params={"key": "system.state.personal"}).status_code == 400
    assert (
        client.post(
            "/system/config/set", params={"key": "system.state.personal", "value": "https://example.test/foreign"}
        ).status_code
        == 400
    )
    assert client.post("/system/config/unset", params={"key": "system.state.personal"}).status_code == 400
    doctor = client.get("/system/doctor")
    assert str(remote) not in doctor.text
    assert _service.StateService.for_user().settings.personal == str(remote)


def test_two_processes_pushing_share_the_same_owned_history(remote):
    import sys

    state = _service.StateService.for_user()
    state.note_presence("system")
    code = (
        "from heinzel.modules.system.state._service import StateService; "
        "print(StateService.for_user().push().model_dump_json())"
    )
    processes = [
        subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    try:
        results = []
        for process in processes:
            output, error = process.communicate(timeout=10)
            assert process.returncode == 0, error
            results.append(json.loads(output))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    assert sorted(result["pushed"] for result in results) == [False, True]
    assert git("-C", remote, "rev-list", "--count", state.branch).strip() == "1"
    assert all(stream.pending == 0 for stream in state.status().streams)


def test_shortening_both_local_stream_and_worktree_cannot_erase_confirmed_history(remote):
    first = hz("system", "state", "push")[1]
    state = _service.StateService.for_user()
    path = Path("presence") / f"{_month()}.jsonl"
    (state.streams.root / path).write_bytes(b"")
    (state._sink().clone / path).write_bytes(b"")
    with pytest.raises(HeinzelError, match="HZS001"):
        state.push()
    assert git("-C", remote, "rev-parse", first["branch"]).strip() == first["commit"]
    assert git("-C", remote, "show", f"{first['branch']}:{path}").strip()
