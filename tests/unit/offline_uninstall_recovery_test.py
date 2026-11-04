"""The durable remover resumes partial outcomes using its private copied stdlib script, never the removed SDK."""

import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from heinzel.kernel import _uninstall_recovery as recovery
from heinzel.kernel._tool_uninstall import owned_file
from heinzel.kernel._uninstall_journal import RemovalJournal
from tests.unit.kernel.installation_continuity_test import tool
from tests.unit.offline_uninstall_test import removable

__all__ = ["removable", "tool"]
pytestmark = pytest.mark.unit


@pytest.fixture
def journal(removable, tmp_path):
    state = tmp_path / "state"
    for directory in (state / "ui", state / "daemon"):
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        for name in ("lifecycle.lock", "tick.lock"):
            path = directory / name
            path.write_text("")
            path.chmod(0o600)
    unit = state / "owned-unit"
    unit.write_text("owned definition")
    # Give the private recovery executable its own folder, matching the real preflight stage.
    directory = tmp_path / "recovery"
    directory.mkdir(mode=0o700)
    uv = directory / "uv"
    uv.write_bytes(removable.test_remover.path.read_bytes())
    uv.chmod(0o500)
    captured = owned_file(uv)
    executable = type(removable.test_remover)(uv, hashlib.sha256(captured.content).hexdigest(), captured.identity)
    record = RemovalJournal(
        executable,
        prefix=removable.prefix,
        binary=removable.tool_destination()[1],
        state=state,
        captured=((owned_file(unit),), removable.tool_entrypoints()),
    )
    record.begin()
    return record, removable, unit


def copy_module(record):
    spec = importlib.util.spec_from_file_location("standalone_removal", record.directory / "recover.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def successful_uv(tool, calls):
    def run(command, **options):
        calls.append((command, options))
        assert command[1:] == ["--offline", "--no-config", "--no-python-downloads", "tool", "uninstall", "heinzel"]
        for path in tool.tool_entrypoints():
            path.unlink()
        shutil.rmtree(tool.prefix)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    return run


@pytest.mark.parametrize("phase", ["prepared", "stopped", "removing", "removed"])
def test_copied_recovery_handles_interruption_without_importing_the_sdk(journal, monkeypatch, phase):
    record, tool, unit = journal
    record.phase(phase)
    module = copy_module(record)
    calls = []
    monkeypatch.setattr(module, "managers_absent", lambda platform: None)
    monkeypatch.setattr(module.subprocess, "run", successful_uv(tool, calls))
    outcome = module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert outcome["phase"] == "complete"
    assert not tool.prefix.exists()
    assert not unit.exists()
    assert not record.gate.exists()
    assert len(calls) == 1
    assert module.recover(record.directory, module.EXPECTED_AUTHORITY)["phase"] == "complete"
    assert len(calls) == 1


def test_partial_uv_refusal_keeps_definitions_and_gate_then_retries_only_remaining_owned_sdk(journal, monkeypatch):
    record, tool, unit = journal
    module = copy_module(record)
    monkeypatch.setattr(module, "managers_absent", lambda platform: None)
    calls = []

    def partial(command, **options):
        calls.append(command)
        (tool.prefix / "lib/site-packages/example/__init__.py").unlink()
        return subprocess.CompletedProcess(command, 1, b"", b"")

    monkeypatch.setattr(module.subprocess, "run", partial)
    with pytest.raises(ValueError, match="incomplete"):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert record.gate.exists()
    assert unit.exists()
    assert json.loads(record.path.read_text())["phase"] == "removing"
    monkeypatch.setattr(module.subprocess, "run", successful_uv(tool, calls))
    assert module.recover(record.directory, module.EXPECTED_AUTHORITY)["phase"] == "complete"
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["new-file", "changed-file", "foreign-prefix", "changed-authority", "changed-uv"])
def test_recovery_refuses_changed_target_or_authority_before_execution(journal, monkeypatch, change):
    record, tool, unit = journal
    module = copy_module(record)
    monkeypatch.setattr(module, "managers_absent", lambda platform: None)
    monkeypatch.setattr(
        module.subprocess, "run", lambda *args, **kwargs: pytest.fail("Changed recovery must not execute")
    )
    if change == "new-file":
        (tool.prefix / "foreign-user-file").write_text("retain me")
    elif change == "changed-file":
        (tool.prefix / "bin/hz").write_text("changed")
    elif change == "foreign-prefix":
        tool.prefix.rename(tool.prefix.with_name("original"))
        tool.prefix.mkdir()
    elif change == "changed-uv":
        Path(record.value["uv"]).chmod(0o700)
        Path(record.value["uv"]).write_text("changed")
    else:
        changed = json.loads(record.path.read_text())
        changed["prefix"] = str(tool.prefix.parent / "unrelated")
        record.path.write_text(json.dumps(changed))
    with pytest.raises((ValueError, OSError)):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert unit.exists()
    assert record.gate.exists()


@pytest.mark.parametrize("failure", ["unlink", "reload", "gate-sync"])
def test_cleanup_failure_keeps_precise_recoverable_phase_and_does_not_repeat_uv(journal, monkeypatch, failure):
    record, tool, _unit = journal
    module = copy_module(record)
    calls = []
    monkeypatch.setattr(module, "managers_absent", lambda platform: None)
    monkeypatch.setattr(module.subprocess, "run", successful_uv(tool, calls))
    if failure == "reload":
        record.value["platform"] = "linux"
        recovery.write(record.path, record.value)
        module.EXPECTED_AUTHORITY = recovery.authority_digest(record.value)
        monkeypatch.setattr(module, "_manager", lambda *args: (_ for _ in ()).throw(OSError("reload refused")))
    elif failure == "unlink":
        monkeypatch.setattr(module, "_owned_unlink", lambda *args: (_ for _ in ()).throw(OSError("unlink refused")))
    else:
        original_sync = module.sync

        def refuse_gate(path):
            if path == record.gate.parent:
                msg = "directory fsync refused"
                raise OSError(msg)
            return original_sync(path)

        monkeypatch.setattr(module, "sync", refuse_gate)
    with pytest.raises(OSError, match="refused"):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert json.loads(record.path.read_text())["phase"] == "removed"
    assert len(calls) == 1
    monkeypatch.setattr(module, "_owned_unlink", recovery._owned_unlink)
    monkeypatch.setattr(module, "_manager", lambda *args: "")
    monkeypatch.setattr(module, "sync", recovery.sync)
    assert module.recover(record.directory, module.EXPECTED_AUTHORITY)["phase"] == "complete"
    assert len(calls) == 1


def test_recovery_after_sdk_removed_finishes_only_unchanged_original_command_links(journal, monkeypatch):
    record, tool, unit = journal
    module = copy_module(record)
    monkeypatch.setattr(module, "managers_absent", lambda platform: None)
    links = tool.tool_entrypoints()
    shutil.rmtree(tool.prefix)
    assert all(path.is_symlink() for path in links)
    monkeypatch.setattr(
        module.subprocess, "run", lambda *args, **kwargs: pytest.fail("The removed SDK must not run uv again")
    )
    assert module.recover(record.directory, module.EXPECTED_AUTHORITY)["phase"] == "complete"
    assert not any(path.is_symlink() for path in links)
    assert not unit.exists()


def test_recovery_after_sdk_removed_retains_a_replaced_foreign_command_link(journal, monkeypatch, tmp_path):
    record, tool, unit = journal
    module = copy_module(record)
    monkeypatch.setattr(module, "managers_absent", lambda platform: None)
    link = tool.tool_entrypoints()[0]
    shutil.rmtree(tool.prefix)
    foreign = tmp_path / "foreign-script"
    foreign.write_text("retain another command")
    link.unlink()
    link.symlink_to(foreign)
    with pytest.raises(ValueError, match="command link changed"):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert link.is_symlink()
    assert link.resolve() == foreign
    assert foreign.read_text() == "retain another command"
    assert unit.exists()
    assert record.gate.exists()
