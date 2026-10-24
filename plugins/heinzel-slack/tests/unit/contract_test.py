"""The capability the adapter declares is implemented with the protocol's methods and signatures (ADR 0028)."""

import inspect

import pytest
from heinzel_slack import HOSTS, module

from heinzel import ProviderLet
from heinzel.kernel import CAPABILITIES

pytestmark = pytest.mark.unit

PROVIDERS = [let for let in module.lets if isinstance(let, ProviderLet)]


def test_the_adapter_serves_chat_reading_and_writing_on_slack_hosts():
    assert [(p.name, sorted(p.modes), p.hosts) for p in PROVIDERS] == [("slack:chat", ["read", "write"], tuple(HOSTS))]


@pytest.mark.parametrize("provider", PROVIDERS, ids=lambda p: p.name)
def test_methods_of_the_declared_modes_match_the_protocol(provider):
    capability = CAPABILITIES[provider.capability]
    served = [name for name, mode in capability.methods.items() if mode in provider.modes]
    assert sorted(served) == ["messages", "post"]
    for name in served:
        implemented = inspect.signature(getattr(provider.factory, name), eval_str=True)
        declared = inspect.signature(getattr(capability.protocol, name), eval_str=True)
        assert implemented == declared, name
