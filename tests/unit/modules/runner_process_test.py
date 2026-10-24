"""Signalling the agent's process group."""

import os
import signal

import pytest

from heinzel.modules.system.runner._process import _signal_group

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("error", [ProcessLookupError, PermissionError])
def test_signalling_a_group_that_is_gone_is_not_an_error(monkeypatch, error):
    def gone(pid, signum):
        raise error

    monkeypatch.setattr(os, "killpg", gone)
    _signal_group(12345, signal.SIGTERM)  # macOS: EPERM when only zombies are left in the group
