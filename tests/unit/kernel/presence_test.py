"""The kernel's `Presence` base class: heads ask for it by type; by itself it notes nothing (ADR 0062)."""

import pytest

from heinzel.kernel import Presence

pytestmark = pytest.mark.unit


def test_the_base_class_notes_nothing():
    assert Presence().mark("graph") is None
