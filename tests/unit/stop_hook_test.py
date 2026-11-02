"""The Stop hook keeps its check, but adds no load on a host that queues heavy checks with hz-gate."""

import importlib.util
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

SPEC = importlib.util.spec_from_file_location(
    "check_before_stop", Path(__file__).resolve().parents[2] / ".claude/hooks/check_before_stop.py"
)
assert SPEC is not None
assert SPEC.loader is not None
hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hook)


def _run(monkeypatch, tmp_path, *, gate, check):
    tools = {"make": "make", "uv": "uv", "hz-gate": "/bin/hz-gate" if gate else None}
    monkeypatch.setattr(hook.shutil, "which", tools.get)
    monkeypatch.setattr(hook.sys, "stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))
    calls = []

    def run(command, **kwargs):
        if command[0] == "git":
            return SimpleNamespace(stdout=" M a.py\0")
        calls.append((command, kwargs["timeout"]))
        return check()

    monkeypatch.setattr(hook.subprocess, "run", run)
    return hook.main(), calls


def _passes():
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def _fails():
    return SimpleNamespace(returncode=1, stdout="broken", stderr="")


def _no_turn():
    command = "hz-gate"
    raise subprocess.TimeoutExpired(command, 300)


def test_the_check_goes_through_the_gate_when_it_exists(monkeypatch, tmp_path):
    code, calls = _run(monkeypatch, tmp_path, gate=True, check=_passes)
    assert code == 0
    assert calls[0][0][:2] == ["/bin/hz-gate", "make"]


def test_a_failure_through_the_gate_still_stops_the_agent(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, gate=True, check=_fails)[0] == 2


def test_no_turn_in_the_gate_defers_to_pre_push(monkeypatch, tmp_path, capsys):
    assert _run(monkeypatch, tmp_path, gate=True, check=_no_turn)[0] == 0
    assert "pre-push hook" in capsys.readouterr().err


def test_without_a_gate_the_check_runs_directly(monkeypatch, tmp_path):
    code, calls = _run(monkeypatch, tmp_path, gate=False, check=_passes)
    assert code == 0
    assert calls[0][0] == ["make", "check-fast"]
