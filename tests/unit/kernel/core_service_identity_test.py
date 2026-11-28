"""Moving core implementations does not duplicate types or shared operation context."""

import pickle

import pytest

from heinzel.kernel import _logging as published_logging
from heinzel.kernel import _notifications as published_notices
from heinzel.kernel import _telemetry as published_telemetry
from heinzel.services.core import _logging as logging_service
from heinzel.services.core import _notifications as notices_service
from heinzel.spec import _telemetry as telemetry_spec

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("published", "implementation", "model_names"),
    [
        (published_logging, logging_service, ("JsonFormatter", "TextFormatter", "Step")),
        (
            published_notices,
            notices_service,
            ("NoticeKind", "Delivery", "NoticeHelper", "CapabilityState", "ResolveWhen", "Notice", "Notifications"),
        ),
        (published_telemetry, telemetry_spec, ("TelemetryEntry", "TelemetryRead", "TelemetryLog")),
    ],
)
def test_core_service_facades_preserve_exports_and_class_metadata(published, implementation, model_names):
    for name in published.__all__:
        assert getattr(published, name) is getattr(implementation, name)
    for name in model_names:
        model = getattr(implementation, name)
        assert model.__module__ == published.__name__
        expected = f"c{published.__name__}\n{name}\np0\n.".encode()
        assert pickle.dumps(model, protocol=0) == expected
        assert pickle.loads(expected) is model  # noqa: S301 - fixed class reference in test


def test_logging_correlation_has_one_scope_across_the_old_and_new_modules():
    assert logging_service.correlation_id() is None
    with published_logging.correlate("outer"):
        assert logging_service.correlation_id() == "outer"
        with logging_service.correlate("inner"):
            assert published_logging.correlation_id() == "inner"
        assert logging_service.correlation_id() == "outer"
    assert published_logging.correlation_id() is None


def test_notification_producer_has_one_scope_across_the_old_and_new_modules():
    assert notices_service.Notifications._current_producer() is None
    with published_notices.notice_producer_scope("heinzel:example"):
        assert notices_service.Notifications._current_producer() == "heinzel:example"
        with notices_service.notice_producer_scope("heinzel:other"):
            assert published_notices.Notifications._current_producer() == "heinzel:other"
        assert notices_service.Notifications._current_producer() == "heinzel:example"
    assert published_notices.Notifications._current_producer() is None
