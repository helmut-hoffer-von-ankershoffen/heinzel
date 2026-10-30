"""Connected-read review is data; it does not grant provider access."""

import pytest
from pydantic import ValidationError

from heinzel import ConnectedReadReview, ConnectedReadService, Kernel, Module, ModuleRecord, Settings
from heinzel.kernel import Origin

pytestmark = pytest.mark.unit


def test_review_is_closed_owner_bound_data():
    fields = {
        "owner": "u-" + "a" * 32,
        "session": "hz-session",
        "operation": "provider:read",
        "schema_sha256": "b" * 64,
        "source_revision": "selected-source",
        "account_identity": "account-id",
        "provider_scopes": ("read", "previously-granted-scope"),
        "fixed_arguments": {"window": "2026-01-01"},
    }
    review = ConnectedReadReview(**fields)
    assert review.provider_scopes == ("read", "previously-granted-scope")
    with pytest.raises(ValidationError):
        ConnectedReadReview(**{**fields, "owner": "untrusted"})
    with pytest.raises(ValidationError):
        ConnectedReadReview(**{**fields, "verified": True})
    with pytest.raises(ValidationError):
        ConnectedReadReview(**{**fields, "provider_scopes": ("same", "same")})
    with pytest.raises(ValidationError):
        ConnectedReadReview(**{**fields, "fixed_arguments": {"large": "x" * 20000}})


def test_two_concrete_connected_read_services_coexist():
    class ProviderOne(ConnectedReadService):
        pass

    class ProviderTwo(ConnectedReadService):
        pass

    first = Module("first", "first")
    second = Module("second", "second")
    first.service(ProviderOne)
    second.service(ProviderTwo)
    origin = Origin(distribution="fixture", version="1", builtin=False)
    records = [
        ModuleRecord(qualname="first", module=first, origin=origin),
        ModuleRecord(qualname="second", module=second, origin=origin),
    ]
    kernel = Kernel(Settings(), records)
    assert type(kernel.resolve_selected_service(ConnectedReadService, kernel.module("first"))) is ProviderOne
    assert type(kernel.resolve_selected_service(ConnectedReadService, kernel.module("second"))) is ProviderTwo
