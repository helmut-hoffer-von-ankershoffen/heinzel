"""Record os.fsync calls instead of waiting on the disk, for unit tests that write a journal step by step."""

import os

import pytest


@pytest.fixture
def recorded_flushes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Return the list of descriptors passed to os.fsync; the disk is not flushed.

    A unit test cannot observe a physical flush, and on a loaded runner each one can take tens of milliseconds:
    journal tests spent their 5 s budget inside fsync. Tests using this fixture assert that the flushes happened.
    """
    flushed: list[int] = []
    monkeypatch.setattr(os, "fsync", flushed.append)
    return flushed
