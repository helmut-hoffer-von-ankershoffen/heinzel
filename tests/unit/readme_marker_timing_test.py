"""README marker timing retains only fixed categories and bounded output."""

import os
from types import SimpleNamespace

import pytest

from tests.e2e import docs_test

pytestmark = pytest.mark.unit


def test_marker_timing_handles_split_output_and_never_records_text(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(docs_test.time, "monotonic", lambda: now[0])
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as stream:
        capture = docs_test.CapturedOutput(SimpleNamespace(stdout=stream, stderr=None))
        try:
            os.write(write_fd, b"private-token Installed 60 pack")
            capture.pump()
            assert capture.markers == {}
            now[0] = 12.0
            os.write(write_fd, b"ages in 2s\nInstalling Heinzelm")
            capture.pump()
            now[0] = 13.0
            os.write(write_fd, b"Installed 3 executables: x\nhttp://127.0.0.1:1234/")
            capture.pump()
            assert capture.markers == {
                "packages-output": 2.0,
                "app-install-output": 2.0,
                "executables-output": 3.0,
                "loopback-url-output": 3.0,
            }
            assert "private-token" not in str(capture.markers)
        finally:
            capture.close()
            os.close(write_fd)
