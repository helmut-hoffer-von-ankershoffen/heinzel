"""Uninstall control input is exact, strict and cannot smuggle approval flags or data-purge requests."""

import pytest
from pydantic import ValidationError

from heinzel.modules.system.lifecycle._authority import Request

pytestmark = pytest.mark.unit

EXPECTED = {"schema": 1, "deployment": "a" * 64, "generation": 1, "previous": None}
ROOT = "/owned/profile/lifecycle"


@pytest.mark.parametrize(
    "fields",
    [
        {"expected": None},
        {"remove_payloads": True},
        {"expected": EXPECTED, "remove_payloads": "true"},
        {"expected": EXPECTED, "remove_payloads": 1},
        {"expected": EXPECTED, "remove_payloads": None},
        {"expected": EXPECTED, "purge_data": True},
        {"expected": EXPECTED, "human": True},
        {"expected": EXPECTED, "candidate": "a" * 64},
    ],
)
def test_uninstall_requires_selected_activation_and_refuses_ignored_or_ambiguous_fields(fields):
    with pytest.raises(ValidationError):
        Request.model_validate({"directory": ROOT, "action": "uninstall", **fields})


@pytest.mark.parametrize("action", ["prepare", "switch", "complete", "recover", "cancel", "cleanup"])
def test_other_actions_cannot_carry_an_uninstall_choice(action):
    with pytest.raises(ValidationError):
        Request.model_validate({"directory": ROOT, "action": action, "expected": EXPECTED, "remove_payloads": False})


@pytest.mark.parametrize("remove", [False, True])
def test_exact_uninstall_request_retains_the_explicit_payload_choice(remove):
    request = Request.model_validate(
        {"directory": ROOT, "action": "uninstall", "expected": EXPECTED, "remove_payloads": remove}
    )
    assert request.expected.model_dump(mode="json") == EXPECTED
    assert request.remove_payloads is remove
