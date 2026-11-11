"""Offline contracts for the bounded sign-in diagnostic; no browser or server is launched."""

from io import BytesIO

import pytest

from tests.e2e import _signin_trace_server as trace
from tests.e2e import signin_pair_diagnostic_test as pair

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
