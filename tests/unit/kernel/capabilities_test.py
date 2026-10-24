"""Capability specs and requirement strings (ADR 0028)."""

import inspect

import pytest

from heinzel.kernel import CAPABILITIES, Requirement
from heinzel.kernel._capabilities import capability_of, parse_requirements
from heinzel.kernel._protocols import Documents, Tracker

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", sorted(CAPABILITIES))
def test_every_protocol_method_has_a_mode_and_a_docstring(name):
    spec = CAPABILITIES[name]
    methods = {n for n, _ in inspect.getmembers(spec.protocol, inspect.isfunction) if not n.startswith("_")}
    assert set(spec.methods) == methods
    assert all(inspect.getdoc(getattr(spec.protocol, method)) for method in methods)
    assert spec.summary


def test_only_documents_can_write():
    assert {name for name, spec in CAPABILITIES.items() if "write" in spec.modes} == {"documents"}


def test_finds_the_capability_of_a_protocol():
    assert capability_of(Tracker) is CAPABILITIES["tracker"]
    assert capability_of(int) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tracker:read", Requirement("tracker", frozenset({"read"}))),
        ("documents:read,write", Requirement("documents", frozenset({"read", "write"}))),
        (" documents:write ", Requirement("documents", frozenset({"write"}))),
        ("acme-docs:documents:read", Requirement("documents", frozenset({"read"}), "acme-docs")),
    ],
)
def test_parses_requirements(text, expected):
    assert Requirement.parse(text) == expected


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("tracker", "must read"),
        ("a:b:c:d", "must read"),
        ("chat:write", "unknown capability 'chat'"),
        ("tracker:", "modes must be"),
        ("tracker:delete", "modes must be"),
        ("tracker:write", "tracker has no write mode"),
        ("Acme:tracker:read", "adapter 'Acme'"),
    ],
)
def test_refuses_malformed_requirements(text, problem):
    with pytest.raises(ValueError, match=problem):
        Requirement.parse(text)


def test_requirements_print_as_written():
    assert str(Requirement.parse("documents:write,read")) == "documents:read,write"
    assert str(Requirement.parse("acme-docs:documents:read")) == "acme-docs:documents:read"


def test_repeated_capabilities_merge_their_modes():
    assert parse_requirements(["documents:read", "tracker:read", "documents:write"]) == (
        Requirement("documents", frozenset({"read", "write"})),
        Requirement("tracker", frozenset({"read"})),
    )


def test_one_capability_cannot_be_pinned_to_two_adapters():
    with pytest.raises(ValueError, match="one adapter per capability"):
        parse_requirements(["documents:read", "acme-docs:documents:write"])


def test_documents_write_method_is_marked_write():
    assert CAPABILITIES["documents"].methods == {"read": "read", "replace_section": "write"}
    assert CAPABILITIES["documents"].protocol is Documents
