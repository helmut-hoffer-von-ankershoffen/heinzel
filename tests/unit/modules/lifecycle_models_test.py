"""Closed lifecycle journals reject internally contradictory authority and cleanup targets."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from heinzel.modules.system.lifecycle._models import CleanupTarget, RuntimeCleanup, Transition

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(("candidate", "action"), [("a" * 64, "update"), (None, "uninstall")])
def test_absent_activation_cannot_describe_an_update_or_uninstall(candidate, action):
    with pytest.raises(ValidationError, match="transition must match"):
        Transition(id=uuid4(), before=None, candidate=candidate, phase="prepared", action=action, generation=1)


@pytest.mark.parametrize(("parent", "name"), [("deployments", "a" * 32), ("environments", "a" * 64)])
def test_cleanup_cannot_reinterpret_an_environment_as_a_deployment_or_the_reverse(parent, name):
    with pytest.raises(ValidationError, match="inventory parent"):
        CleanupTarget(parent=parent, name=name, files={"worker.py": "a" * 64}, directories=("",))


def test_cleanup_journal_cannot_authorize_the_same_owned_directory_twice():
    target = CleanupTarget(parent="deployments", name="a" * 64, files={"worker.py": "a" * 64}, directories=("",))
    with pytest.raises(ValidationError, match="more than once"):
        RuntimeCleanup(id=uuid4(), targets=(target, target))
