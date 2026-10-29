"""Pure constraints do not invoke a tool or confer authority."""

import pytest

from heinzel import HeinzelError
from heinzel.modules.heinzel._tool_argument_policy import ExactArgumentPolicy

pytestmark = pytest.mark.unit
BINDING = "a" * 64


def policy():
    return ExactArgumentPolicy(
        "calendar:events", BINDING, (("account", "alice"), ("calendar", "primary"), ("limit", 10))
    )


def test_exact_values_are_supplied_for_omissions_and_cannot_be_mutated_through_result():
    first = policy().apply("calendar:events", BINDING, ("account", "calendar", "limit"), {"calendar": "primary"})
    assert first == {"account": "alice", "calendar": "primary", "limit": 10}
    first["account"] = "other"
    assert policy().apply("calendar:events", BINDING, ("account", "calendar", "limit"), {})["account"] == "alice"


@pytest.mark.parametrize(
    "args", [{"account": "bob"}, {"limit": True}, {"limit": 10.0}, {"extra": None}, {"account": ["alice"]}]
)
def test_model_cannot_override_or_add_any_argument(args):
    with pytest.raises(HeinzelError):
        policy().apply("calendar:events", BINDING, ("account", "calendar", "limit"), args)


@pytest.mark.parametrize(
    "declared", [("account", "calendar"), ("account", "calendar", "limit", "extra"), ("account", "account", "limit")]
)
def test_every_registered_parameter_including_defaults_requires_coverage(declared):
    with pytest.raises(HeinzelError):
        policy().apply("calendar:events", BINDING, declared, {})


def test_operation_schema_binding_and_policy_are_immutable():
    first = policy()
    reordered = ExactArgumentPolicy(
        "calendar:events", BINDING, (("limit", 10), ("calendar", "primary"), ("account", "alice"))
    )
    assert first.fingerprint() == reordered.fingerprint()
    assert (
        first.fingerprint()
        != ExactArgumentPolicy(
            "calendar:events", BINDING, (("account", "bob"), ("calendar", "primary"), ("limit", 10))
        ).fingerprint()
    )
    for name, digest in [("other:events", BINDING), ("calendar:events", "b" * 64)]:
        with pytest.raises(HeinzelError):
            first.apply(name, digest, ("account", "calendar", "limit"), {})


@pytest.mark.parametrize(
    "values",
    [
        (("a", "x"), ("a", "x")),
        (("a", []),),
        (("a", 1.0),),
        (("a", "x" * 4097),),
        (("a", "\ud800"),),
        (("bad-name", "x"),),
    ],
)
def test_invalid_policy_fails_closed(values):
    with pytest.raises(HeinzelError):
        ExactArgumentPolicy("calendar:events", BINDING, values)


def test_explicit_zero_argument_policy():
    empty = ExactArgumentPolicy("status:read", BINDING, ())
    assert empty.apply("status:read", BINDING, (), {}) == {}
    with pytest.raises(HeinzelError):
        empty.apply("status:read", BINDING, ("account",), {})
