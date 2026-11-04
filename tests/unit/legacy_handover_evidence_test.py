"""A failed genuine handover exposes fixed facts without disclosing retained output or identities."""

import json
import os
from types import SimpleNamespace

import pytest

from tests.e2e.daemon_legacy_recovery_test import handover_evidence

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("alive", [True, False])
def test_handover_diagnostic_keeps_output_configuration_and_pid_private(tmp_path, monkeypatch, alive):
    settings = SimpleNamespace(state_dir=tmp_path / "state")
    directory = settings.state_dir / "daemon"
    directory.mkdir(parents=True)
    (directory / "install.json").write_text(json.dumps({"handover_id": "fixture-private-history"}))
    (directory / "heartbeat.json").write_text("fixture-private-heartbeat")
    (tmp_path / "manager.json").write_text(json.dumps({"pid": 123456789, "profile": "fixture-private-profile"}))
    (tmp_path / "tick-stdout.txt").write_text("fixture-private-output")
    (tmp_path / "tick-stderr.txt").write_text("")
    calls = []

    def signal(pid, number):
        calls.append((pid, number))
        if not alive:
            raise ProcessLookupError

    monkeypatch.setattr(os, "kill", signal)
    facts = handover_evidence(settings, tmp_path)
    assert calls == [(123456789, 0)]
    assert facts == {
        "marker": True,
        "observation": False,
        "heartbeat": True,
        "handover_id_present": True,
        "manager_has_pid": True,
        "child_alive": alive,
        "stdout_present": True,
        "stdout_nonempty": True,
        "stderr_present": True,
        "stderr_nonempty": False,
    }
    assert "fixture-private" not in json.dumps(facts)
    assert "123456789" not in json.dumps(facts)


def test_absent_child_and_output_do_not_trigger_a_process_probe(tmp_path, monkeypatch):
    settings = SimpleNamespace(state_dir=tmp_path / "state")
    (tmp_path / "manager.json").write_text('{"pid": null}')

    def forbidden(*_args):
        pytest.fail("There is no owned child to probe")

    monkeypatch.setattr(os, "kill", forbidden)
    assert not any(handover_evidence(settings, tmp_path).values())
