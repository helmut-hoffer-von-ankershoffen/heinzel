"""A private session journal that was edited, truncated or malformed is refused with a restore hint, never trusted."""

from datetime import UTC, datetime

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit

AT = datetime(2026, 11, 14, tzinfo=UTC)


@pytest.fixture
def journal(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    repository = PersonalSessionRepository(current, clock=lambda: AT)
    service = SessionCrud(repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2), clock=lambda: AT)
    with current.local("cli"):
        session = service.new_chat("create-1", "Pip", None).session_id

        def tamper(kind: str, key: str | None, body: object = None) -> None:
            with repository.control_writer(session) as writer:
                data = (
                    {}
                    if body is None
                    else immutable_body(writer, "tampered", body, control=True).model_dump(mode="json")
                )
                writer.append(kind, key=key, data=data)

        yield service, session, tamper


def test_an_event_without_a_body_reference_is_not_a_snapshot(journal):
    service, session, tamper = journal
    tamper("skill.draft", "skill-draft-x")
    with pytest.raises(HeinzelError, match="invalid private body reference"):
        service.skill_draft(session, "x")


@pytest.mark.parametrize(
    ("kind", "key", "read", "message"),
    [
        ("artifact.associated", "artifact-association-x", lambda s, i: s.personal_artifacts(i), "artifact association"),
        ("skill.draft", "skill-draft-x", lambda s, i: s.skill_draft(i, "x"), "skill draft is invalid"),
        ("skill.sharing", "skill-sharing-x", lambda s, i: s.skill_sharing(i, "x"), "sharing proposal is invalid"),
        ("profile.proposed", "profile-proposal-x", lambda s, i: s.latest_profile_proposal(i), "profile proposal"),
        ("contract.proposed", "contract-x", lambda s, i: s.snapshot(i), "saved contract version is invalid"),
        ("schedule.paused", "schedule-x", lambda s, i: s.snapshot(i), "saved schedule is invalid"),
    ],
)
def test_a_snapshot_with_the_wrong_shape_is_refused(journal, kind, key, read, message):
    service, session, tamper = journal
    tamper(kind, key, {"unexpected": True})
    with pytest.raises(HeinzelError, match=message):
        read(service, session)


def test_a_missing_draft_or_sharing_proposal_is_reported_not_invented(journal):
    service, session, _tamper = journal
    with pytest.raises(HeinzelError, match="skill draft does not exist"):
        service.skill_draft(session, "nope")
    with pytest.raises(HeinzelError, match="sharing proposal does not exist"):
        service.skill_sharing(session, "nope")


@pytest.mark.parametrize(
    ("cursor", "limit", "message"),
    [(-1, 5, "Invalid draft cursor"), (True, 5, "Invalid draft cursor"), (0, 0, "page size"), (0, 101, "page size")],
)
def test_draft_pages_are_bounded(journal, cursor, limit, message):
    service, session, _tamper = journal
    with pytest.raises(HeinzelError, match=message):
        service.skill_drafts(session, cursor, limit)


@pytest.mark.parametrize(("cursor", "limit"), [(-1, 5), (True, 5), (0, 0), (0, 101), (0, True)])
def test_sharing_pages_are_bounded(journal, cursor, limit):
    service, session, _tamper = journal
    with pytest.raises(HeinzelError, match="Invalid sharing proposal page"):
        service.skill_sharing_proposals(session, cursor, limit)


def test_attachments_are_refused_until_an_upload_is_retained(journal):
    service, session, _tamper = journal
    request = MessageRequest(request_id="m1", text="hello", attachment_ids=["f" * 32])
    with pytest.raises(HeinzelError, match="Attachments need a retained upload"):
        service.accept(session, request)
