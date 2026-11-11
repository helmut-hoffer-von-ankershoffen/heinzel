"""Offline contracts for the bounded sign-in diagnostic; no browser or server is launched."""

import os
import sys
from io import BytesIO

import pytest

from tests.e2e import _signin_pair_diagnostic as pair
from tests.e2e import _signin_trace_server as trace

pytestmark = pytest.mark.unit


def test_pair_preserves_canonical_selection_and_environment(tmp_path):
    source = {
        "PATH": "/bin",
        "HOME": "/fixture",
        "UV_PYTHON": "3.14",
        "SECRET": "never-copy",
        "HZ_CREDENTIAL_TEST": "never-copy",
        "CI": "true",
    }
    env = pair.pair_environment(source, tmp_path / "shots")
    assert env == {
        "PATH": "/bin",
        "HOME": "/fixture",
        "UV_PYTHON": "3.14",
        "UV_FROZEN": "1",
        "HZ_SCREENSHOTS": str(tmp_path / "shots"),
    }
    command = pair.pair_command(tmp_path / "pair.xml")
    assert command[-2:] == list(pair.CASES)
    assert "--session-timeout=900" in command
    assert command[command.index("-n") + 1] == "0"


def test_only_fixed_nonsecret_markers_can_be_reported():
    assert pair.MARKER.fullmatch("SIGN_IN_BROWSER webkit fetch-start 1234567890123")
    assert pair.MARKER.fullmatch("SIGN_IN_SERVER prepare-refused 1234567890123")
    assert pair.MARKER.fullmatch("SIGN_IN_STDERR_PENDING 65536")
    assert pair.MARKER.fullmatch("SIGN_IN_STACK rich/console.py:42")
    for value in (
        "SIGN_IN_BROWSER webkit fetch-start secret",
        "SIGN_IN_STDERR_PENDING 12 token=secret",
        "SIGN_IN_STACK /private/secret.py:3",
        "Bearer secret",
    ):
        assert not pair.MARKER.fullmatch(value)


def test_browser_driver_inherits_the_pytest_environment(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": '{"engines": []}\n', "stderr": ""})()

    monkeypatch.setattr(trace.subprocess, "run", run)
    assert trace._drive_browser(["node", "fixture"], tmp_path, "token") == {"engines": []}
    assert "env" not in calls[0][1]
    assert calls[0][1]["timeout"] == 180


def test_drain_discards_secrets_and_caps_lines_and_markers():
    valid = b"SIGN_IN_SERVER handler-enter 1234567890123\n"
    stream = BytesIO(b"secret-token\n" + b"x" * 5000 + b"\n" + valid * 120)
    process = type("Process", (), {"stdout": stream})()
    markers = []
    pair.drain(process, markers)
    assert markers == [valid.decode().strip()] * 100


def test_isolation_fixture_environment_is_accepted_and_secrets_refused():
    names = (
        "COLUMNS",
        "TERMINAL_WIDTH",
        "_TYPER_FORCE_DISABLE_TERMINAL",
        "GOOGLE_WORKSPACE_CLI_CONFIG_DIR",
        "HZ_CONFIG_HOME",
        "HZ_DATA_DIR",
        "HZ_CACHE_DIR",
        "HZ_STATE_DIR",
        "HZ_LOG_DIR",
        "HZ_SYSTEM_DAEMON__UNIT_DIR",
        "HZ_SYSTEM_CREDENTIAL__BACKEND",
        "HZ_UPDATE_CHECK",
        "HZ_SYSTEM_NOTIFY__NATIVE",
        "HZ_SYSTEM_NOTIFY__DOCTOR",
        "PYTEST_CURRENT_TEST",
    )
    parent = dict.fromkeys(names, "fixture")
    server = {"HZ_SYSTEM_CREDENTIAL__BACKEND": "environment"}
    trace.validate_environment(parent, server)
    with pytest.raises(RuntimeError):
        trace.validate_environment({**parent, "GITHUB_TOKEN": "secret"}, server)
    with pytest.raises(RuntimeError):
        trace.validate_environment(parent, {**server, "HZ_CREDENTIAL_TEST": "secret"})


def test_junit_keeps_only_expected_case_names_and_removes_raw_output(tmp_path):
    path = tmp_path / "pair.xml"
    path.write_text(
        '<testsuite><testcase name="test_client_install_survives_dialog_close_and_reopen"/>'
        '<testcase name="test_local_chatgpt_sign_in_fits_phone_viewports"/>'
        "<system-out>secret</system-out></testsuite>"
    )
    result = pair.summarize_junit(path)
    assert result["valid"] is True
    assert "secret" not in str(result)
    assert not path.exists()


def test_real_pytest_isolation_variables_are_accepted():
    names = (
        "COLUMNS",
        "TERMINAL_WIDTH",
        "_TYPER_FORCE_DISABLE_TERMINAL",
        "GOOGLE_WORKSPACE_CLI_CONFIG_DIR",
        "HZ_CONFIG_HOME",
        "HZ_DATA_DIR",
        "HZ_CACHE_DIR",
        "HZ_STATE_DIR",
        "HZ_LOG_DIR",
        "HZ_SYSTEM_DAEMON__UNIT_DIR",
        "HZ_SYSTEM_CREDENTIAL__BACKEND",
        "HZ_UPDATE_CHECK",
        "HZ_SYSTEM_NOTIFY__NATIVE",
        "HZ_SYSTEM_NOTIFY__DOCTOR",
        "PYTEST_CURRENT_TEST",
    )
    assert all(name in os.environ for name in names)
    trace.validate_environment(
        {name: os.environ[name] for name in names}, {"HZ_SYSTEM_CREDENTIAL__BACKEND": "environment"}
    )


def test_diagnostic_process_drains_without_leaking_output():
    code = "import sys; print('secret'); print('SIGN_IN_SERVER handler-enter 1234567890123'); sys.stdout.flush()"
    status, markers = pair.execute([sys.executable, "-c", code], {"PATH": "/usr/bin:/bin"})
    assert status == 0
    assert markers == ["SIGN_IN_SERVER handler-enter 1234567890123"]
