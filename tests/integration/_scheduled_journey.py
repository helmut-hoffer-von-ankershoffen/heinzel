"""Shared steps of the FAKE-model, FAKE-provider chat-to-scheduled-notice journeys; they prove host boundaries only."""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from typing import override

import pytest

import heinzel.kernel._ai as ai_module
import heinzel.kernel._session_store as session_store
import heinzel.modules.heinzel._autonomous_schedule as schedule_module
import heinzel.modules.heinzel._interactive_turn as interactive_turn
import heinzel.modules.heinzel._service as service_module
import heinzel.modules.heinzel._session_crud as session_crud
import heinzel.modules.heinzel._session_repository as session_repository
from heinzel import HeinzelError, Notifications
from heinzel.modules.heinzel._autonomous_schedule import AutonomousSchedules
from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from tests.integration.chat_test import AUTH


class Notices(Notifications):
    def __init__(self):
        self.calls = []

    @override
    def emit_once(self, _kind, title, body, *, route, command, key):
        self.calls.append((title, body, key))
        return True


def freeze_clock(monkeypatch, start):
    """Freeze every journal clock; returns the one-item list a test advances."""
    now = [start]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0] if tz is not None else now[0].replace(tzinfo=None)

    for module in (
        ai_module,
        session_store,
        schedule_module,
        interactive_turn,
        service_module,
        session_crud,
        session_repository,
    ):
        monkeypatch.setattr(module, "datetime", Clock)
    return now


def chat_proposal(client, session, message):
    accepted = client.post(
        "/api/heinzel/chat",
        headers=AUTH,
        json={"session_id": session, "request_id": "ask", "message": message},
    )
    assert accepted.status_code == 200, accepted.text
    answer = client.post(
        "/api/heinzel/answer-chat",
        headers=AUTH,
        json={"session_id": session, "request_id": "ask"},
    )
    assert answer.status_code == 200, answer.text
    response = client.get(
        "/api/heinzel/latest-scheduled-agent-proposal",
        headers=AUTH,
        params={"session_id": session},
    )
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["status"] == "proposed"
    assert proposal["permitted_tools"] == []
    return proposal


def refuse_tick_before_confirmation(kernel, current, session, at):
    with current.local("daemon"):
        tick = kernel.resolve(AutonomousSchedules).tick(session, "schedule", at)
        with pytest.raises(HeinzelError):
            asyncio.run(tick)


def confirm_contract(client, session, proposal):
    card = client.post(
        "/api/system/runner/chat/propose-agent-contract",
        headers=AUTH,
        params={"durable_session": session, "request_id": proposal["request_id"]},
    ).json()
    action = next(item for item in card["actions"] if item["operation"] == "accept-agent-contract")
    result = client.post(
        "/api/system/runner/chat/confirm",
        headers=AUTH,
        params={"session": card["id"], "action": action["id"]},
    )
    assert result.status_code == 200, result.text


def propose_schedule(client, session, proposal):
    policy = {
        "max_per_24h": 3,
        "topic_cooldown_seconds": 60,
        "item_evidence": {"deduplicate_by": "revision"},
        "disclosure": (
            "Content may appear on this device. Item identities are provider-reported and not completeness guarantees."
        ),
    }
    response = client.post(
        "/api/heinzel/propose-agent-schedule",
        headers=AUTH,
        json={
            "session_id": session,
            "request_id": "schedule",
            "contract_request_id": proposal["request_id"],
            "cadence_seconds": 300,
            "max_requests": 4,
            "timeout_seconds": 30,
            "connected_request_id": "grant-one",
            "notification_policy": policy,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def write_model(executable, counter, *, digest=None):
    """The fake executable: propose a contract, optionally from a playbook draft, then read and notice on each wake."""
    executable.write_text(
        f"""#!{sys.executable}
import json,sys
from pathlib import Path
if sys.argv[1:] == ['auth','status','--json']:
 print(json.dumps({{'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty'}})); raise SystemExit
if sys.argv[1:] == ['--version']:
 print('2.1.312 (Claude Code)'); raise SystemExit
marker=Path({str(counter)!r}); n=len(marker.read_text()) if marker.exists() else 0
marker.write_text('x'*(n+1)); payload=json.load(sys.stdin)
parts=[p for m in payload['messages'] for p in m['parts'] if p.get('part_kind') == 'tool-return']
digest={digest!r}
report=3 if digest else 1
if not digest and n == 0:
 name='use_tool'; args={{'name':'agent:propose','arguments':{{'expected_revision':0,
        'contract':'Inspect the approved source','timeout_seconds':30}}}}
elif n == 0:
 name='use_tool'; args={{'name':'playbook:list','arguments':{{}}}}
elif n == 1 and digest:
 assert 'draftfixture:draft' in json.dumps(payload['messages'])
 name='use_tool'; args={{'name':'playbook:preview','arguments':{{'template':'draftfixture:draft'}}}}
elif n == 2 and digest:
 assert digest in json.dumps(payload['messages'])
 name='use_tool'; args={{'name':'playbook:propose','arguments':{{'template':'draftfixture:draft',
        'sha256':digest,'expected_revision':0,'contract':'Inspect the approved source','timeout_seconds':30}}}}
elif n == report or (n > report and (n-report-1) % 3 == 2):
 name=next(t['name'] for t in payload['tools'] if 'report' in t['schema'].get('properties',{{}}))
 args={{'report':'Done'}}
elif (n-report-1) % 3 == 0:
 name='use_tool'; args={{'name':'connected_read','arguments':{{}}}}
else:
 value=json.loads(parts[-1]['content']); ref=value['observed_items'][0]['reference']
 name='use_tool'; args={{'name':'notification:send','arguments':{{'item_ref':ref,'title':'Title','body':'Body'}}}}
print(json.dumps({{'type':'result','subtype':'success','is_error':False,'permission_denials':[],
                  'structured_output':{{'kind':'tool','name':name,'arguments':args,'text':None}}}}))
""",
        encoding="utf-8",
    )


def assert_ticks(context):
    client, kernel, current, session, state, notices, settings, monkeypatch, now = context
    with current.local("daemon"):

        def tick():
            daemon = DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now[0], zone=UTC)
            registered = [job for job in daemon._jobs() if job.id.startswith("heinzel:agent-" + session.lower())]
            assert len(registered) == 1
            monkeypatch.setattr(daemon, "_jobs", lambda: registered)
            monkeypatch.setattr(daemon, "_post_notices", lambda _log: None)
            return daemon.tick()

        first = tick()
        assert any(run.disposition == "rendered" for run in first.runs), first
        assert state["reads"] == 1
        assert len(notices.calls) == 1
        events = kernel.resolve(SessionCrud).repository.control_read(session).events
        assert sum(event.kind == "input-snapshot.observed" for event in events) == 1
        assert sum(event.kind == "scheduled-notification.attempted" for event in events) == 1
        assert sum(event.kind == "scheduled-notification.recorded" for event in events) == 1
        activity = [
            (event.data.get("tool"), event.data.get("status")) for event in events if event.kind == "interactive.tool"
        ]
        assert ("connected_read", "requested") in activity
        assert ("connected_read", "completed") in activity
        assert ("notification:send", "requested") in activity
        assert ("notification:send", "completed") in activity
        assert any(event.kind == "message.accepted" and event.data.get("origin") == "daemon" for event in events)
        tick()  # A fresh daemon for the same slot must not repeat either effect.
        assert state["reads"] == 1
        assert len(notices.calls) == 1
        now[0] += timedelta(minutes=5)
        tick()  # Same observed item revision in a later slot is read but not noticed twice.
        assert state["reads"] == 2
        assert len(notices.calls) == 1
        events = kernel.resolve(SessionCrud).repository.control_read(session).events
        assert sum(event.kind == "input-snapshot.observed" for event in events) == 2
        assert sum(event.kind == "scheduled-notification.attempted" for event in events) == 1
    with current.local("cli"):
        grant_id = kernel.resolve(ConnectedReadGrants).approved(session).request_id
    revoked = client.post(
        "/api/heinzel/revoke-connected-read",
        headers=AUTH,
        json={"session_id": session, "request_id": grant_id},
    )
    assert revoked.status_code == 200, revoked.text
    now[0] += timedelta(minutes=5)
    with current.local("daemon"):
        daemon = DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now[0], zone=UTC)
        assert not any(job.id.startswith("heinzel:agent-" + session.lower()) for job in daemon._jobs())
    assert state["reads"] == 2
    assert len(notices.calls) == 1
    return first_wake_projection(client, session)


def first_wake_projection(client, session):
    activity_response = client.get(
        "/api/heinzel/tool-activity", headers=AUTH, params={"session_id": session, "limit": 100}
    )
    assert activity_response.status_code == 200, activity_response.text
    items = activity_response.json()["items"]
    first_notice = next(item for item in items if item["tool"] == "notification:send" and item["status"] == "completed")
    first_turn = [item for item in items if item["turn_id"] == first_notice["turn_id"]]
    assert {(item["tool"], item["status"]) for item in first_turn} >= {
        ("connected_read", "requested"),
        ("connected_read", "completed"),
        ("notification:send", "requested"),
        ("notification:send", "completed"),
    }
    assert len({item["turn_id"] for item in items if item["tool"] == "connected_read"}) >= 2
    response = client.get("/api/heinzel/session", headers=AUTH, params={"session_id": session})
    assert response.status_code == 200, response.text
    replies = [
        message
        for message in response.json()["messages"]
        if message["role"] == "assistant" or (message["role"] != "tool" and message.get("origin") == "daemon")
    ]
    matches = [
        index
        for index, message in enumerate(replies)
        if message["role"] == "assistant" and message.get("turn_id") == first_notice["turn_id"]
    ]
    assert len(matches) == 1
    return {"first_reply_index": matches[0], "first_turn_id": first_notice["turn_id"]}
