"""A private root mapping never replaces unrelated or public configuration."""

import copy

import pytest
from heinzel_tailscale._mapping import Mapping

from heinzel import HeinzelError

pytestmark = pytest.mark.unit


@pytest.fixture
def mapping():
    return Mapping.for_node("workstation.tail-example.ts.net.", 8737)


@pytest.fixture
def unrelated():
    return {
        "TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}},
        "Web": {
            "workstation.tail-example.ts.net:443": {"Handlers": {"/docs": {"Proxy": "http://127.0.0.1:3000"}}},
            "workstation.tail-example.ts.net:8443": {"Handlers": {"/": {"Text": "Another application"}}},
        },
        "Services": {"svc:example": {"TCP": {"3000": {"TCPForward": "127.0.0.1:3000"}}}},
        "FutureField": {"preserve": True},
    }


def test_enable_changes_only_the_missing_private_root(mapping, unrelated):
    original = copy.deepcopy(unrelated)
    enabled = mapping.enable(unrelated)
    assert unrelated == original
    assert enabled["Web"][mapping.authority]["Handlers"]["/"] == {"Proxy": "http://127.0.0.1:8737"}
    assert mapping.matches(enabled)
    del enabled["Web"][mapping.authority]["Handlers"]["/"]
    assert enabled == original


def test_disable_preserves_other_paths_ports_services_and_unknown_fields(mapping, unrelated):
    enabled = mapping.enable(unrelated)
    assert mapping.disable(enabled) == unrelated
    assert mapping.matches(enabled)


def test_disable_of_the_last_owned_handler_removes_its_listener_only(mapping):
    enabled = mapping.enable({})
    assert mapping.disable(enabled) == {"TCP": {}, "Web": {}}


def test_disable_preserves_a_listener_used_by_another_host(mapping):
    config = mapping.enable({"Web": {"other.tail-example.ts.net:443": {"Handlers": {"/": {"Text": "Other"}}}}})
    disabled = mapping.disable(config)
    assert disabled["TCP"] == {"443": {"HTTPS": True}}
    assert disabled["Web"] == {"other.tail-example.ts.net:443": {"Handlers": {"/": {"Text": "Other"}}}}


def test_disable_preserves_unrecognized_authority_fields_and_their_listener(mapping):
    config = mapping.enable({"Web": {mapping.authority: {"FuturePolicy": {"preserve": True}}}})
    disabled = mapping.disable(config)
    assert disabled["Web"] == {mapping.authority: {"FuturePolicy": {"preserve": True}, "Handlers": {}}}
    assert disabled["TCP"] == {"443": {"HTTPS": True}}


def test_conflicting_root_cannot_be_enabled_or_disabled(mapping, unrelated):
    unrelated["Web"][mapping.authority]["Handlers"]["/"] = {"Proxy": "http://127.0.0.1:9000"}
    original = copy.deepcopy(unrelated)
    with pytest.raises(HeinzelError, match="another target"):
        mapping.enable(unrelated)
    with pytest.raises(HeinzelError, match="target changed"):
        mapping.disable(unrelated)
    assert unrelated == original
    assert not mapping.matches(unrelated)


@pytest.mark.parametrize(
    "config",
    [
        {"AllowFunnel": {"workstation.tail-example.ts.net:443": True}},
        {"TCP": {"443": {"TCPForward": "127.0.0.1:3000"}}},
        {"TCP": {"443": {"HTTPS": True, "FutureFlag": True}}},
        {"Foreground": {"other-session": {"TCP": {"443": {"HTTPS": True}}}}},
        {"TCP": []},
        {"Web": {"workstation.tail-example.ts.net:443": {"Handlers": {"/": "bad"}}}},
    ],
)
def test_public_conflicting_foreground_or_unknown_shapes_are_not_mutated(mapping, config):
    original = copy.deepcopy(config)
    for operation in (mapping.enable, mapping.disable):
        with pytest.raises(HeinzelError):
            operation(config)
        assert config == original
    assert not mapping.matches(config)


def test_unchanged_matching_mapping_is_idempotent(mapping):
    enabled = mapping.enable({})
    assert mapping.enable(enabled) == enabled
    assert mapping.origin == "https://workstation.tail-example.ts.net"
    assert mapping.authority == "workstation.tail-example.ts.net:443"


def test_dormant_handler_without_an_https_listener_is_not_reported_as_ready(mapping):
    config = {"Web": {mapping.authority: {"Handlers": {"/": {"Proxy": mapping.target}}}}}
    assert not mapping.matches(config)
    assert mapping.matches(mapping.enable(config))


@pytest.mark.parametrize(
    "name",
    [
        "",
        "localhost",
        "other.example.test",
        "https://workstation.tail-example.ts.net",
        "workstation.tail-example.ts.net/path",
        "workstation.tail-example.ts.net\n",
        "-bad.tail.ts.net",
        "*.tail.ts.net",
    ],
)
def test_untrusted_or_non_tailnet_dns_names_are_refused(name):
    with pytest.raises(HeinzelError):
        Mapping.for_node(name, 8737)


@pytest.mark.parametrize(("local", "external"), [(0, 443), (8737, 0), (-1, 443), (8737, 65536)])
def test_invalid_ports_are_refused(local, external):
    with pytest.raises(HeinzelError):
        Mapping.for_node("workstation.tail-example.ts.net", local, external)
