"""The approval issuer refuses every review, issue and recheck it cannot ground, and never guesses an exact decision."""

from dataclasses import dataclass

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.kernel import DaemonAttestation
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_operations import _SessionApprovalIssuer
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit

ARGS = {"session_id": "s", "request_id": "r"}
REVIEWED = {"session_id": "s", "request_id": "r", "review": {"x": 1}}


@dataclass
class _Status:
    installed: bool = True
    version: str | None = "1.0"
    installed_version: str | None = "1.0"
    version_mismatch: bool = False


@pytest.fixture
def service(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    repository = PersonalSessionRepository(current)
    with current.local("cli"):
        yield SessionCrud(repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))


def _issuer(service, status=None):
    return _SessionApprovalIssuer(service, DaemonAttestation(status=lambda: status) if status else None)


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ("publish-draft-workflow", "Draft workflow review is unavailable"),
        ("share-draft-link", "Draft workflow review is unavailable"),
        ("deliver-channel-item", "Channel review is unavailable"),
        ("execute-captured-write", "Captured write review is unavailable"),
        ("queue-agent-notification", "Notification review is unavailable"),
        ("approve-connected-read-set", "Connected read-set review is unavailable"),
        ("approve-connected-read", "Connected read review is unavailable"),
        ("approve-agent-schedule", "Schedule review is unavailable"),
        ("approve-model-grant", "Configured model policy review is unavailable"),
    ],
)
def test_review_without_a_kernel_is_refused_with_the_specific_reason(service, operation, message):
    with pytest.raises(HeinzelError, match=message):
        _issuer(service).review("heinzel", operation, ARGS)


@pytest.mark.parametrize(
    "arguments",
    [{}, {"session_id": "s"}, {"session_id": 1, "request_id": "r"}, {**ARGS, "other": 1}],
)
def test_malformed_approval_arguments_are_refused(service, arguments):
    with pytest.raises(HeinzelError, match="Approval is invalid or unavailable"):
        _issuer(service).review("heinzel", "execute-captured-write", arguments)


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ("approve-agent-schedule", "Schedule review is unavailable"),
        ("approve-connected-read", "Connected read review is unavailable"),
        ("queue-agent-notification", "Notification review is unavailable"),
        ("deliver-channel-item", "channel posting changed"),
        ("execute-captured-write", "Captured write review is unavailable"),
    ],
)
def test_issue_without_a_kernel_is_refused(service, operation, message):
    with pytest.raises(HeinzelError, match=message):
        _issuer(service).issue("heinzel", operation, REVIEWED, "test")


@pytest.mark.parametrize(
    "operation",
    ["approve-connected-read", "queue-agent-notification", "approve-agent-schedule", "execute-captured-write"],
)
def test_applied_without_a_kernel_is_never_assumed(service, operation):
    assert _issuer(service).applied("heinzel", operation, REVIEWED) is False


def test_a_read_set_review_that_is_not_an_exact_object_cannot_be_issued(service):
    with pytest.raises(HeinzelError, match="reviewed read set changed"):
        _issuer(service).issue("heinzel", "approve-connected-read-set", {**ARGS, "review": "nope"}, "test")


def test_a_read_set_that_was_never_reviewed_cannot_be_recognised_as_applied(service):
    assert _issuer(service).applied("heinzel", "approve-connected-read-set", ARGS) is False


def test_schedule_activation_is_blocked_without_daemon_attestation(service):
    for action in ("activate", "resume"):
        with pytest.raises(HeinzelError, match="attestation service is unavailable"):
            _issuer(service).check_daemon("heinzel", "schedule-control", {"action": action})


@pytest.mark.parametrize(
    "status",
    [
        _Status(version=None),
        _Status(installed_version=None),
        _Status(version="0.9", installed_version="1.0", version_mismatch=True),
    ],
)
def test_schedule_activation_is_blocked_while_the_daemon_runs_another_version(service, status):
    with pytest.raises(HeinzelError, match="daemon unit version is"):
        _issuer(service, status).check_daemon("heinzel", "schedule-control", {"action": "activate"})


def test_matching_or_uninstalled_daemon_and_other_actions_pass_the_daemon_check(service):
    _issuer(service, _Status()).check_daemon("heinzel", "schedule-control", {"action": "activate"})
    _issuer(service, _Status(installed=False, version=None)).check_daemon(
        "heinzel", "schedule-control", {"action": "resume"}
    )
    _issuer(service).check_daemon("heinzel", "schedule-control", {"action": "pause"})
    _issuer(service).check_daemon("other", "schedule-control", {"action": "activate"})
