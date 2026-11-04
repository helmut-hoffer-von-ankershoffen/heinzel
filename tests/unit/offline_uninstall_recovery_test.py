"""The durable remover resumes partial outcomes using its private copied stdlib script, never the removed SDK."""

import hashlib
import importlib.util
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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
        captured=((owned_file(unit),), removable.tool_entrypoints(), {}),
    )
    record.begin()
    return record, removable, unit


def copy_module(record):
    spec = importlib.util.spec_from_file_location("standalone_removal", record.directory / "recover.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # This unit harness imports the copied script rather than launching its recorded interpreter.
    # Model that runtime identity locally, as the separate root-owned-system-Python cases already do.
    module.sys = SimpleNamespace(executable=record.value["python"])
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


@pytest.mark.parametrize(
    "change", ["new-file", "changed-file", "foreign-prefix", "changed-authority", "changed-uv", "changed-definition"]
)
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
    elif change == "changed-definition":
        unit.write_text("foreign definition")
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
    assert json.loads(record.path.read_text())["phase"] == ("cleaned" if failure == "gate-sync" else "removed")
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


@pytest.mark.parametrize(
    "change",
    ["none", "fragment", "argv", "profile", "extra-profile", "directory", "enabled", "unknown", "pid", "duplicate"],
)
def test_linux_recovery_requires_exact_retained_loaded_identity_and_quiescence(journal, monkeypatch, change):
    record, tool, _unit = journal
    module = copy_module(record)
    unit = "hz-daemon.service"
    executable = str(tool.prefix / "bin/hz")
    environment = {"PATH": "/fixture/bin:/usr/bin:/bin", "HOME": "/fixture/home", "HZ_STATE_DIR": "/fixture/state"}
    expected = {
        "fragment": "/fixture/hz-daemon.service",
        "program": executable,
        "arguments": f"{executable} system daemon tick",
        "environment": environment,
        "directory": "/fixture/home",
        "disabled": True,
    }
    values = {
        "Id": unit,
        "LoadState": "loaded",
        "FragmentPath": expected["fragment"],
        "DropInPaths": "",
        "ExecStart": f"{{ path={executable} ; argv[]={executable} system daemon tick ; ignore_errors=no ; }}",
        "Environment": shlex.join([f"{key}={value}" for key, value in environment.items()]),
        "WorkingDirectory": expected["directory"],
        "ActiveState": "inactive",
        "UnitFileState": "disabled",
        "MainPID": "0",
        "ControlPID": "0",
        "Job": "",
    }
    authority = {"manager_units": {unit: expected}}
    mutations = {
        "fragment": ("FragmentPath", "/foreign/service"),
        "argv": ("ExecStart", values["ExecStart"] + " argv[]=foreign"),
        "profile": ("Environment", "PATH=/foreign HOME=/fixture/home HZ_STATE_DIR=/fixture/state"),
        "extra-profile": ("Environment", values["Environment"] + " HZ_CONFIG=/foreign"),
        "directory": ("WorkingDirectory", "/foreign"),
        "enabled": ("UnitFileState", "enabled"),
        "pid": ("MainPID", "42"),
    }
    if change in mutations:
        key, value = mutations[change]
        values[key] = value
    if change == "unknown":
        authority["manager_units"] = {}
    output = "".join(f"{key}={value}\n" for key, value in values.items())
    if change == "duplicate":
        output += f"Id={unit}\n"
    monkeypatch.setattr(module, "_linux_identity", lambda: (1, 2, 3, 4))
    monkeypatch.setattr(
        module, "_manager", lambda command, platform: output if command[3] == unit else "LoadState=not-found\n"
    )
    if change == "none":
        module._linux_absent(authority)
    else:
        with pytest.raises(ValueError, match=r"foreign|ambiguous"):
            module._linux_absent(authority)


def test_linux_recovery_does_not_accept_a_timer_retargeted_to_a_foreign_service(journal):
    record, _tool, _unit = journal
    module = copy_module(record)
    expected = {"fragment": "/fixture/hz-daemon.timer", "triggers": "hz-daemon.service", "disabled": True}
    values = {
        "Id": "hz-daemon.timer",
        "FragmentPath": expected["fragment"],
        "DropInPaths": "",
        "Triggers": "hz-daemon.service",
        "UnitFileState": "disabled",
    }
    assert module._loaded_identity(values, expected, "hz-daemon.timer")
    assert not module._loaded_identity({**values, "Triggers": "foreign.service"}, expected, "hz-daemon.timer")
    assert not module._loaded_identity({**values, "UnitFileState": "enabled"}, expected, "hz-daemon.timer")


def test_recovery_manager_reader_bounds_real_owned_child_output_and_reaps_it(journal, monkeypatch):
    record, _tool, _unit = journal
    module = copy_module(record)
    children = []
    original = module.subprocess.Popen

    def start(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(module.subprocess, "Popen", start)
    command = [sys.executable, "-I", "-c", "import os,time; os.write(1,b'x'*10000000); time.sleep(10)"]
    with pytest.raises(ValueError, match="output exceeds its budget"):
        module._bounded_output(command, {"PATH": "/usr/bin:/bin"})
    assert len(children) == 1
    assert children[0].poll() is not None


def test_recovery_manager_reader_deadline_kills_only_its_own_unfinished_probe(journal, monkeypatch):
    import time

    record, _tool, _unit = journal
    module = copy_module(record)
    command = [sys.executable, "-I", "-c", "import time; time.sleep(10)"]
    with pytest.raises(subprocess.TimeoutExpired):
        module._bounded_output(command, {"PATH": "/usr/bin:/bin"}, deadline=time.monotonic() + 0.05)


@pytest.mark.parametrize("phase", ["prepared", "stopped", "removing", "removed", "complete"])
@pytest.mark.parametrize("gate", ["foreign", "missing", "linked"])
def test_copied_recovery_rejects_lost_gate_authority_before_any_effect(journal, monkeypatch, phase, gate):
    record, tool, unit = journal
    record.phase(phase)
    module = copy_module(record)
    links = {path: path.readlink() for path in tool.tool_entrypoints()}
    foreign = '{"token":"another-unfinished-operation"}\n'
    if gate == "foreign":
        record.gate.write_text(foreign)
    elif gate == "missing":
        record.gate.unlink()
    else:
        target = record.gate.with_suffix(".foreign")
        target.write_text(record.gate_content)
        record.gate.unlink()
        record.gate.symlink_to(target)
    calls = []
    monkeypatch.setattr(module, "managers_absent", lambda journal: None)
    monkeypatch.setattr(module.subprocess, "run", successful_uv(tool, calls))
    with pytest.raises((ValueError, OSError)):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert calls == [], "A different or missing operation gate must never authorize uv"
    assert tool.prefix.is_dir()
    assert unit.read_text() == "owned definition"
    assert all(path.is_symlink() and path.readlink() == target for path, target in links.items())
    if gate == "foreign":
        assert record.gate.read_text() == foreign
    elif gate == "missing":
        assert not record.gate.exists()
    else:
        assert record.gate.is_symlink()


@pytest.mark.parametrize("phase", ["removed", "complete"])
def test_absent_gate_resumes_only_observed_finished_cleanup_without_destructive_replay(journal, monkeypatch, phase):
    record, tool, unit = journal
    module = copy_module(record)
    calls = []
    monkeypatch.setattr(module, "managers_absent", lambda journal: None)
    monkeypatch.setattr(module.subprocess, "run", successful_uv(tool, calls))
    module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert not record.gate.exists()
    assert not tool.prefix.exists()
    assert not unit.exists()
    record.phase(phase)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: pytest.fail("No uv after finished cleanup"))
    monkeypatch.setattr(module, "_owned_unlink", lambda *args: pytest.fail("No integration unlink without a gate"))
    monkeypatch.setattr(module, "_finish_owned_links", lambda *args: pytest.fail("No command unlink without a gate"))
    assert module.recover(record.directory, module.EXPECTED_AUTHORITY)["phase"] == "complete"
    assert len(calls) == 1


def test_missing_gate_with_sdk_gone_still_retains_unfinished_owned_cleanup(journal, monkeypatch):
    record, tool, unit = journal
    record.phase("removed")
    module = copy_module(record)
    for path in tool.tool_entrypoints():
        path.unlink()
    shutil.rmtree(tool.prefix)
    record.gate.unlink()
    monkeypatch.setattr(module, "managers_absent", lambda journal: None)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: pytest.fail("No uv without gate authority"))
    with pytest.raises(ValueError, match="unfinished cleanup"):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert unit.read_text() == "owned definition"


def test_gate_change_during_manager_verification_still_precedes_uv_and_unlink(journal, monkeypatch):
    record, tool, unit = journal
    module = copy_module(record)
    foreign = '{"token":"another-unfinished-operation"}\n'
    monkeypatch.setattr(module, "managers_absent", lambda journal: record.gate.write_text(foreign))
    calls = []
    monkeypatch.setattr(module.subprocess, "run", successful_uv(tool, calls))
    with pytest.raises(ValueError, match="gate changed"):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert calls == []
    assert tool.prefix.is_dir()
    assert unit.is_file()
    assert all(path.is_symlink() for path in tool.tool_entrypoints())
    assert record.gate.read_text() == foreign


@pytest.mark.parametrize("phase", ["prepared", "stopped", "removing"])
def test_absent_gate_never_infers_a_completed_phase_from_missing_targets(journal, monkeypatch, phase):
    record, tool, _unit = journal
    module = copy_module(record)
    monkeypatch.setattr(module, "managers_absent", lambda journal: None)
    monkeypatch.setattr(module.subprocess, "run", successful_uv(tool, []))
    module.recover(record.directory, module.EXPECTED_AUTHORITY)
    record.phase(phase)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: pytest.fail("No destructive replay"))
    with pytest.raises(ValueError, match="gate is missing"):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)


def test_completed_journal_with_matching_gate_cannot_replay_a_present_sdk(journal, monkeypatch):
    record, tool, unit = journal
    record.phase("complete")
    module = copy_module(record)
    calls = []
    monkeypatch.setattr(module, "managers_absent", lambda journal: None)
    monkeypatch.setattr(module.subprocess, "run", successful_uv(tool, calls))
    with pytest.raises(ValueError, match="unfinished cleanup"):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
    assert calls == []
    assert tool.prefix.is_dir()
    assert unit.is_file()
    assert record.gate.read_text() == record.gate_content


@pytest.mark.parametrize("change", ["runtime-identity", "interpreter-content"])
def test_copied_recovery_keeps_exact_owned_interpreter_checks(journal, tmp_path, monkeypatch, change):
    record, _tool, _unit = journal
    module = copy_module(record)
    if change == "runtime-identity":
        other = tmp_path / "another-python"
        other.write_bytes(b"different owned interpreter")
        other.chmod(0o700)
        monkeypatch.setattr(module, "sys", SimpleNamespace(executable=str(other)))
    else:
        Path(record.value["python"]).write_bytes(b"interpreter changed after capture")
    monkeypatch.setattr(
        module, "managers_absent", lambda *_args: pytest.fail("Interpreter refusal precedes manager effects")
    )
    monkeypatch.setattr(
        module.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Interpreter refusal precedes uv")
    )
    with pytest.raises(ValueError, match="original recovery interpreter changed"):
        module.recover(record.directory, module.EXPECTED_AUTHORITY)
