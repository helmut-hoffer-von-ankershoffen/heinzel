"""Chat review staging: each owner-private proposal is staged once, replays exactly, and never without an issuer."""

import pytest

from heinzel import CurrentUser, HeinzelError, Module
from heinzel.kernel._approval import ApprovalIssuer, arguments_hash
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner.chat import ChatService, ChatSettings, ChatStore
from heinzel.modules.system.runner.chat._service import contract, parse_context
from heinzel.modules.system.runner.chat._store import PROPOSALS as PROPOSALS_FILE

pytestmark = pytest.mark.unit

SESSION = "durable-1"
# method, operation, message when no issuer is bound, message when a replay no longer matches
PROPOSALS = [
    ("propose_model_policy", "approve-model-grant", "Model policy review is unavailable", "policy review no longer"),
    ("propose_agent_contract", "accept-agent-contract", "Agent contract review is unavailable", "contract review no"),
    ("propose_captured_write", "execute-captured-write", "Captured write review is unavailable", "review no longer"),
    ("propose_channel_item", "deliver-channel-item", "Channel posting review is unavailable", "no longer matches"),
    (
        "propose_agent_notification",
        "queue-agent-notification",
        "Notification review is unavailable",
        "notification review no longer",
    ),
    ("propose_agent_schedule_review", "approve-agent-schedule", "schedule review is unavailable", "schedule review no"),
]


class _Issuer(ApprovalIssuer):
    def review(self, module, operation, arguments):
        return {"module": module, "operation": operation, "request": arguments["request_id"]}


def _with_issuer() -> Module:
    module = Module("reviews", "Test-only approval issuer.")
    module.service(ApprovalIssuer, _Issuer)
    return module


@pytest.fixture
def store(settings) -> ChatStore:
    return ChatStore(settings, ChatSettings(), RunnerSettings())


@pytest.fixture
def bare(store, make_kernel) -> ChatService:
    return ChatService(store, make_kernel())


@pytest.fixture
def reviewing(settings, make_kernel):
    kernel = make_kernel(reviews=_with_issuer())
    current = kernel.resolve(CurrentUser)
    owned = ChatStore(settings, ChatSettings(), RunnerSettings(), current)
    with current.local("cli"):
        yield ChatService(owned, kernel), owned


def test_a_personal_chat_cannot_be_staged_without_an_authenticated_owner(store, make_kernel):
    service = ChatService(store, make_kernel(reviews=_with_issuer()))
    with pytest.raises(HeinzelError, match="personal chat owner is unavailable"):
        service.propose_agent_contract(SESSION, "req-1")


@pytest.mark.parametrize(("method", "_operation", "message", "_mismatch"), PROPOSALS)
def test_a_proposal_is_refused_when_no_approval_issuer_is_bound(bare, method, _operation, message, _mismatch):
    with pytest.raises(HeinzelError, match=message):
        getattr(bare, method)(SESSION, "req-1")


@pytest.mark.parametrize(("method", "operation", "_message", "_mismatch"), PROPOSALS)
def test_a_proposal_is_staged_once_and_an_exact_retry_returns_the_same_chat(
    reviewing, method, operation, _message, _mismatch
):
    service, _store = reviewing
    first = getattr(service, method)(SESSION, "req-1")
    again = getattr(service, method)(SESSION, "req-1")
    assert again.id == first.id
    staged = [action for action in again.actions if action.operation == operation]
    assert len(staged) == 1
    assert staged[0].arguments["review"]["operation"] == operation
    assert first.personal_session_id == SESSION


@pytest.mark.parametrize(("method", "operation", "_message", "mismatch"), PROPOSALS)
def test_a_replay_whose_stored_action_lost_its_review_is_refused(reviewing, method, operation, _message, mismatch):
    service, store = reviewing
    chat = getattr(service, method)(SESSION, "req-1")
    assert any(item.operation == operation for item in chat.actions)
    path = store.directory(chat.id) / PROPOSALS_FILE
    text = path.read_text()
    assert '"review"' in text
    path.write_text(text.replace('"review"', '"unreviewed"'))
    with pytest.raises(HeinzelError, match=mismatch):
        getattr(service, method)(SESSION, "req-1")


def test_a_release_draft_share_is_bound_to_the_hash_of_its_review(reviewing, bare):
    service, store = reviewing
    review = {"module": "heinzel", "operation": "share-draft-link", "request": "x"}
    request_id = arguments_hash(review)
    with pytest.raises(HeinzelError, match="Release draft share review is unavailable"):
        bare.propose_release_draft_share(SESSION, request_id)
    chat = service.propose_release_draft_share(SESSION, "other")
    assert len([a for a in chat.actions if a.operation == "share-draft-link"]) == 1
    # The stored review's hash does not equal this request id, so a replay is not the original action.
    with pytest.raises(HeinzelError, match="draft review no longer matches"):
        service.propose_release_draft_share(SESSION, "other")
    assert store.directory(chat.id).is_dir()


def test_chat_context_validation_names_what_is_wrong():
    assert parse_context(None, None) is None
    with pytest.raises(HeinzelError, match="Unknown chat context"):
        parse_context("bogus:thing", None)
    with pytest.raises(HeinzelError, match="durable check-in conversation"):
        parse_context("check-in:abc", None)
    assert parse_context("some-event", None).kind == "event"
    assert parse_context(None, "just detail").kind == "text"


def test_the_contract_of_a_check_in_chat_carries_its_instructions_and_no_email_addresses():
    context = parse_context("contract:weekly", "mail me at someone@example.org")
    text = contract(context)
    assert "someone@example.org" not in text
    assert "What the opener said about it" in text
