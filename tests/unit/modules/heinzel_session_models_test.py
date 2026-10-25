"""The shared wire boundary preserves identity, explicit question versions and finite budget values."""

import pytest
from pydantic import ValidationError

from heinzel.modules.heinzel._session_models import (
    AnswerRequest,
    HeinzelAppearance,
    MessageRequest,
    SessionCosts,
    SessionSnapshot,
)

pytestmark = pytest.mark.unit
IDENTITY = "hz-01JBC3EWHNX0P6PM0FQJCCGZWG"


def test_new_chat_can_render_before_approving_a_contract() -> None:
    snapshot = SessionSnapshot(
        session_id=IDENTITY,
        heinzel_id=IDENTITY,
        name="Little helper",
        costs=SessionCosts(session_max_usd=2, message_max_usd=0.2),
    )
    assert snapshot.state == "proposed"
    assert snapshot.contract_version is None
    assert snapshot.messages == []
    assert snapshot.questions == []
    assert snapshot.pending_actions == []
    assert snapshot.session_id == snapshot.heinzel_id
    assert SessionSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_provider_identity_cannot_replace_the_heinzel_identity() -> None:
    with pytest.raises(ValidationError, match="session id"):
        SessionSnapshot(
            session_id=IDENTITY,
            heinzel_id="hz-01JBC3EWHNX0P6PM0FQJCCGZWH",
            name="Little helper",
            costs=SessionCosts(session_max_usd=2, message_max_usd=0.2),
        )


@pytest.mark.parametrize("text", ["", " ", "\n"])
def test_empty_text_without_an_attachment_is_rejected(text: str) -> None:
    with pytest.raises(ValidationError, match="Say something"):
        MessageRequest(request_id="request-1", text=text)
    assert MessageRequest(request_id="request-1", text=text, attachment_ids=["upload-1"]).attachment_ids == ["upload-1"]


def test_message_retries_and_steering_are_explicit() -> None:
    request = MessageRequest(request_id="request-1", text="Use the new date", when_busy="steer")
    assert request.when_busy == "steer"
    assert request.request_id == "request-1"
    with pytest.raises(ValidationError):
        MessageRequest.model_validate({"request_id": "request-1", "text": "Hello", "human": True})


def test_answer_targets_a_question_revision_without_granting_authority() -> None:
    request = AnswerRequest(request_id="request-1", question_id="question-1", question_revision=2, answer="Report A")
    assert request.question_revision == 2
    with pytest.raises(ValidationError):
        AnswerRequest.model_validate(
            {
                "request_id": "request-1",
                "question_id": "question-1",
                "question_revision": 2,
                "answer": "Report A",
                "approve": True,
            }
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_money_never_serializes_nonfinite_or_negative_values(value: float) -> None:
    with pytest.raises(ValidationError):
        SessionCosts(spent_usd=value, session_max_usd=2, message_max_usd=0.2)


def test_appearance_is_data_and_cannot_spoof_running_state_or_inject_markup() -> None:
    look = HeinzelAppearance(hat="purple", accessories=["scarf"])
    assert look.hat == "purple"
    with pytest.raises(ValidationError):
        HeinzelAppearance.model_validate({"hat": "<script>"})
    with pytest.raises(ValidationError):
        HeinzelAppearance.model_validate({"state": "awake"})


def test_default_appearance_preserves_inherited_palette_and_age_derived_beard() -> None:
    inherited = HeinzelAppearance()
    assert inherited.hat is None
    assert inherited.shirt is None
    assert inherited.beard is None
    restored = HeinzelAppearance.model_validate_json(inherited.model_dump_json())
    assert restored == inherited
