"""A failed genuine handover exposes fixed facts without disclosing retained output or identities."""

import ast
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
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


def test_manager_double_supplies_only_its_fixed_native_discovery_identity():
    source = Path(__file__).parents[1] / "fixtures/legacy_recovery_manager.py"
    function = next(
        node
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "initialize"
    )
    lookups = []

    def original(service):
        service._which = lambda name: lookups.append(name) or f"/fixture/{name}"
        service._post_notices = lambda value: value

    phases = []
    namespace = {"original_init": original, "runner": object(), "probe": object(), "phase": phases.append}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)  # noqa: S102 - fixed repository fixture
    service = SimpleNamespace()
    namespace["initialize"](service)
    assert service._which("launchctl") == "/bin/launchctl"
    assert service._which("uv") == "/fixture/uv"
    assert lookups == ["uv"]
    assert service._runner is namespace["runner"]
    assert service._quiescence_probe is namespace["probe"]

    assert service._post_notices("original-notice-result") == "original-notice-result"
    assert phases == ["native-notices-enter", "native-notices-return"]


def test_handover_phase_projection_retains_only_fixed_categories(tmp_path):
    settings = SimpleNamespace(state_dir=tmp_path / "state")
    (tmp_path / "manager.json").write_text('{"pid": null}')
    (tmp_path / "tick-phases.jsonl").write_text(
        json.dumps(
            {
                "phase": "tick-watchdog",
                "stacks": ["post_native", "lock_until_finished", "secret-value"],
                "sdk": "0.9.15",
            }
        )
        + "\n"
        + json.dumps(
            {
                "phase": "secret-phase",
                "stacks": ["/opt/private", "https://private/?token=secret"],
                "sdk": {"private": "secret"},
            }
        )
        + "\n"
    )
    facts = handover_evidence(settings, tmp_path)
    assert facts["phases"] == ["tick-watchdog"]
    assert facts["sdk_versions"] == ["0.9.15"]
    assert facts["stack_categories"] == ["lock_until_finished", "post_native"]
    assert "secret" not in json.dumps(facts)
    assert "private" not in json.dumps(facts)


def test_installed_fixture_watchdog_projects_no_frame_text_or_locals(tmp_path):
    source = Path(__file__).parents[1] / "fixtures/legacy_recovery_manager.py"
    nodes = [
        node
        for node in ast.parse(source.read_text()).body
        if (isinstance(node, ast.FunctionDef) and node.name == "phase")
        or (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in {"PHASES", "STACK_PHASES"} for target in node.targets)
        )
    ]
    frame = SimpleNamespace(f_code=SimpleNamespace(co_name="post_native"), f_back=None)
    namespace = {
        "ROOT": tmp_path,
        "json": json,
        "sys": SimpleNamespace(_current_frames=lambda: {123: frame}),
        "__version__": "0.9.15",
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)  # noqa: S102 - fixed repository fixture
    namespace["phase"]("tick-watchdog", stacks=True)
    assert json.loads((tmp_path / "tick-phases.jsonl").read_text()) == {
        "phase": "tick-watchdog",
        "stacks": ["post_native"],
        "sdk": "0.9.15",
    }


@pytest.mark.parametrize(
    ("executable", "category"),
    [
        ("/bin/launchctl", "manager"),
        ("/fixed/lsregister", "registration"),
        ("/usr/bin/open", "application"),
        ("/usr/bin/osascript", "script"),
        ("/fixed/Notifier.app/Contents/MacOS/notifier", "notifier"),
    ],
)
def test_native_exec_observer_records_only_fixed_categories(tmp_path, executable, category):
    source = Path(__file__).parents[1] / "fixtures/legacy_recovery_manager.py"
    function = next(
        node
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "native_exec_observation"
    )
    namespace = {"ROOT": tmp_path, "json": json, "os": os, "Path": Path}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)  # noqa: S102 - fixed repository fixture
    observe = namespace["native_exec_observation"]
    observe("subprocess.Popen", ("/fixed/uv", ["fixture-private-value"], "/private", {"SECRET": "value"}))
    assert not (tmp_path / "native-exec-attempts.jsonl").exists()
    observe("subprocess.Popen", (executable, ["fixture-private-value"], "/private", {"SECRET": "value"}))
    assert json.loads((tmp_path / "native-exec-attempts.jsonl").read_text()) == {"category": category}


def test_actual_popen_audit_observes_without_replacing_exec_refusal(tmp_path):
    source = Path(__file__).parents[1] / "fixtures/legacy_recovery_manager.py"
    script = """
import ast, json, os, subprocess, sys
from pathlib import Path
source = Path(sys.argv[1])
function = next(node for node in ast.parse(source.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == 'native_exec_observation')
namespace = {'ROOT': Path(sys.argv[2]), 'json': json, 'os': os, 'Path': Path}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
sys.addaudithook(namespace['native_exec_observation'])
try:
    subprocess.Popen(['/nonexistent-fixture-directory/lsregister', 'fixture-private-argument'])
except FileNotFoundError:
    pass
else:
    raise AssertionError('The observer must not replace the genuine OS refusal')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(source), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / "native-exec-attempts.jsonl").read_text()) == {"category": "registration"}
    assert "fixture-private" not in (tmp_path / "native-exec-attempts.jsonl").read_text()


def test_observer_io_failure_is_reported_without_replacing_sdk_execution(tmp_path):
    source = Path(__file__).parents[1] / "fixtures/legacy_recovery_manager.py"
    function = next(
        node
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "native_exec_observation"
    )
    root = tmp_path / "not-a-directory"
    root.write_text("fixture")
    warnings = []
    observed_os = SimpleNamespace(
        PathLike=os.PathLike, fsdecode=os.fsdecode, write=lambda fd, payload: warnings.append((fd, payload))
    )
    namespace = {"ROOT": root, "json": json, "os": observed_os, "Path": Path, "contextlib": contextlib}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)  # noqa: S102 - fixed repository fixture
    assert namespace["native_exec_observation"]("subprocess.Popen", ("/fixed/lsregister",)) is None
    assert warnings == [(2, b"native-exec-observer-failed\n")]
