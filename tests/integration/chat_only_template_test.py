"""Synthetic host chat exercises inert draft material without a provider."""

import hashlib
import sys

import pytest

from heinzel import CallIdentity, ChatDraftTemplate, CurrentUser, HeinzelError, Module, PlaybookLet, Principal
from heinzel.modules.heinzel._playbook_adoption import preview_playbook_draft
from heinzel.modules.heinzel._playbooks import find_template
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._user_chat import UserChatService
from tests.integration.chat_test import AUTH, activation_arguments, stage_schedule, web_client
from tests.unit.modules.user_chat_test import FixtureConfig

pytestmark = pytest.mark.integration
WORKSHOP_SENTENCE = "Help me keep a private summary of my notes current"


def assert_reopened_owner_draft(current, crud, session, proposal, legacy):
    with current.local("cli"):
        events = crud.repository.control_read(session).events
        assert sum(event.kind == "approval.issued" for event in events) == int(legacy)
        assert sum(event.kind == "schedule.activated" for event in events) == int(legacy)
        assert not any(event.kind == "agent.contract-accepted" for event in events)
        assert not any("observed" in event.kind or "notification" in event.kind for event in events)
        reopened = SessionCrud(PersonalSessionRepository(current), crud.costs)
        assert reopened.new_chat("create").session_id == session
        assert reopened.latest_scheduled_agent_proposal(session).model_dump(mode="json") == proposal
        assert [message.text for message in reopened.snapshot(session).messages if message.role == "user"] == [
            WORKSHOP_SENTENCE,
            "Use the example information and prepare a private summary",
        ]
    other = CallIdentity(Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"}))
    with current.using(other), pytest.raises(HeinzelError, match="No such personal Heinzel"):
        reopened.latest_scheduled_agent_proposal(session)


def assert_answer_replay(client, session, reply, calls):
    model_calls = calls.read_text()
    answered_again = client.post(
        "/api/heinzel/answer-chat", headers=AUTH, json={"session_id": session, "request_id": "details"}
    )
    assert answered_again.status_code == 200
    assert answered_again.json()["message"]["text"] == reply
    assert calls.read_text() == model_calls


def activate_legacy_check_in(client, session):
    proposed = client.post(
        "/api/heinzel/propose-check-in",
        headers=AUTH,
        json={"session_id": session, "request_id": "legacy-check-in", "timezone": "UTC"},
    )
    assert proposed.status_code == 200, proposed.text
    activation = activation_arguments(session, proposed.json()["schedule"], "legacy-activate")
    staged = stage_schedule(client, session, activation)
    assert staged.status_code == 200, staged.text
    review = staged.json()
    result = client.post(
        "/api/system/runner/chat/confirm",
        headers=AUTH,
        params={"session": review["id"], "action": review["actions"][0]["id"]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["actions"][0]["status"] == "done"
    return client.get("/api/heinzel/session", headers=AUTH, params={"session_id": session}).json()["schedule"]


@pytest.mark.parametrize("legacy", [False, True])
def test_chat_only_template_asks_for_context_and_only_proposes_draft(make_kernel, tmp_path, monkeypatch, legacy):
    source = "Ask what information and output the person wants before proposing an inert draft."
    digest = hashlib.sha256(source.encode()).hexdigest()
    module = Module("fixture", "Fixture")
    module.add(
        PlaybookLet(name="fixture", summary="Fixture", chat_drafts=(ChatDraftTemplate(name="draft", text=source),))
    )
    kernel = make_kernel(builtins=True, fixture=module)
    preview = preview_playbook_draft(kernel, "fixture:draft")
    assert preview.text == source
    with pytest.raises(HeinzelError, match="No template"):
        find_template(kernel, "fixture:draft")
    service = kernel.resolve(UserChatService)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    executable = tmp_path / "fake-model"
    calls = tmp_path / "calls"
    executable.write_text(f"""#!{sys.executable}
import json,sys
from pathlib import Path
if sys.argv[1:] == ['auth','status','--json']:
 print(json.dumps({{'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty'}})); raise SystemExit
if sys.argv[1:] == ['--version']:
 print('2.1.312 (Claude Code)'); raise SystemExit
marker=Path({str(calls)!r})
n=len(marker.read_text()) if marker.exists() else 0
marker.write_text('x'*(n+1))
payload=json.load(sys.stdin)
if n == 0:
 assert 'Help me keep a private summary of my notes current' in json.dumps(payload['messages'])
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':'playbook:list','arguments':{{}}}},'text':None}}
elif n in (1,3):
 if n == 1: assert 'fixture:draft' in json.dumps(payload['messages'])
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':'playbook:preview','arguments':{{'template':'fixture:draft'}}}},'text':None}}
elif n == 4:
 assert {digest!r} in json.dumps(payload['messages'])
 args={{'template':'fixture:draft','sha256':{digest!r},'expected_revision':0,'timeout_seconds':30,
        'contract':('Use the example information and prepare a private summary.'
)}}
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':'playbook:propose','arguments':args}},'text':None}}
else:
 name=next(t['name'] for t in payload['tools'] if t['name'] != 'use_tool')
 report=(('Which information and output should I use?')
         if n == 2 else 'Draft ready for review')
 wire={{'kind':'tool','name':name,'arguments':{{'report':report}},'text':None}}
print(json.dumps({{'type':'result','subtype':'success','is_error':False,
                   'permission_denials':[], 'structured_output':wire}}))
""")
    executable.chmod(0o700)
    monkeypatch.setattr(service, "_load", lambda: FixtureConfig(executable))
    client = web_client(kernel, tmp_path)
    created = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "create"})
    assert created.status_code == 200, created.text
    session = created.json()["session_id"]
    replay = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "create"})
    assert replay.status_code == 200
    assert replay.json()["session_id"] == session

    def turn(request_id, message):
        response = client.post(
            "/api/heinzel/chat",
            headers=AUTH,
            json={"session_id": session, "request_id": request_id, "message": message},
        )
        assert response.status_code == 200, response.text
        response = client.post(
            "/api/heinzel/answer-chat",
            headers=AUTH,
            json={"session_id": session, "request_id": request_id},
        )
        assert response.status_code == 200, response.text
        return response.json()["message"]["text"]

    question = turn("initial", WORKSHOP_SENTENCE)
    assert "Which information" in question
    assert "output" in question
    params = {"session_id": session}
    route = "/api/heinzel/latest-scheduled-agent-proposal"
    assert client.get(route, headers=AUTH, params=params).json() is None
    previous_schedule = activate_legacy_check_in(client, session) if legacy else None
    if legacy:
        assert previous_schedule["status"] == "active"
    reply = turn("details", "Use the example information and prepare a private summary")
    assert reply == "Draft ready for review"
    proposal = client.get(route, headers=AUTH, params=params).json()
    assert proposal["status"] == "proposed"
    assert proposal["permitted_tools"] == []
    assert "private summary" in proposal["contract"]
    assert digest in proposal["contract"]
    snapshot = client.get("/api/heinzel/session", headers=AUTH, params=params).json()
    assert snapshot["schedule"] == previous_schedule
    assert [item["text"] for item in snapshot["messages"] if item["role"] == "user"] == [
        WORKSHOP_SENTENCE,
        "Use the example information and prepare a private summary",
    ]
    assert_answer_replay(client, session, reply, calls)
    assert_reopened_owner_draft(current, crud, session, proposal, legacy)
