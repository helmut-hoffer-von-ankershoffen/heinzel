"""Every capability the adapter declares is implemented with the protocol's methods and signatures (ADR 0028)."""

import inspect

import pytest
from heinzel_atlassian import AtlassianSettings, hosts, module

from heinzel import CredentialLet, ProviderLet
from heinzel.kernel import CAPABILITIES

pytestmark = pytest.mark.unit

PROVIDERS = [let for let in module.lets if isinstance(let, ProviderLet)]


def test_the_adapter_serves_tracker_and_documents_read_only_on_the_configured_site():
    assert sorted((p.name, sorted(p.modes), p.hosts, p.configured_hosts) for p in PROVIDERS) == [
        ("atlassian:documents", ["read"], (), hosts),
        ("atlassian:tracker", ["read"], (), hosts),
    ]


def test_the_hosts_are_the_site_and_the_gateway_once_the_site_is_set():
    assert hosts(AtlassianSettings()) == []
    assert hosts(AtlassianSettings(site_url="https://acme.atlassian.net/wiki")) == [
        "https://acme.atlassian.net",
        "api.atlassian.com",
    ]


def test_no_credential_is_declared_because_acli_owns_authentication():
    assert not [let for let in module.lets if isinstance(let, CredentialLet)]


@pytest.mark.parametrize("provider", PROVIDERS, ids=lambda p: p.name)
def test_methods_of_the_declared_modes_match_the_protocol(provider):
    capability = CAPABILITIES[provider.capability]
    served = [name for name, mode in capability.methods.items() if mode in provider.modes]
    assert served
    for name in served:
        implemented = inspect.signature(getattr(provider.factory, name), eval_str=True)
        declared = inspect.signature(getattr(capability.protocol, name), eval_str=True)
        assert implemented == declared, name


@pytest.mark.parametrize("provider", PROVIDERS, ids=lambda p: p.name)
def test_no_write_method_is_implemented(provider):
    capability = CAPABILITIES[provider.capability]
    writes = [name for name, mode in capability.methods.items() if mode == "write"]
    assert not [name for name in writes if hasattr(provider.factory, name)]
