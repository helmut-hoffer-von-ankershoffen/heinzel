"""The CI manager probe fails closed without issuing service actions."""

from types import SimpleNamespace

import pytest

from tests.integration import linux_user_manager_probe as probe

pytestmark = pytest.mark.unit


def test_probe_checks_each_disposable_unit_twice_with_read_only_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(probe, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(probe, "_linux_user_manager_identity", lambda: (1, 2, 3, 4))
    monkeypatch.setattr(probe.Path, "is_file", lambda _: True)

    def run(command):
        calls.append(command)
        name = command[-1]
        output = (
            "Version=257\n"
            if name == "--property=Version"
            else f"Id={name}\nLoadState=not-found\nActiveState=inactive\nSubState=dead\nJob=\n"
        )
        return SimpleNamespace(returncode=0, truncated=False, stdout=output)

    monkeypatch.setattr(probe, "_quiescence_command", run)
    version, units = probe._observe("heinzel-probe-12345678")
    assert version == "Version=257"
    assert len(units) == 2
    assert len(calls) == 6
    assert all(command[0] == "/usr/bin/systemctl" and "show" in command for command in calls)


def test_probe_denies_missing_user_bus_before_any_command(monkeypatch):
    monkeypatch.setattr(probe, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(probe, "_linux_user_manager_identity", lambda: None)
    monkeypatch.setattr(probe, "_quiescence_command", lambda _command: pytest.fail("unexpected query"))
    with pytest.raises(probe.ProbeBlockedError, match="manager socket is missing"):
        probe._observe("heinzel-probe-12345678")
