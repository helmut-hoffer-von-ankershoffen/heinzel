"""README procedure diagnostics preserve command attribution while redacting credentials."""

import subprocess

import pytest

from tests.e2e import docs_test
from tests.e2e.docs_test import safe_diagnostics

pytestmark = pytest.mark.unit


def test_readme_failure_diagnostics_hide_session_and_registry_tokens(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "fictional-ci-secret")
    message = "uvx failed; http://127.0.0.1:1234/#token=fictional_session; token fictional-ci-secret"
    shown = safe_diagnostics(message)
    assert "fictional_session" not in shown
    assert "fictional-ci-secret" not in shown
    assert "uvx failed" in shown
    assert "<redacted>" in shown


@pytest.mark.parametrize("progress_lines", [1, 3000])
def test_readme_timeout_identifies_the_command_redacts_output_and_cleans_up_owned_children(
    monkeypatch, tmp_path, progress_lines
):
    cleaned = []
    monkeypatch.setenv("GH_TOKEN", "fictional-ci-secret")
    monkeypatch.setattr(docs_test.time, "monotonic", lambda: 0)

    class Capture:
        def text(self, name):
            if name == "stdout":
                return "progress\n" * progress_lines + "http://127.0.0.1:1234/#token=fictional_session"
            return "fictional-ci-secret"

    class SlowCommand:
        capture = Capture()

        def __init__(self, *_args, **_kwargs):
            pass

        def wait(self, budget):
            assert budget == 4
            raise subprocess.TimeoutExpired(["hz", "system", "info"], budget)

        def stop(self):
            cleaned.append(self)

    monkeypatch.setattr(docs_test, "GuardedCommand", SlowCommand)
    with pytest.raises(pytest.fail.Exception, match="README command exceeded") as failure:
        docs_test.run_documented_command(["hz", "system", "info"], env={}, cwd=tmp_path, deadline=4)
    assert "hz system info" in str(failure.value)
    assert "fictional_session" not in str(failure.value)
    assert "fictional-ci-secret" not in str(failure.value)
    assert len(cleaned) == 1
    assert isinstance(cleaned[0], SlowCommand)


@pytest.mark.parametrize("foreground", [False, True])
def test_readme_exhausted_budget_never_starts_another_command(monkeypatch, tmp_path, foreground):
    monkeypatch.setattr(docs_test.time, "monotonic", lambda: 0)
    monkeypatch.setattr(docs_test.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("Must not start"))
    if foreground:
        with pytest.raises(pytest.fail.Exception, match="exhausted"):
            docs_test.launch_documented_app("uvx heinzel@latest", {}, tmp_path, 0, bootstrap_wheels=tmp_path)
    else:
        with pytest.raises(pytest.fail.Exception, match="exhausted"):
            docs_test.run_documented_command(["hz", "system", "update"], env={}, cwd=tmp_path, deadline=0)


def test_unpublished_readme_update_selects_only_the_candidate_command(tmp_path):
    wheel = tmp_path / "candidate wheel.whl"
    block = "hz --version\nhz system update               # update, keeping plugins\nhz system agent install\n"
    selected = docs_test.candidate_update(block, wheel)
    assert selected == (
        f"hz --version\nhz system update --source '{wheel}'               # update, keeping plugins\n"
        "hz system agent install\n"
    )
    assert docs_test.candidate_update("hz system info\n", wheel) == "hz system info\n"
    assert docs_test.candidate_update("hz system update --source other\n", wheel) == "hz system update --source other\n"
    with pytest.raises(AssertionError, match="one command"):
        docs_test.candidate_update("hz system update\nhz system update\n", wheel)
