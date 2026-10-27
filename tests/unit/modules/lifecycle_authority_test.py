"""Native authority messages cannot carry ignored options, executable paths, approval flags or ambiguous intent."""

import pytest
from pydantic import ValidationError

from heinzel.modules.system.lifecycle._authority import Request

pytestmark = pytest.mark.unit
ROOT = "/owner/profile/lifecycle"


@pytest.mark.parametrize("extra", [{"human": True}, {"python": "/bin/other"}, {"source": "https://example.test/code"}])
def test_native_authority_rejects_undeclared_privilege_and_executable_fields(extra):
    with pytest.raises(ValidationError):
        Request.model_validate({"directory": ROOT, "action": "recover", **extra})


@pytest.mark.parametrize(
    "body",
    [
        {"action": "prepare"},
        {"action": "prepare", "candidate": None},
        {"action": "prepare", "expected": None},
        {"action": "switch"},
        {"action": "complete"},
        {"action": "recover", "candidate": "a" * 64},
        {"action": "shell", "candidate": None},
        {"action": "recover", "directory": "../other"},
    ],
)
def test_native_authority_refuses_unbound_or_ignored_control_intent(body):
    with pytest.raises(ValidationError):
        Request.model_validate({"directory": ROOT, **body})


def test_uninstall_intent_requires_an_explicit_null_target():
    requested = Request.model_validate({"directory": ROOT, "action": "prepare", "candidate": None, "expected": None})
    assert requested.candidate is None
    assert requested.action == "prepare"


@pytest.mark.parametrize("value", ["true", 1, None])
def test_first_install_policy_requires_a_strict_boolean(value):
    with pytest.raises(ValidationError):
        Request.model_validate(
            {"directory": ROOT, "action": "prepare", "candidate": "a" * 64, "expected": None, "first_install": value}
        )


@pytest.mark.parametrize(
    "body",
    [
        {"action": "recover", "first_install": False},
        {"action": "prepare", "candidate": None, "expected": None, "first_install": True},
    ],
)
def test_first_install_policy_cannot_be_ignored_or_used_for_removal(body):
    with pytest.raises(ValidationError):
        Request.model_validate({"directory": ROOT, **body})
