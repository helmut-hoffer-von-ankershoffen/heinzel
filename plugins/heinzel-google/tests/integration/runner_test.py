"""The real `Gws` runner against a stand-in `gws` executable: arguments, environment, timeout and output bounds."""

import json
import sys
import time

import pytest
from heinzel_google import GoogleSession, GoogleSettings, Gws, _gws

pytestmark = pytest.mark.integration

SCRIPT = """\
import json, os, sys, time
mode_file = os.path.join(os.path.dirname(sys.argv[0]), "mode")
mode = open(mode_file).read().strip() if os.path.exists(mode_file) else "echo"
if sys.argv[1:] == ["--version"]:
    print("gws 0.22.5")
elif mode == "echo":
    names = sorted(k for k in os.environ if k.startswith("HZ_CREDENTIAL_"))
    print(json.dumps({"argv": sys.argv[1:], "credentials": names}))
    sys.stderr.write("note\\n")
elif mode == "sleep":
    time.sleep(30)
elif mode == "flood":
    for _ in range(10):
        sys.stdout.write("x" * 100_000)
        sys.stdout.flush()
elif mode == "auth":
    print(json.dumps({"error": {"code": 401, "message": "Access denied.", "reason": "authError"}}))
    sys.exit(2)
"""


@pytest.fixture
def stand_in(tmp_path):
    script = tmp_path / "gws"
    script.write_text(f"#!{sys.executable}\n{SCRIPT}", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def test_arguments_arrive_as_a_list_and_credentials_stay_home(stand_in, monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_OTHER_TOKEN", "never-passed-on")
    process = Gws().run(stand_in, ["drive", "files", "get", "--params", '{"fileId":"x y; echo hi"}'], 10)
    assert process.exit_code == 0
    assert json.loads(process.stdout) == {
        "argv": ["drive", "files", "get", "--params", '{"fileId":"x y; echo hi"}'],
        "credentials": [],
    }
    assert process.stderr == b"note\n"
    assert not process.truncated


def test_a_run_past_its_timeout_is_stopped(stand_in, tmp_path, monkeypatch):
    (tmp_path / "mode").write_text("sleep")
    started = time.monotonic()
    process = Gws().run(stand_in, [], 0.5)
    assert process.exit_code is None
    assert time.monotonic() - started < 5


def test_output_past_the_limit_is_cut(stand_in, tmp_path, monkeypatch):
    (tmp_path / "mode").write_text("flood")
    monkeypatch.setattr(_gws, "MAX_BYTES", 1000)
    process = Gws().run(stand_in, [], 10)
    assert (len(process.stdout), process.truncated) == (1000, True)


def test_the_session_maps_a_real_auth_failure(stand_in, tmp_path, monkeypatch):
    (tmp_path / "mode").write_text("auth")
    session = GoogleSession(GoogleSettings(gws_path=stand_in), Gws())
    with pytest.raises(Exception, match="not signed in"):
        session.get("Google Drive", ("drive", "files", "get"), {"fileId": "x"}, "file x")


def test_sleep_waits(monkeypatch):
    waited: list[float] = []
    monkeypatch.setattr(_gws.time, "sleep", waited.append)
    Gws().sleep(2.0)
    assert waited == [2.0]


@pytest.fixture
def shell_stand_in(tmp_path):
    def make(body: str) -> str:
        script = tmp_path / "gws-sh"
        script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        script.chmod(0o755)
        return str(script)

    return make


def test_gws_runs_from_a_neutral_directory(shell_stand_in, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # a project directory, perhaps with a .env gws would read
    process = Gws().run(shell_stand_in("pwd"), [], 10)
    assert process.stdout.decode().strip() == _gws.NEUTRAL


def test_a_run_that_ignores_sigterm_is_killed(shell_stand_in, monkeypatch):
    monkeypatch.setattr(_gws, "GRACE", 0.3)
    started = time.monotonic()
    # Two seconds to install the trap before SIGTERM arrives, also on a loaded machine.
    process = Gws().run(shell_stand_in("trap '' TERM\necho ready\nwhile :; do sleep 1; done"), [], 2.0)
    assert process.exit_code is None
    assert 2.0 + 0.3 <= time.monotonic() - started < 10  # SIGTERM was ignored, SIGKILL ended it


def test_output_held_open_by_an_escaped_child_counts_as_cut(shell_stand_in, monkeypatch):
    monkeypatch.setattr(_gws, "GRACE", 0.3)
    child = "import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'], "
    child += "start_new_session=True); print('partial')"
    process = Gws().run(shell_stand_in(f'{sys.executable} -c "{child}"'), [], 10)
    assert process.truncated


def test_login_runs_with_the_terminal_and_returns_its_exit_code(shell_stand_in):
    assert Gws().login(shell_stand_in("exit 3"), ["auth", "login"], 10) == 3
    assert Gws().login(shell_stand_in("sleep 5"), [], 0.2) is None


def test_a_login_that_ignores_sigterm_is_killed(shell_stand_in, monkeypatch):
    monkeypatch.setattr(_gws, "GRACE", 0.3)
    assert Gws().login(shell_stand_in("trap '' TERM\nwhile :; do sleep 1; done"), [], 2.0) is None
