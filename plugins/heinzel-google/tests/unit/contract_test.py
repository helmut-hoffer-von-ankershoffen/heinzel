"""Every capability the adapter declares is implemented with the protocol's methods and signatures (ADR 0028)."""

import inspect

import pytest
from heinzel_google import CALENDAR_HOSTS, DOCUMENT_HOSTS, GoogleCalendar, GoogleDocuments, module

from heinzel import ProviderLet
from heinzel.kernel import CAPABILITIES
from heinzel.testing import assert_adapter

pytestmark = pytest.mark.unit

PROVIDERS = [let for let in module.lets if isinstance(let, ProviderLet)]


def test_the_adapter_serves_documents_and_calendar_read_only_on_googles_hosts():
    assert sorted((p.name, sorted(p.modes)) for p in PROVIDERS) == [
        ("google:calendar", ["read"]),
        ("google:documents", ["read"]),
    ]
    hosts = {p.capability: p.hosts for p in PROVIDERS}
    assert hosts == {"documents": tuple(DOCUMENT_HOSTS), "calendar": tuple(CALENDAR_HOSTS)}
    assert all(host.endswith(".googleapis.com") for host in (*DOCUMENT_HOSTS, *CALENDAR_HOSTS))


def test_the_adapters_pass_the_shared_check():
    assert_adapter(GoogleDocuments, "documents")
    assert_adapter(GoogleCalendar, "calendar")


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
