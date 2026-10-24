"""The team module's runner of git and gh, with real subprocesses (`team._repo.Runner`). Offline."""

import sys

import pytest

from heinzel.modules.team._repo import CommandFailed, Runner

pytestmark = pytest.mark.integration


def test_the_runner_returns_standard_output(tmp_path):
    assert Runner()([sys.executable, "-c", "print('hello')"], tmp_path) == "hello\n"


def test_the_runner_reports_a_failure_with_what_it_said():
    with pytest.raises(CommandFailed, match="broken") as failed:
        Runner()([sys.executable, "-c", "import sys; sys.exit('broken')"], None)
    assert failed.value.stderr == "broken"


def test_the_runner_stops_a_command_that_takes_too_long():
    with pytest.raises(CommandFailed, match="longer than"):
        Runner(timeout=0.1)([sys.executable, "-c", "import time; time.sleep(5)"], None)


def test_programs_get_no_credentials_never_prompt_and_act_on_no_other_repository(monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_GITHUB_TOKEN", "secret")
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    script = (
        "import os; e = os.environ; "
        "print(e.get('HZ_CREDENTIAL_GITHUB_TOKEN'), e.get('GIT_DIR'), "
        "e['GIT_TERMINAL_PROMPT'], e['GIT_LFS_SKIP_SMUDGE'])"
    )
    assert Runner()([sys.executable, "-c", script], None) == "None None 0 1\n"


def test_programs_get_only_the_allowlisted_environment(monkeypatch):
    monkeypatch.setenv("CLOUD_SECRET_KEY", "secret")
    monkeypatch.setenv("GH_HOST", "github.com")
    script = "import os; print(os.environ.get('CLOUD_SECRET_KEY'), os.environ.get('GH_HOST'))"
    assert Runner()([sys.executable, "-c", script], None) == "None github.com\n"
