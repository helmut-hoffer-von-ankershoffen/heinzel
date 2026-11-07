"""Failed private publication exposes only stable, credential-free diagnostics."""

import os
import sys
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


def _write_stderr(log_dir: Path) -> None:
    saved = os.dup(2)
    try:
        assert _supervision.redirect_supervised_stderr(log_dir) == log_dir / _supervision.SUPERVISED_LOG
        os.write(2, b"startup failed: why\n")
    finally:
        sys.stderr.flush()
        os.dup2(saved, 2)
        os.close(saved)


def test_supervised_stderr_goes_to_a_private_log_and_rotates(tmp_path):
    log_dir = tmp_path / "log"
    _write_stderr(log_dir)
    log = log_dir / _supervision.SUPERVISED_LOG
    assert log.read_text() == "startup failed: why\n"
    assert log.stat().st_mode & 0o777 == 0o600
    log.write_bytes(b"x" * (_supervision.SUPERVISED_LOG_LIMIT + 1))
    _write_stderr(log_dir)
    assert log.read_text() == "startup failed: why\n"
    assert log.with_name(f"{_supervision.SUPERVISED_LOG}.1").stat().st_size == _supervision.SUPERVISED_LOG_LIMIT + 1


def test_supervised_stderr_refuses_a_linked_log(tmp_path):
    log_dir = tmp_path / "log"
    log_dir.mkdir(mode=0o700)
    target = tmp_path / "elsewhere"
    target.write_text("")
    (log_dir / _supervision.SUPERVISED_LOG).symlink_to(target)
    assert _supervision.redirect_supervised_stderr(log_dir) is None
    assert target.read_text() == ""


def test_supervised_stderr_refuses_a_shared_log(tmp_path):
    log_dir = tmp_path / "log"
    log_dir.mkdir(mode=0o700)
    log = log_dir / _supervision.SUPERVISED_LOG
    log.write_text("")
    log.chmod(0o644)
    assert _supervision.redirect_supervised_stderr(log_dir) is None
    assert log.read_text() == ""
