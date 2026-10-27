"""Readiness requires the owning authenticated web call and actual loaded listener facts."""

from types import SimpleNamespace

import pytest
from heinzel_tailscale._mapping import Mapping
from heinzel_tailscale._models import SharingIntent
from heinzel_tailscale._readiness import SharingReadiness
from heinzel_tailscale._service import intent_mapping

from heinzel import HeinzelError, LocalWebRuntime

pytestmark = pytest.mark.unit
MAPPING = Mapping.for_node("workstation.tail-example.ts.net", 9123)


class User:
    def __init__(self, authentication):
        self.authentication = authentication

    def context(self):
        if self.authentication is None:
            msg = "No authenticated context"
            raise HeinzelError(msg)
        return SimpleNamespace(authentication=self.authentication)


@pytest.mark.parametrize(
    "authentication", ["cli", "mcp-stdio", "daemon", "api-bearer", "web-session", "web-device", None]
)
def test_only_an_authenticated_web_owner_can_verify_loaded_facts(authentication):
    runtime = LocalWebRuntime(port=9123, loaded_origins=frozenset({MAPPING.origin}))
    assert SharingReadiness(runtime, User(authentication)).verifies(MAPPING) == (
        authentication in {"web-session", "web-device"}
    )


@pytest.mark.parametrize(
    "runtime",
    [
        LocalWebRuntime(),
        LocalWebRuntime(port=9123),
        LocalWebRuntime(port=8737, loaded_origins=frozenset({MAPPING.origin})),
    ],
)
def test_authentication_alone_does_not_prove_a_loaded_origin_or_overridden_port(runtime):
    assert not SharingReadiness(runtime, User("web-session")).verifies(MAPPING)


def test_apply_origins_updates_only_the_process_with_a_bound_web_listener():
    web_runtime = LocalWebRuntime(port=9123)
    SharingReadiness(web_runtime, User("web-session")).apply_origins(frozenset({MAPPING.origin}))
    assert web_runtime.loaded_origins == frozenset({MAPPING.origin})

    cli_runtime = LocalWebRuntime()
    SharingReadiness(cli_runtime, User("cli")).apply_origins(frozenset({MAPPING.origin}))
    assert cli_runtime.loaded_origins == frozenset()


@pytest.mark.parametrize(
    "origin", ["https://[broken", "https://evil.example", MAPPING.origin + "/", MAPPING.origin + "?x=1"]
)
def test_corrupted_saved_intent_is_not_accepted_as_a_network_mutation_target(origin):
    intent = SharingIntent(enabled=True, origin=origin, backend_port=9123, https_port=443, added_origin=True)
    with pytest.raises(HeinzelError):
        intent_mapping(intent)
