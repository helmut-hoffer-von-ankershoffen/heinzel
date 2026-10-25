"""The kernel's `Presence` base class: heads ask for it by type; by itself it notes nothing (ADR 0081)."""

import pytest

from heinzel.kernel import Presence

pytestmark = pytest.mark.unit


def test_the_base_class_notes_nothing():
    assert Presence().mark("graph") is None


def test_only_authenticated_activity_and_never_daemon_context_is_noted(make_kernel):
    from heinzel.heads._presence import mark_authenticated_presence
    from heinzel.kernel import CurrentUser

    seen = []

    class Recorder(Presence):
        def mark(self, module):
            seen.append(module)

    kernel = make_kernel()
    kernel.bind(Presence, Recorder())
    user = kernel.resolve(CurrentUser)
    mark_authenticated_presence(kernel, "public")
    with user.local("daemon"):
        mark_authenticated_presence(kernel, "background")
    with user.local("mcp-stdio"):
        mark_authenticated_presence(kernel, "human")
    assert seen == ["human"]
