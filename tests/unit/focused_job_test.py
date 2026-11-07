"""The focused-job workflow runs exactly the selection it was given, and refuses anything else."""

import importlib.util
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
