"""The focused-job workflow runs exactly the selection it was given, and refuses anything else."""

import importlib.util
import json
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


@pytest.mark.parametrize(
    ("kind", "selector"),
    [
        ("e2e", "web-2"),
        ("pytest", "tests/unit/ci_test.py::test_every_job_has_a_timeout[]"),
        ("pytest", "tests/unit/ci_test.py::TestCase[one]::test_name"),
        ("pytest", "tests/unit/ci_test.py::test_every_job_has_a_timeout[ci.yml] tests/unit/ci_test.py"),
    ],
)
def test_repeat_refuses_lanes_and_invalid_parametrized_selections(kind, selector):
    with pytest.raises(focused_job.SelectorError):
        focused_job.plan(kind, selector, OUT, repeat=3)


class _Failures:
    """Collects the node ids that failed in a nested pytest session."""

    def __init__(self) -> None:
        self.nodeids: list[str] = []

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.failed:
            self.nodeids.append(report.nodeid)


def _run_rounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, rounds: int, *, case: str = ""
) -> tuple[pytest.ExitCode, list[str]]:
    """Run a planned selection in this process and return its status and failed IDs."""
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "rounds_case_test.py").write_text(source, encoding="utf-8")
    selector = "tests/rounds_case_test.py" + case
    monkeypatch.setattr(focused_job, "ROOT", tmp_path)
    monkeypatch.setenv("FOCUSED_SELECTOR", selector)
    monkeypatch.setenv("FOCUSED_REPEAT", str(rounds))
    monkeypatch.setenv("FOCUSED_OUT", str(tmp_path / "out"))
    monkeypatch.setattr(focused_job, "_ROUNDS", {})
    monkeypatch.setattr(focused_job, "_START", focused_job.time.monotonic())
    monkeypatch.delitem(sys.modules, "rounds_case_test", raising=False)
    monkeypatch.chdir(tmp_path)
    failures = _Failures()
    # Its own --basetemp: without one the nested session cleans the machine-wide pytest-of-$USER root at start-up,
    # deleting other sessions' garbage directories inside this test's 5 s budget while those sessions may be
    # scanning them (bead l8fr: a timeout in shutil.rmtree there, and an unclosed scandir iterator).
    args = [
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:xdist",
        "-c",
        str(tmp_path / "pytest.ini"),
        f"--basetemp={tmp_path / 'basetemp'}",
        focused_job.plan("pytest", selector, tmp_path / "out", repeat=rounds)[-1][-1],
    ]
    exit_code = pytest.main(args, plugins=[focused_job, failures])
    return exit_code, failures.nodeids


def test_the_round_plugin_repeats_in_one_process_and_tallies(tmp_path, monkeypatch):
    """Failures on a later execution in the same process, plain and parametrized, land in their own round under
    pytest's real ids; a test that always passes keeps its rounds green."""
    exit_code, failed = _run_rounds(
        tmp_path,
        monkeypatch,
        "import pytest\n"
        "RUNS = []\n"
        "PRUNS = []\n"
        "@pytest.fixture\n"
        "def resource():\n"
        "    yield 1\n"
        "def test_second_run_fails(resource):\n"
        "    RUNS.append(1)\n"
        "    assert len(RUNS) != 2\n"
        "@pytest.mark.parametrize('case', ['a'])\n"
        "def test_case(case):\n"
        "    PRUNS.append(case)\n"
        "    assert len(PRUNS) != 3\n"
        "def test_other():\n"
        "    pass\n",
        rounds=3,
    )
    assert exit_code == pytest.ExitCode.TESTS_FAILED
    assert failed == [
        "tests/rounds_case_test.py::test_second_run_fails[round2]",
        "tests/rounds_case_test.py::test_case[round3-a]",
    ]
    # round 1 green, rounds 2 and 3 red; booking test_case[round3-a] to another round changes the count
    assert json.loads((tmp_path / "out" / "tally.json").read_text()) == {"rounds": 3, "green": 1, "asked": 3}


def test_repeat_exact_param_excludes_siblings_and_books_each_phase(tmp_path, monkeypatch):
    exit_code, failed = _run_rounds(
        tmp_path,
        monkeypatch,
        "import pytest\n"
        "RUNS = []\n"
        "@pytest.fixture\n"
        "def resource():\n"
        "    RUNS.append(1)\n"
        "    if len(RUNS) == 4:\n"
        "        pytest.fail('setup')\n"
        "    yield len(RUNS)\n"
        "    if len(RUNS) == 3:\n"
        "        pytest.fail('teardown')\n"
        "    if len(RUNS) == 6:\n"
        "        pytest.skip('teardown')\n"
        "@pytest.mark.parametrize('case', ['wanted', 'sibling'], ids=['round2', 'round1'])\n"
        "def test_case(case, resource):\n"
        "    assert case == 'wanted'\n"
        "    assert resource != 2\n"
        "    if resource == 5:\n"
        "        pytest.skip('skip')\n",
        rounds=6,
        case="::test_case[round2]",
    )
    assert exit_code == pytest.ExitCode.TESTS_FAILED
    assert failed == [
        "tests/rounds_case_test.py::test_case[@round2-round2]",
        "tests/rounds_case_test.py::test_case[@round3-round2]",
        "tests/rounds_case_test.py::test_case[@round4-round2]",
    ]
    assert json.loads((tmp_path / "out" / "tally.json").read_text()) == {"rounds": 6, "green": 1, "asked": 6}


def test_repeat_unknown_param_fails_collection_without_running_siblings(tmp_path, monkeypatch, capsys):
    exit_code, failed = _run_rounds(
        tmp_path,
        monkeypatch,
        "import pytest\n"
        "@pytest.mark.parametrize('case', ['a', 'ab'])\n"
        "def test_case(case):\n"
        "    pytest.fail('must not run')\n",
        rounds=3,
        case="::test_case[b]",
    )
    assert exit_code == pytest.ExitCode.USAGE_ERROR
    assert not failed
    assert "expected one case per round" in capsys.readouterr().err
    assert json.loads((tmp_path / "out" / "tally.json").read_text()) == {"rounds": 0, "green": 0, "asked": 3}


@pytest.mark.parametrize(
    ("mark", "body"),
    [("skip", "pytest.fail('must not run')"), ("xfail", "assert False"), ("xfail", "pass")],
)
def test_repeat_counts_skip_xfail_and_xpass_as_non_green(tmp_path, monkeypatch, mark, body):
    exit_code, failed = _run_rounds(
        tmp_path,
        monkeypatch,
        "import pytest\n"
        f"@pytest.mark.parametrize('case', [pytest.param('a', marks=pytest.mark.{mark}), 'b'])\n"
        "def test_case(case):\n"
        "    assert case == 'a'\n"
        f"    {body}\n",
        rounds=2,
        case="::test_case[a]",
    )
    assert exit_code == pytest.ExitCode.OK
    assert not failed
    assert json.loads((tmp_path / "out" / "tally.json").read_text()) == {"rounds": 2, "green": 0, "asked": 2}


def test_repeat_deadline_stops_before_starting_the_next_case(tmp_path, monkeypatch):
    monkeypatch.setattr(focused_job, "DEADLINE", -1)
    exit_code, failed = _run_rounds(
        tmp_path,
        monkeypatch,
        "import pytest\n"
        "@pytest.mark.parametrize('case', ['a', 'b'])\n"
        "def test_case(case):\n"
        "    pytest.fail('must not run')\n",
        rounds=2,
        case="::test_case[a]",
    )
    assert exit_code == pytest.ExitCode.TESTS_FAILED
    assert not failed
    assert json.loads((tmp_path / "out" / "tally.json").read_text()) == {"rounds": 0, "green": 0, "asked": 2}


@pytest.mark.parametrize(("attempted", "green", "status"), [(2, 0, 1), (1, 1, 1), (2, 2, 0), (None, 0, 1)])
def test_exact_repeat_job_requires_all_requested_rounds_to_pass(tmp_path, monkeypatch, attempted, green, status):
    def run(command, env, timeout):
        if "pytest" in command and attempted is not None:
            (tmp_path / "tally.json").write_text(
                json.dumps({"rounds": attempted, "green": green, "asked": 2}), encoding="utf-8"
            )
        return 0

    monkeypatch.setenv("FOCUSED_KIND", "pytest")
    monkeypatch.setenv("FOCUSED_SELECTOR", "tests/unit/ci_test.py::test_every_job_has_a_timeout[ci.yml]")
    monkeypatch.setenv("FOCUSED_REPEAT", "2")
    monkeypatch.setenv("FOCUSED_OUT", str(tmp_path))
    monkeypatch.setattr(focused_job, "_run", run)
    assert focused_job.main(["focused_job.py"]) == status
    tally = json.loads((tmp_path / "tally.json").read_text())
    assert (tally["rounds"], tally["green"]) == (attempted or 0, green)


def test_the_nested_session_never_cleans_the_shared_pytest_temp_root(tmp_path, monkeypatch):
    """Bead l8fr: another session's garbage under the machine-wide pytest temp root is not this test's to delete."""
    import getpass

    shared = tmp_path / "shared-root" / f"pytest-of-{getpass.getuser()}"
    garbage = shared / "garbage-from-another-session"
    garbage.mkdir(parents=True)
    (garbage / "kept").write_text("x", encoding="utf-8")
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(tmp_path / "shared-root"))
    case = tmp_path / "case"
    case.mkdir()
    exit_code, failed = _run_rounds(
        case, monkeypatch, "def test_uses_tmp(tmp_path):\n    assert tmp_path.is_dir()\n", rounds=1
    )
    assert exit_code == pytest.ExitCode.OK
    assert failed == []
    assert (garbage / "kept").is_file()


@pytest.mark.parametrize(
    ("nodeid", "booked"),
    [
        ("t.py::test_x[round1]", 0),
        ("t.py::test_x[round2]", 1),
        ("t.py::test_x[round2-a]", 1),  # pytest joins the round with the test's own parameters
        ("t.py::test_x[a-round3]", 2),
        ("t.py::test_x[around2]", 0),  # only a whole `roundN` parameter counts
        ("t.py::test_x", 0),
    ],
)
def test_a_failure_is_booked_to_its_round(nodeid, booked):
    assert focused_job._round_of(nodeid) == booked
