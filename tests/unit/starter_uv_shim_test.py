"""Starter resolver policy stays with uv, while nested acceptance retains the reviewed wrapper."""

import contextlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def starter():
    path = Path(__file__).resolve().parents[2] / "scripts/starter-test.py"
    spec = importlib.util.spec_from_file_location("starter_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("arguments", "position"),
    [
        (["run", "python", "child.py"], 1),
        (["tool", "run", "hz-plugin", "acceptance"], 2),
        (["tool", "install", "fixture.whl"], 2),
        (["pip", "install", "fixture.whl"], 2),
        (["pip", "check"], 0),
        (["--version"], 0),
    ],
)
def test_wrapper_does_not_export_resolver_policy_to_descendants(starter, tmp_path, monkeypatch, arguments, position):
    real = tmp_path / "real-uv"
    observed = tmp_path / "observed.json"
    real.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        f"open({str(observed)!r}, 'w').write(json.dumps([sys.argv[1:], os.environ.get('UV_FIND_LINKS')]))\n"
    )
    real.chmod(0o700)
    monkeypatch.setattr(starter.shutil, "which", lambda _name: str(real))
    wrapper = starter.uv_shim(tmp_path / "wrapper", str(tmp_path / "reviewed-wheels")) / "uv"
    subprocess.run(
        [str(wrapper), *arguments],
        env={**os.environ, "UV_FIND_LINKS": "must-not-propagate"},
        check=True,
        timeout=15,
    )
    actual, inherited = json.loads(observed.read_text())
    assert inherited is None
    expected = arguments.copy()
    if position:
        expected[position:position] = ["--find-links", str(tmp_path / "reviewed-wheels")]
    assert actual == expected


def test_nested_uv_run_keeps_policy_out_of_child_and_retains_explicit_wrapper(starter, tmp_path, monkeypatch):
    real = Path(sys.executable).parent / "uv"
    assert real.is_file()
    monkeypatch.setattr(starter.shutil, "which", lambda _name: str(real))
    environment = {**os.environ, "UV_FIND_LINKS": str(tmp_path)}
    starter.shim_find_links(environment, tmp_path / "wrapper")
    wrapper = environment["PLUGIN_TEST_UV"]
    probe = (
        "import os, pathlib, subprocess; "
        "assert 'UV_FIND_LINKS' not in os.environ; "
        "uv = os.environ['PLUGIN_TEST_UV']; "
        "env = dict(os.environ, PATH=str(pathlib.Path(uv).parent)+os.pathsep+os.environ['PATH']); "
        "subprocess.run([uv, '--version'], env=env, check=True, timeout=5)"
    )
    subprocess.run(
        [wrapper, "run", "--offline", "--no-project", "--python", sys.executable, "python", "-c", probe],
        env=environment,
        check=True,
        timeout=15,
    )


def test_journey_uses_private_uv_and_state_directories(starter, tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_STATE_DIR", str(tmp_path / "caller-state"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "caller-venv"))
    monkeypatch.setenv("UV_FROZEN", "1")
    monkeypatch.delenv("UV_FIND_LINKS", raising=False)
    destination = tmp_path / "starter"
    environment = starter.journey_environment(destination)
    assert environment["UV_CACHE_DIR"] == str(destination / "uv-cache")
    assert environment["UV_TOOL_DIR"] == str(destination / "uv-tool")
    assert environment["HZ_STATE_DIR"] == str(destination / "state" / "state_dir")
    assert "VIRTUAL_ENV" not in environment
    assert "UV_FROZEN" not in environment


def test_interrupted_starter_command_terminates_its_process_group(starter, tmp_path):
    parent_ready = tmp_path / "parent.pid"
    child_ready = tmp_path / "child.pid"
    child_stopped = tmp_path / "child.stopped"
    child = tmp_path / "child.py"
    child.write_text(
        "import os, pathlib, signal, time\n"
        f"stopped = pathlib.Path({str(child_stopped)!r})\n"
        "def stop(_signum, _frame): stopped.write_text('stopped'); raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        f"pathlib.Path({str(child_ready)!r}).write_text(str(os.getpid()))\n"
        "while True: time.sleep(.05)\n",
        encoding="utf-8",
    )
    command = tmp_path / "command.py"
    command.write_text(
        "import os, pathlib, subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, {str(child)!r}])\n"
        f"pathlib.Path({str(parent_ready)!r}).write_text(str(os.getpid()))\n"
        "while True: time.sleep(.05)\n",
        encoding="utf-8",
    )
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('starter_for_signal', {str(starter.__file__)!r})\n"
        "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n"
        "module.install_signal_handlers()\n"
        f"module.run_process([sys.executable, {str(command)!r}], {str(tmp_path)!r}, "
        "dict(__import__('os').environ), sys.stdout)\n",
        encoding="utf-8",
    )
    process = subprocess.Popen([sys.executable, str(runner)], start_new_session=True, stdout=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while not child_ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert parent_ready.exists()
        assert child_ready.exists()
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 143
        assert child_stopped.read_text() == "stopped"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if parent_ready.exists():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(int(parent_ready.read_text()), signal.SIGKILL)


@pytest.mark.parametrize("returncode", [0, 23])
def test_completed_starter_command_reaps_background_descendants(starter, tmp_path, returncode):
    child_ready = tmp_path / "normal-child.pid"
    child_stopped = tmp_path / "normal-child.stopped"
    child = tmp_path / "normal-child.py"
    child.write_text(
        "import os, pathlib, signal, time\n"
        f"stopped = pathlib.Path({str(child_stopped)!r})\n"
        "def stop(_signum, _frame): stopped.write_text('stopped'); raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        f"pathlib.Path({str(child_ready)!r}).write_text(str(os.getpid()))\n"
        "while True: time.sleep(.05)\n",
        encoding="utf-8",
    )
    command = tmp_path / "returning-command.py"
    command.write_text(
        "import pathlib, subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}])\n"
        f"ready = pathlib.Path({str(child_ready)!r})\n"
        "deadline = time.monotonic() + 5\n"
        "while not ready.exists() and time.monotonic() < deadline: time.sleep(.01)\n"
        f"raise SystemExit({returncode})\n",
        encoding="utf-8",
    )
    runner = tmp_path / "normal-runner.py"
    runner.write_text(
        "import importlib.util, signal, sys\n"
        f"spec = importlib.util.spec_from_file_location('starter_for_return', {str(starter.__file__)!r})\n"
        "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n"
        f"raise SystemExit(module.run_process([sys.executable, {str(command)!r}], {str(tmp_path)!r}, "
        "dict(__import__('os').environ), sys.stdout))\n",
        encoding="utf-8",
    )
    process = subprocess.Popen([sys.executable, str(runner)], start_new_session=True, stdout=subprocess.DEVNULL)
    try:
        assert process.wait(timeout=8) == returncode
        assert child_stopped.read_text() == "stopped"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if child_ready.exists():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(int(child_ready.read_text()), signal.SIGKILL)


def test_journey_owns_its_cache_without_changing_the_callers_cache(starter, tmp_path, monkeypatch):
    caller_cache = str(tmp_path / "caller-cache")
    destination = tmp_path / "journey"
    destination.mkdir()
    monkeypatch.setenv("UV_CACHE_DIR", caller_cache)
    monkeypatch.delenv("UV_FIND_LINKS", raising=False)

    class FirstCommandError(Exception):
        pass

    def inspect_first_command(*args, **kwargs):
        assert args[2]["UV_CACHE_DIR"] == str(destination / "uv-cache")
        raise FirstCommandError

    monkeypatch.setattr(starter, "run_process", inspect_first_command)
    with pytest.raises(FirstCommandError):
        starter.journey("fixture-core", None, tmp_path, destination)
    assert os.environ["UV_CACHE_DIR"] == caller_cache
