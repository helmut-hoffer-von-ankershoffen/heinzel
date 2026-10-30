"""Connected-read review is data; it does not grant provider access."""

import pytest
from pydantic import ValidationError

from heinzel import (
    Config,
    ConnectedReadReview,
    ConnectedReadService,
    HeinzelError,
    Kernel,
    Module,
    ModuleRecord,
    Settings,
)
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


def test_connected_service_selection_uses_actual_capability_binding():
    class ProviderOne(ConnectedReadService):
        pass

    class ProviderTwo(ConnectedReadService):
        pass

    class CalendarAdapter:
        pass

    first = Module("first", "first")
    second = Module("second", "second")
    first.service(ProviderOne)
    second.service(ProviderTwo)
    first.provides("calendar", modes={"read"}, name="first", hosts=["first.invalid"])(CalendarAdapter)
    second.provides("calendar", modes={"read"}, name="second", hosts=["second.invalid"])(CalendarAdapter)
    origin = Origin(distribution="fixture", version="1", builtin=False)
    records = [
        ModuleRecord(qualname="first", module=first, origin=origin),
        ModuleRecord(qualname="second", module=second, origin=origin),
    ]
    kernel = Kernel(Settings(), records, config=Config(files=(), data={"capabilities": {"calendar": "second"}}))
    record, service = kernel.selected_capability_service(ConnectedReadService, "calendar")
    assert record is kernel.module("second")
    assert type(service) is ProviderTwo
    with pytest.raises(HeinzelError):
        kernel.selected_capability_service(ConnectedReadService, "mail")


def test_capability_service_refuses_ambiguous_or_disabled_binding():
    class ProviderOne(ConnectedReadService):
        pass

    class ProviderTwo(ConnectedReadService):
        pass

    class CalendarAdapter:
        pass

    first = Module("first", "first")
    second = Module("second", "second")
    first.service(ProviderOne)
    second.service(ProviderTwo)
    first.provides("calendar", modes={"read"}, name="shared", hosts=["first.invalid"])(CalendarAdapter)
    second.provides("calendar", modes={"read"}, name="shared", hosts=["second.invalid"])(CalendarAdapter)
    origin = Origin(distribution="fixture", version="1", builtin=False)
    records = [
        ModuleRecord(qualname="first", module=first, origin=origin),
        ModuleRecord(qualname="second", module=second, origin=origin),
    ]
    for selected in ("shared", "none"):
        config = Config(files=(), data={"capabilities": {"calendar": selected}})
        kernel = Kernel(Settings(), records, config=config)
        with pytest.raises(HeinzelError):
            kernel.selected_capability_service(ConnectedReadService, "calendar")
