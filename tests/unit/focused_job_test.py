"""The focused-job workflow runs exactly the selection it was given, and refuses anything else."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("focused_job", REPO / "scripts" / "focused_job.py")
assert _SPEC is not None
assert _SPEC.loader is not None
focused_job = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(focused_job)

OUT = Path("focused-artifacts")


def test_an_e2e_lane_runs_the_same_make_target_as_ci():
    assert focused_job.plan("e2e", "web-2", OUT) == [["make", "test-e2e", "SUITE=web-2"]]


def test_e2e_test_ids_run_serially_with_a_report():
    commands = focused_job.plan("e2e", "tests/e2e/web_ui_test.py::test_x tests/e2e/audio_visualizer_test.py", OUT)
    assert commands[0] == ["make", "e2e-prepare"]
    assert commands[1][-2:] == ["tests/e2e/web_ui_test.py::test_x", "tests/e2e/audio_visualizer_test.py"]
    assert "--junitxml=focused-artifacts/junit.xml" in commands[1]


def test_pytest_ids_install_the_test_group_first():
    commands = focused_job.plan("pytest", "tests/unit/ci_test.py::test_every_job_has_a_timeout[ci.yml]", OUT)
    assert commands[0][:2] == ["uv", "sync"]
    assert commands[1][-1] == "tests/unit/ci_test.py::test_every_job_has_a_timeout[ci.yml]"


def test_vitest_files_run_with_or_without_the_web_prefix():
    files = sorted(p.relative_to(REPO / "web") for p in (REPO / "web" / "src").rglob("*.test.tsx"))
    assert files, "the web app has component tests"
    first = str(files[0])
    for selector in (first, f"web/{first}"):
        command = focused_job.plan("vitest", selector, OUT)[0]
        assert command[-1] == first
        assert "--maxWorkers=2" in command


@pytest.mark.parametrize(
    ("kind", "selector"),
    [
        ("e2e", ""),
        ("e2e", "--deselect=tests"),  # an option, not a test id
        ("e2e", "tests/unit/ci_test.py"),  # outside the end-to-end tests
        ("pytest", "tests/../../etc/passwd"),
        ("pytest", "tests/unit/ci_test.py;rm -rf ~"),
        ("pytest", "tests/unit/ci_test.py $(id)"),
        ("pytest", "tests/unit/does_not_exist_test.py"),
        ("pytest", " ".join(["tests/unit/ci_test.py"] * 11)),
        ("vitest", "src/nope.test.tsx"),
        ("shell", "tests/unit/ci_test.py"),
    ],
)
def test_refuses_what_is_not_a_test_selection(kind, selector):
    with pytest.raises(focused_job.SelectorError):
        focused_job.plan(kind, selector, OUT)


def test_check_validates_without_running(monkeypatch, capsys):
    monkeypatch.setenv("FOCUSED_KIND", "e2e")
    monkeypatch.setenv("FOCUSED_SELECTOR", "web-2")
    monkeypatch.setattr(focused_job.subprocess, "run", pytest.fail)
    assert focused_job.main(["focused_job.py", "--check"]) == 0
    assert "plan: make test-e2e SUITE=web-2" in capsys.readouterr().out


def test_an_invalid_selection_fails_before_anything_runs(monkeypatch, capsys):
    monkeypatch.setenv("FOCUSED_KIND", "pytest")
    monkeypatch.setenv("FOCUSED_SELECTOR", "-p evil")
    monkeypatch.setattr(focused_job.subprocess, "run", pytest.fail)
    assert focused_job.main(["focused_job.py"]) == 2
    assert "::error::" in capsys.readouterr().out


def test_repeat_runs_ids_in_one_pytest_process_with_the_round_plugin():
    commands = focused_job.plan("pytest", "tests/unit/ci_test.py::test_every_job_has_a_timeout", OUT, repeat=5)
    assert commands[-1][commands[-1].index("-p", commands[-1].index("no:cacheprovider")) + 1] == "focused_job"
    assert focused_job.plan("e2e", "web-2", OUT) == [["make", "test-e2e", "SUITE=web-2"]]


@pytest.mark.parametrize("value", ["0", "61", "x", "-1", "5;id"])
def test_refuses_a_repeat_outside_1_to_60(value):
    with pytest.raises(focused_job.SelectorError):
        focused_job.repeat_count("pytest", value)


@pytest.mark.parametrize("selector", ["web-2", "tests/unit/ci_test.py::test_every_job_has_a_timeout[ci.yml]"])
def test_repeat_refuses_lanes_and_parametrized_cases(selector):
    with pytest.raises(focused_job.SelectorError):
        focused_job.plan("e2e" if selector == "web-2" else "pytest", selector, OUT, repeat=3)


def test_the_round_plugin_repeats_in_one_process_and_tallies(tmp_path):
    (tmp_path / "flaky_test.py").write_text(
        "import pytest\n"
        "RUNS = []\n"
        "@pytest.fixture\n"
        "def resource():\n"
        "    yield 1\n"
        "def test_second_run_fails(resource):\n"
        "    RUNS.append(1)\n"
        "    assert len(RUNS) != 2\n"
        "def test_other():\n"
        "    pass\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "FOCUSED_REPEAT": "3",
        "FOCUSED_OUT": str(tmp_path / "out"),
        "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO / "scripts"), os.environ.get("PYTHONPATH")])),
    }
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "focused_job", "-p", "no:xdist"]
    result = subprocess.run(
        [*command, "flaky_test.py", "-o", "addopts=", "-rA"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert "FAILED flaky_test.py::test_second_run_fails[round2]" in result.stdout, result.stdout + result.stderr
    assert json.loads((tmp_path / "out" / "tally.json").read_text()) == {"rounds": 3, "green": 2, "asked": 3}


def test_parametrized_tests_book_failures_to_their_round(tmp_path):
    (tmp_path / "param_test.py").write_text(
        "import pytest\n"
        "RUNS = []\n"
        "@pytest.mark.parametrize('case', ['a'])\n"
        "def test_case(case):\n"
        "    RUNS.append(case)\n"
        "    assert len(RUNS) != 2\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "FOCUSED_REPEAT": "2",
        "FOCUSED_OUT": str(tmp_path / "out"),
        "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO / "scripts"), os.environ.get("PYTHONPATH")])),
    }
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "focused_job", "-p", "no:xdist"]
    result = subprocess.run(
        [*command, "param_test.py", "-o", "addopts=", "-rA"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert "round2" in result.stdout, result.stdout + result.stderr
    assert json.loads((tmp_path / "out" / "tally.json").read_text()) == {"rounds": 2, "green": 1, "asked": 2}
