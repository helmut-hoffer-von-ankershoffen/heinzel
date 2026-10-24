"""The runner starts the fake `acli` executable for real: argument lists, environment, deadlines, bounds. Offline."""

import json
import time
from pathlib import Path

import pytest
from heinzel_atlassian import Acli, AtlassianSettings
from heinzel_atlassian._acli import ENVIRONMENT, MAX_BYTES, MAX_ERROR_BYTES, AcliTimeoutError, executable

pytestmark = pytest.mark.integration

SEARCH = ["jira", "workitem", "search"]
SETTINGS = AtlassianSettings()


def allowed(name: str) -> bool:
    # macOS itself adds __CF_USER_TEXT_ENCODING to every process it starts.
    return (
        name in {"NO_COLOR", "__CF_USER_TEXT_ENCODING"} or name.startswith(("LC_", "ACLI_")) or name.endswith("_PROXY")
    )


def test_arguments_reach_acli_verbatim_without_a_shell(acli_on_path):
    acli_on_path.search([])
    hostile = 'project = "WID"; rm -rf ~ && echo $HOME `id` | cat'
    done = Acli(SETTINGS).run([*SEARCH, "--jql", hostile, "--json"], 10)
    assert (done.returncode, json.loads(done.stdout), done.overflow) == (0, [], False)
    assert acli_on_path.calls[-1].args == [*SEARCH, "--jql", hostile, "--json"]


def test_acli_gets_no_credentials_colours_input_or_working_directory_of_the_caller(acli_on_path, monkeypatch, tmp_path):
    monkeypatch.setenv("HZ_CREDENTIAL_ATLASSIAN_ATLASSIAN_TOKEN", "fake-token-never-passed-on")
    monkeypatch.setenv("GH_TOKEN", "fake-token-of-another-vendor")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.acme.example:3128")
    monkeypatch.setenv("ACLI_LOG_FILE", str(tmp_path / "acli.log"))
    monkeypatch.chdir(tmp_path)  # as if hz ran in a cloned repository
    acli_on_path.search([])
    Acli(SETTINGS).run([*SEARCH, "--json"], 10)
    call = acli_on_path.calls[-1]
    assert call.credentials == []
    assert {"PATH", "HOME", "NO_COLOR", "HTTPS_PROXY", "ACLI_LOG_FILE"} <= set(call.environment)
    assert "GH_TOKEN" not in call.environment
    # Python may add LC_CTYPE itself (PEP 538); nothing outside the allowlist arrives.
    assert not [name for name in call.environment if name not in ENVIRONMENT and not allowed(name)]
    assert call.no_color == "1"
    assert call.stdin == ""  # end of input at once: a prompt can't wait for an answer
    assert call.cwd == str(Path.home().resolve())


def test_a_failure_returns_its_status_and_standard_error(acli_on_path):
    acli_on_path.fail("jira workitem search", acli_on_path.UNAUTHORIZED)
    done = Acli(SETTINGS).run([*SEARCH, "--json"], 10)
    assert done.returncode == 1
    assert b"acli jira auth login" in done.stderr


def test_acli_past_its_deadline_is_stopped(acli_on_path):
    acli_on_path.answer("jira workitem search", [], sleep=10)
    started = time.monotonic()
    with pytest.raises(AcliTimeoutError):
        Acli(SETTINGS).run([*SEARCH, "--json"], 0.5)
    assert time.monotonic() - started < 8


def test_past_the_deadline_the_children_of_acli_are_stopped_too(acli_on_path):
    acli_on_path.answer("jira workitem search", [], sleep=30, linger=True)  # the child would hold the pipes 60 s
    started = time.monotonic()
    with pytest.raises(AcliTimeoutError):
        Acli(SETTINGS).run([*SEARCH, "--json"], 3)
    assert time.monotonic() - started < 8


def test_a_child_left_holding_the_pipes_does_not_hold_up_the_answer(acli_on_path):
    acli_on_path.answer("jira workitem search", [], linger=True)  # acli exits, its child would hold the pipes 60 s
    started = time.monotonic()
    done = Acli(SETTINGS).run([*SEARCH, "--json"], 10)
    assert time.monotonic() - started < 8
    assert (done.returncode, json.loads(done.stdout)) == (0, [])


def test_output_past_the_bound_stops_acli_and_is_flagged(acli_on_path):
    acli_on_path.answer("jira workitem search", "x" * 1_000_000, repeat=MAX_BYTES // 1_000_000 + 2)
    done = Acli(SETTINGS).run([*SEARCH, "--json"], 10)
    assert done.overflow
    assert done.returncode != 0  # stopped
    assert len(done.stdout) == MAX_BYTES + 1


def test_standard_error_is_cut_without_stopping_acli(acli_on_path):
    acli_on_path.answer("jira workitem search", "[]", stderr="e" * (MAX_ERROR_BYTES * 3))
    done = Acli(SETTINGS).run([*SEARCH, "--json"], 10)
    assert (done.returncode, done.stdout, done.overflow) == (0, b"[]", False)
    assert len(done.stderr) == MAX_ERROR_BYTES


def test_the_version_is_asked_once_and_parsed(acli_on_path):
    acli = Acli(SETTINGS)
    assert acli.version() == (1, 3, 39)
    assert acli.version() == (1, 3, 39)
    assert len(acli_on_path.log.read_text(encoding="utf-8").splitlines()) == 1


def test_an_absolute_path_in_the_settings_is_run(acli_on_path, no_acli):
    del no_acli  # PATH holds no acli now: only the setting finds it
    assert acli_on_path.spec is not None
    settings = AtlassianSettings(acli=str(acli_on_path.spec.parent / "acli"))
    acli_on_path.search([])
    assert Acli(settings).run([*SEARCH, "--json"], 10).returncode == 0


@pytest.mark.usefixtures("no_acli")
def test_without_acli_on_path_nothing_starts():
    with pytest.raises(FileNotFoundError):
        Acli(SETTINGS).run([*SEARCH, "--json"], 10)


def test_an_acli_found_through_a_relative_path_entry_does_not_count(acli_on_path, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "bin")  # the fake lives in tmp_path/bin: found only relative to the directory
    assert executable(SETTINGS) is None
    with pytest.raises(FileNotFoundError):
        Acli(SETTINGS).run([*SEARCH, "--json"], 10)
    assert acli_on_path.calls == []
