"""Without its services, the personal approval issuer refuses to review or issue and reports nothing as applied."""

from datetime import UTC, datetime

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_operations import _SessionApprovalIssuer
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit

AT = datetime(2026, 11, 13, tzinfo=UTC)


@pytest.fixture
def issuer(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    crud = SessionCrud(
        PersonalSessionRepository(current, clock=lambda: AT),
        lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2),
        clock=lambda: AT,
    )
    with current.local("cli"):
        session = crud.new_chat("issuer-create", "Issuer").session_id
        yield _SessionApprovalIssuer(crud, None, None), {"session_id": session, "request_id": "r-1"}


@pytest.mark.parametrize(
    ("operation", "reason"),
    [
        ("publish-draft-workflow", "Draft workflow review is unavailable"),
        ("deliver-channel-item", "Channel review is unavailable"),
        ("execute-captured-write", "Captured write review is unavailable"),
        ("queue-agent-notification", "Notification review is unavailable"),
        ("approve-agent-schedule", "Schedule review is unavailable"),
        ("approve-model-grant", "Configured model policy review is unavailable"),
        ("approve-connected-read", "Connected read review is unavailable"),
        ("approve-connected-read-set", "Connected read-set review is unavailable"),
    ],
)
def test_review_without_its_service_is_unavailable(issuer, operation, reason):
    service, arguments = issuer
    with pytest.raises(HeinzelError, match=reason):
        service.review("heinzel", operation, arguments)


@pytest.mark.parametrize(
    ("operation", "reason"),
    [
        ("publish-draft-workflow", "unavailable"),
        ("deliver-channel-item", "The reviewed channel posting changed"),
        ("execute-captured-write", "unavailable"),
        ("queue-agent-notification", "Notification review is unavailable"),
        ("approve-agent-schedule", "Schedule review is unavailable"),
        ("approve-model-grant", "unavailable"),
        ("approve-connected-read", "Connected read review is unavailable"),
        ("approve-connected-read-set", "unavailable"),
    ],
)
def test_issue_without_its_service_is_refused(issuer, operation, reason):
    service, arguments = issuer
    with pytest.raises(HeinzelError, match=reason):
        service.issue("heinzel", operation, {**arguments, "review": {}}, "test")


@pytest.mark.parametrize(
    "operation",
    [
        "execute-captured-write",
        "queue-agent-notification",
        "approve-agent-schedule",
        "approve-connected-read",
        "approve-connected-read-set",
    ],
)
def test_nothing_is_applied_without_its_service_or_review(issuer, operation):
    service, arguments = issuer
    assert service.applied("heinzel", operation, arguments) is False


def test_malformed_arguments_and_a_missing_daemon_are_refused(issuer):
    service, arguments = issuer
    with pytest.raises(HeinzelError, match="Approval is invalid or unavailable"):
        service.review("heinzel", "publish-draft-workflow", {"session_id": 1})
    with pytest.raises(HeinzelError, match="daemon attestation service is unavailable"):
        service.check_daemon("heinzel", "schedule-control", {**arguments, "action": "activate"})
    with pytest.raises(HeinzelError):
        service.issue("heinzel", "accept-agent-contract", {**arguments, "review": {}}, "test")
    with pytest.raises(HeinzelError):
        service.applied("heinzel", "accept-agent-contract", arguments)
