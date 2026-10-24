"""Every capability the adapter declares is implemented with the protocol's methods and signatures (ADR 0028)."""

import inspect

import pytest
from heinzel_github import GithubSettings, hosts, module

from heinzel import ProviderLet
from heinzel.kernel import CAPABILITIES

pytestmark = pytest.mark.unit

PROVIDERS = [let for let in module.lets if isinstance(let, ProviderLet)]


def test_the_adapter_serves_code_host_and_tracker_read_only_on_hosts_its_settings_decide():
    assert sorted((p.name, sorted(p.modes), p.hosts, p.configured_hosts) for p in PROVIDERS) == [
        ("github:code-host", ["read"], (), hosts),
        ("github:tracker", ["read"], (), hosts),
    ]


def test_the_hosts_are_where_gh_sends_requests_for_the_configured_host():
    assert hosts(GithubSettings()) == ["api.github.com"]
    assert hosts(GithubSettings(host="ghe.example.com")) == ["ghe.example.com"]


@pytest.mark.parametrize("provider", PROVIDERS, ids=lambda p: p.name)
def test_methods_of_the_declared_modes_match_the_protocol(provider):
    capability = CAPABILITIES[provider.capability]
    served = [name for name, mode in capability.methods.items() if mode in provider.modes]
    assert served
    for name in served:
        implemented = inspect.signature(getattr(provider.factory, name), eval_str=True)
        declared = inspect.signature(getattr(capability.protocol, name), eval_str=True)
        assert implemented == declared, name
