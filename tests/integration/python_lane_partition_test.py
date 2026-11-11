"""The Linux durability mitigation preserves the owning cases and original runner contracts."""

import json
import os
import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
RETIREMENT = "tests/unit/modules/daemon_legacy_retirement_test.py"


def _make(tmp_path, platform, levels, *, execute=False, fail_serial=False):
    tools = tmp_path / "tools"
    tools.mkdir()
    trace = tmp_path / "steps.json"
    calls = tmp_path / "calls.jsonl"
    programs = {
        "uname": f'#!/bin/sh\ntest "$1" = -s || exit 64\necho {platform}\n',
        "steps": (
            f"#!{sys.executable}\n"
            "import json, os, subprocess, sys\n"
            "from pathlib import Path\n"
            "Path(os.environ['TRACE']).write_text(json.dumps({'timeout':os.environ['STEP_TIMEOUT'],'steps':sys.argv[1:]}))\n"
            + (
                f"runner = {str(REPO / 'scripts/steps.py')!r}\n"
                "sys.exit(subprocess.run([sys.executable, runner, *sys.argv[1:]], check=False).returncode)\n"
                if execute
                else ""
            )
        ),
        "uv": (
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "with open(os.environ['CALLS'], 'a') as output: output.write(json.dumps(sys.argv[1:])+'\\n')\n"
            f"sys.exit(1 if os.environ['FAIL_SERIAL']=='1' and {RETIREMENT!r} in sys.argv else 0)\n"
        ),
    }
    for name, body in programs.items():
        executable = tools / name
        executable.write_text(body)
        executable.chmod(0o700)
    result = subprocess.run(
        ["make", "--no-print-directory", "test-parallel", f"TEST_LEVELS={levels}", f"STEPS={tools / 'steps'}"],
        cwd=REPO,
        env=os.environ
        | {
            "PATH": f"{tools}:/usr/bin:/bin",
            "STEP_TIMEOUT": "77",
            "SERIAL_RETIREMENT_TEST": "foreign-selection",
            "TRACE": str(trace),
            "CALLS": str(calls),
            "FAIL_SERIAL": "1" if fail_serial else "0",
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    plan = json.loads(trace.read_text())
    [step] = plan["steps"]
    assert step.startswith("tests=")
    tokens = list(shlex.shlex(step.removeprefix("tests="), posix=True, punctuation_chars="&"))
    commands = [[]]
    for token in tokens:
        if token == "&&":
            commands.append([])
        else:
            commands[-1].append(token)
    return result, plan["timeout"], commands, calls


@pytest.mark.parametrize("platform", ["Linux", "Darwin"])
@pytest.mark.parametrize("levels", ["unit", "integration", "unit or integration"])
def test_owning_commands_serialize_only_linux_unit_retirement(tmp_path, platform, levels):
    result, timeout, commands, _ = _make(tmp_path, platform, levels)
    assert result.returncode == 0, result.stderr
    split = platform == "Linux" and levels != "integration"
    assert timeout == ("420" if platform == "Linux" else "77")
    serial = [
        "uv",
        "run",
        "pytest",
        RETIREMENT,
        "-m",
        f"({levels}) and not perf",
        "-n",
        "1",
        "--max-worker-restart=0",
        "-x",
        "-q",
        "--session-timeout=420",
        "--durations=20",
    ]
    parallel = ["uv", "run", "pytest", "-m", f"({levels}) and not perf"]
    if split:
        parallel.append(f"--ignore={RETIREMENT}")
    parallel += ["-n", "4" if platform == "Linux" else "3", "-x", "-q", "--session-timeout=420", "--durations=20"]
    assert commands == ([serial, parallel] if split else [parallel])


@pytest.mark.parametrize("failure", [False, True])
def test_retirement_failure_stops_the_existing_step_before_parallel_tests(tmp_path, failure):
    result, timeout, commands, calls = _make(
        tmp_path, "Linux", "unit or integration", execute=True, fail_serial=failure
    )
    assert timeout == "420"
    actual = [json.loads(line) for line in calls.read_text().splitlines()]
    assert actual == [command[1:] for command in (commands[:1] if failure else commands)]
    assert (result.returncode != 0) is failure


def test_real_retirement_inventory_forms_a_complete_disjoint_unit_partition(tmp_path):
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", RETIREMENT],
        cwd=REPO,
        env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    nodes = Counter(line for line in result.stdout.splitlines() if line.startswith(f"{RETIREMENT}::"))
    assert len(nodes) == 101
    assert all(count == 1 for count in nodes.values())
    # Every other unit/integration node is retained by the unchanged marker expression and exact file exclusion.
    others = Counter({"tests/unit/other_test.py::test_other": 1, "tests/integration/other_test.py::test_other": 1})
    for levels in ("unit", "unit or integration"):
        directory = tmp_path / levels.replace(" ", "-")
        directory.mkdir()
        _, _, commands, _ = _make(directory, "Linux", levels)
        serial, parallel = commands
        assert serial.count(RETIREMENT) == 1
        assert parallel.count(f"--ignore={RETIREMENT}") == 1
        assert serial[serial.index("-m") + 1] == parallel[parallel.index("-m") + 1]
        baseline = nodes + others
        serial_nodes = Counter({node: count for node, count in baseline.items() if node.split("::")[0] == RETIREMENT})
        parallel_nodes = Counter({node: count for node, count in baseline.items() if node.split("::")[0] != RETIREMENT})
        assert not (serial_nodes & parallel_nodes)
        assert serial_nodes + parallel_nodes == baseline
