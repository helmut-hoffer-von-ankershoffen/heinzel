"""The capability the adapter declares is implemented with the protocol's methods, and it needs only `mail:read`."""

import inspect

import pytest
from heinzel_zoom import module

from heinzel import CredentialLet, OperationLet, ProviderLet, RequirementLet
from heinzel.kernel import CAPABILITIES

pytestmark = pytest.mark.unit

PROVIDERS = [let for let in module.lets if isinstance(let, ProviderLet)]


def test_the_adapter_serves_meetings_read_only_and_sends_data_nowhere_itself():
    assert [(p.name, sorted(p.modes), p.hosts) for p in PROVIDERS] == [("zoom:meetings", ["read"], ())]


def test_methods_of_the_declared_modes_match_the_protocol():
    (provider,) = PROVIDERS
    capability = CAPABILITIES[provider.capability]
    served = [name for name, mode in capability.methods.items() if mode in provider.modes]
    assert sorted(served) == ["ended", "transcript", "transcripts"]
    for name in served:
        implemented = inspect.signature(getattr(provider.factory, name), eval_str=True)
        declared = inspect.signature(getattr(capability.protocol, name), eval_str=True)
        assert implemented == declared, name


def test_it_reads_mail_and_needs_no_credentials_nor_any_write():
    assert [str(let.requirement) for let in module.lets if isinstance(let, RequirementLet)] == ["mail:read"]
    assert not [let for let in module.lets if isinstance(let, CredentialLet)]
    operations = [let for let in module.lets if isinstance(let, OperationLet)]
    assert [(op.name, op.read_only) for op in operations] == [("info", True)]
