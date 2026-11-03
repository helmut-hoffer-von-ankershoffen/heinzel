"""Failed private publication exposes only stable, credential-free diagnostics."""

from pathlib import Path

import pytest

from heinzel.heads.web import _supervision
from heinzel.kernel import HeinzelError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("error", "category", "number"),
    [
        (OSError(13, "private-error-body", "/private/example#token=secret"), "os", "13"),
        (ValueError("private-error-body #token=secret"), "value", "none"),
        (HeinzelError("private-error-body /private/example"), "ownership", "none"),
        (OSError("private-error-body"), "os", "none"),
        (OSError(123456, "private-error-body"), "os", "none"),
    ],
)
def test_publication_failure_keeps_shutdown_and_sanitizes_error(caplog, monkeypatch, error, category, number):
    def refused(*args, **kwargs):
        raise error

    monkeypatch.setattr(_supervision, "SupervisedWeb", refused)
    stopped, announced = [], []
    publication = _supervision.WebPublication(
        Path("/private/example"),
        "a" * 32,
        12345,
        "b" * 32,
        "0.9.14",
        "#token=secret",
        lambda: None,
        lambda: stopped.append(True),
    )
    publication.publish(announced.append, browser=False)
    assert stopped == [True]
    assert announced == []
    assert publication.control is None
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None
    assert caplog.records[0].getMessage() == f"Owned web publication failed: category={category} errno={number}"
    assert "private-error-body" not in caplog.text
    assert "/private/example" not in caplog.text
    assert "#token=" not in caplog.text
