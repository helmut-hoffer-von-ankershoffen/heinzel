"""Read-only host activity retains its owner, provenance and redaction boundary."""

import pytest

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._turn_activity import TurnActivityProjection, interactive_turn_id
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService

pytestmark = pytest.mark.unit


@pytest.fixture
def activity(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        session = crud.new_chat("owner").session_id
        service = UserChatService(crud, current, RunnerSnapshotService())
        yield current, crud, session, service


def start(crud, session):
    turn = interactive_turn_id("request")
    with crud.repository.control_writer(session) as writer:
        writer.append("interactive.started", turn=turn, data={"message_id": "request"})
    return turn


def test_recent_host_events_are_bounded_ordered_and_redacted(activity):
    _current, crud, session, service = activity
    turn = start(crud, session)
    with crud.repository.control_writer(session) as writer:
        writer.append("interactive.proposed", turn=turn, data={"arguments": "PRIVATE_ARGUMENT"})
        for index in range(102):
            writer.append(
                "interactive.tool",
                turn=turn,
                data={"tool": "text.word_count", "status": "completed", "result": "PRIVATE_RESULT", "index": index},
            )
    default = service.activity(session)
    assert len(default.items) == 30
    assert len(service.activity(session, 100).items) == 100
    recent = service.activity(session, 2).items
    assert [item.seq for item in recent] == [default.items[-2].seq, default.items[-1].seq]
    assert recent[0].seq < recent[1].seq
    assert [(item.tool, item.status) for item in recent] == [("text.word_count", "completed")] * 2
    assert set(recent[0].model_dump()) == {"seq", "at", "turn_id", "tool", "status"}
    assert "PRIVATE_" not in default.model_dump_json()


@pytest.mark.parametrize("limit", [True, False, 0, -1, 101, 1.0, "1", None])
def test_invalid_bound_is_refused(activity, limit):
    _current, _crud, session, service = activity
    with pytest.raises(HeinzelError, match="between 1 and 100"):
        service.activity(session, limit)


@pytest.mark.parametrize("tool", ["unavailable", "hz.read", "connected_read", "connected_read_set", "profile:propose"])
def test_host_tool_names_remain_available(activity, tool):
    _current, crud, session, service = activity
    turn = start(crud, session)
    with crud.repository.control_writer(session) as writer:
        writer.append("interactive.tool", turn=turn, data={"tool": tool, "status": "denied"})
    assert service.activity(session).items[0].tool == tool


@pytest.mark.parametrize(
    ("turn", "tool", "status", "started"),
    [
        (None, "text.word_count", "completed", True),
        ("unrelated", "text.word_count", "completed", True),
        ("valid", "text.word_count", "completed", False),
        ("valid", "arbitrary", "completed", True),
        ("valid", "name with private text", "completed", True),
        ("valid", "unavailable", "completed", True),
        ("valid", "text.word_count", "invented", True),
        ("valid", 42, "completed", True),
    ],
)
def test_invalid_host_provenance_is_refused(activity, turn, tool, status, started):
    _current, crud, session, service = activity
    expected = start(crud, session) if started else interactive_turn_id("request")
    with crud.repository.control_writer(session) as writer:
        writer.append(
            "interactive.tool", turn=expected if turn == "valid" else turn, data={"tool": tool, "status": status}
        )
    with pytest.raises(HeinzelError, match="provenance"):
        service.activity(session)


def test_start_after_tool_cannot_authorize_it(activity):
    _current, crud, session, service = activity
    with crud.repository.control_writer(session) as writer:
        writer.append(
            "interactive.tool",
            turn=interactive_turn_id("request"),
            data={"tool": "text.word_count", "status": "completed"},
        )
    start(crud, session)
    with pytest.raises(HeinzelError, match="provenance"):
        service.activity(session)


def test_cached_projection_and_service_refuse_another_owner_and_unauthenticated_read(activity):
    current, crud, session, service = activity
    projector = TurnActivityProjection(crud)
    stranger = CallIdentity(Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"}))
    for identity in (stranger, None):
        with current.using(identity):
            with pytest.raises(HeinzelError):
                projector.activity(session)
            with pytest.raises(HeinzelError):
                service.activity(session)


@pytest.mark.parametrize("name", ["skill.propose_draft", "skill.propose_sharing"])
@pytest.mark.parametrize("invalid", [None, "missing-proposal", "bad-key", "bad-status", "corrupt-body"])
def test_skill_activity_requires_exact_private_proposal(activity, name, invalid):
    _current, crud, session, service = activity
    turn = start(crud, session)
    reference = "turn-draft" if name == "skill.propose_draft" else "turn-sharing"
    if invalid != "missing-proposal":
        draft = crud.propose_skill_draft(
            session, "turn-draft", "test-skill", "team", "team-1", {"SKILL.md": "PRIVATE_SKILL_BODY"}
        )
        if name == "skill.propose_sharing":
            crud.propose_skill_sharing(session, reference, "turn-draft", draft.preview_sha256)
    with crud.repository.control_writer(session) as writer:
        writer.append(
            "interactive.skill-tool",
            key="bad" if invalid == "bad-key" else "skill-" + reference,
            turn=turn,
            data={"tool": name, "status": "failed" if invalid == "bad-status" else "completed"},
        )
    if invalid == "corrupt-body":
        event = next(event for event in crud.repository.control_read(session).events if event.kind == "skill.draft")
        root = crud.repository.current.directory.parent / "heinzel-sessions" / crud.repository.principal_id
        body = root / "control" / session / "turns" / f"{event.data['body_id']}.jsonl"
        body.write_text('"changed private body"\n')
    if invalid:
        with pytest.raises(HeinzelError):
            service.activity(session)
    else:
        result = service.activity(session)
        assert [(item.tool, item.status) for item in result.items] == [(name, "completed")]
        assert "PRIVATE_SKILL_BODY" not in result.model_dump_json()
