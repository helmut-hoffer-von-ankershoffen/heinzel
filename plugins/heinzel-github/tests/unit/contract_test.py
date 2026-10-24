"""Every capability the adapter declares is implemented with the protocol's methods and signatures (ADR 0028)."""

import inspect

import pytest
from heinzel_github import module

from heinzel import ProviderLet
from heinzel.kernel import CAPABILITIES

pytestmark = pytest.mark.unit

PROVIDERS = [let for let in module.lets if isinstance(let, ProviderLet)]


def test_the_adapter_serves_code_host_and_tracker_read_only_on_hosts_configuration_decides():
    assert sorted((p.name, sorted(p.modes), p.hosts) for p in PROVIDERS) == [
        ("github:code-host", ["read"], ()),
        ("github:tracker", ["read"], ()),
    ]


@pytest.mark.parametrize("provider", PROVIDERS, ids=lambda p: p.name)
def test_methods_of_the_declared_modes_match_the_protocol(provider):
    capability = CAPABILITIES[provider.capability]
    served = [name for name, mode in capability.methods.items() if mode in provider.modes]
    assert served
    for name in served:
        implemented = inspect.signature(getattr(provider.factory, name), eval_str=True)
        declared = inspect.signature(getattr(capability.protocol, name), eval_str=True)
        assert implemented == declared, name
