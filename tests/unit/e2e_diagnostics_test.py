"""Sanitized immediate E2E diagnostics use pytest's one-based frame lines."""

from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def test_sanitized_frame_uses_pytest_line_without_disclosing_error_text(request):
    reporter = request.config.pluginmanager.get_plugin(str(Path(__file__).resolve().parents[1] / "conftest.py"))
    frame = SimpleNamespace(reprfileloc=SimpleNamespace(path="tests/e2e/docs_test.py", lineno=37))
    longrepr = SimpleNamespace(
        reprcrash=SimpleNamespace(message="ValueError: secret"), reprtraceback=SimpleNamespace(reprentries=[frame])
    )
    frames, kind = reporter._frames(SimpleNamespace(longrepr=longrepr))
    assert frames == ["tests/e2e/docs_test.py:37"]
    assert kind == "ValueError"
    assert reporter._node("tests/e2e/docs_test.py::test_example[secret]") == "tests/e2e/docs_test.py::test_example"


def test_external_frames_and_non_e2e_registration_are_excluded(monkeypatch, request):
    reporter = request.config.pluginmanager.get_plugin(str(Path(__file__).resolve().parents[1] / "conftest.py"))
    frame = SimpleNamespace(reprfileloc=SimpleNamespace(path="/private/secret.py", lineno=1))
    longrepr = SimpleNamespace(
        reprcrash=SimpleNamespace(message="custom error: private"), reprtraceback=SimpleNamespace(reprentries=[frame])
    )
    assert reporter._frames(SimpleNamespace(longrepr=longrepr)) == ([], "Failure")
    assert reporter._node("/private/secret.py::test_name[private]") == "<redacted-node>"
    registered = []
    config = SimpleNamespace(
        getoption=lambda _name: "unit",
        pluginmanager=SimpleNamespace(register=lambda *args: registered.append(args)),
    )
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    reporter.pytest_configure(config)
    assert registered == []
