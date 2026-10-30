"""Actual registered capability providers supply protocol-derived read descriptors."""

import pytest

from heinzel import Config, HeinzelError, Kernel, Module, ModuleRecord, Settings
from heinzel.kernel import Origin
from heinzel.modules.heinzel._capability_review import describe_capability_read

pytestmark = pytest.mark.unit


def test_protocol_schema_and_selected_provider_are_bound_without_execution():
    class CalendarAdapter:
        def __init__(self):
            raise AssertionError

    first, second = Module("first", "first"), Module("second", "second")
    first.provides("calendar", modes={"read"}, name="first", hosts=["first.invalid"])(CalendarAdapter)
    second.provides("calendar", modes={"read"}, name="second", hosts=["second.invalid"])(CalendarAdapter)
    origin = Origin(distribution="fixture", version="1", builtin=False)
    records = [
        ModuleRecord(qualname="first", module=first, origin=origin),
        ModuleRecord(qualname="second", module=second, origin=origin),
    ]
    one = Kernel(Settings(), records, config=Config(files=(), data={"capabilities": {"calendar": "first"}}))
    two = Kernel(Settings(), records, config=Config(files=(), data={"capabilities": {"calendar": "second"}}))
    descriptor = describe_capability_read(one, "calendar", "events")
    assert descriptor.kind == "capability-method"
    assert descriptor.owner_module == "first"
    assert descriptor.schema["additionalProperties"] is False
    assert descriptor.schema["required"] == ["window"]
    assert "Window" in descriptor.schema["$defs"]
    assert descriptor.binding_sha256 != describe_capability_read(two, "calendar", "events").binding_sha256
    with pytest.raises(HeinzelError):
        describe_capability_read(one, "calendar", "create_event")
    with pytest.raises(HeinzelError):
        describe_capability_read(one, "unknown", "events")
