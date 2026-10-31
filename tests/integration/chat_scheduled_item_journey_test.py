"""Chat-created inert proposal through reviewed, source-bound fake scheduled notices."""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from typing import override

import pytest

from heinzel import ConnectedItemIdentity, HeinzelError, Notifications
from heinzel.modules.heinzel._autonomous_schedule import AutonomousSchedules
from heinzel.modules.system.daemon import DaemonService, DaemonSettings
from tests.integration.chat_test import AUTH
from tests.integration.scheduled_connected_read_test import (
    _approve_read,
    _confirm_item_schedule,
    _fixture,
    _invalidate_grant,
)

pytestmark = pytest.mark.integration


class Notices(Notifications):
    def __init__(self):
        self.calls = []

    @override
    def emit_once(self, _kind, title, body, *, route, command, key):
        self.calls.append((title, body, key))
        return True


@pytest.mark.timeout(60)  # a complete chat, approval and restart journey with several model subprocesses
def test_chat_contract_approved_source_and_item_notices_survive_restart_and_revoke(
    make_kernel, tmp_path, monkeypatch, settings
):
    import heinzel.kernel._ai as ai_module
    import heinzel.kernel._session_store as session_store
    import heinzel.modules.heinzel._autonomous_schedule as schedule_module
    import heinzel.modules.heinzel._interactive_turn as interactive_turn
    import heinzel.modules.heinzel._service as service_module
    import heinzel.modules.heinzel._session_crud as session_crud
    import heinzel.modules.heinzel._session_repository as session_repository

    now = [datetime(2026, 5, 4, 12, 2, 0, tzinfo=UTC)]

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

    client, kernel, current, session, state = _fixture(make_kernel, tmp_path, monkeypatch)
    state["item_identity"] = ConnectedItemIdentity(collection=("events",), identity_field="id", revision_field="rev")
    state["events"] = [{"id": "provider-item", "rev": "one"}]
    notices = Notices()
    kernel.bind(Notifications, notices)
    executable = tmp_path / "fake-claude"
    assert executable.exists()
    assert executable.resolve().is_relative_to(tmp_path.resolve())
    assert not executable.samefile(sys.executable)
    assert not executable.samefile(sys._base_executable)
    _write_model(executable, tmp_path / "journey-calls")
    proposal = _chat_proposal(client, session)
    assert state["reads"] == 0
    assert notices.calls == []
    _confirm_contract(client, session, proposal)
    _approve_read(client, kernel, current, session, state, rolling=True)
    schedule = _propose_schedule(client, session, proposal)
    with current.local("daemon"), pytest.raises(HeinzelError):
        asyncio.run(kernel.resolve(AutonomousSchedules).tick(session, "schedule", now[0]))
    assert state["reads"] == 0
    assert notices.calls == []
    _confirm_item_schedule(client, session, schedule)
    _assert_ticks((client, kernel, current, session, state, notices, settings, monkeypatch, now))


def _chat_proposal(client, session):
    accepted = client.post(
        "/api/heinzel/chat",
        headers=AUTH,
        json={"session_id": session, "request_id": "ask", "message": "Propose an agent for my source"},
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


def _confirm_contract(client, session, proposal):
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


def _propose_schedule(client, session, proposal):
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


def _write_model(executable, counter):
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
if n == 0:
 name='use_tool'; args={{'name':'agent:propose','arguments':{{'expected_revision':0,
        'contract':'Inspect the approved source','timeout_seconds':30}}}}
elif n == 1 or (n >= 2 and (n-2) % 3 == 2):
 name=next(t['name'] for t in payload['tools'] if 'report' in t['schema'].get('properties',{{}}))
 args={{'report':'Done'}}
elif (n-2) % 3 == 0:
 name='use_tool'; args={{'name':'connected_read','arguments':{{}}}}
else:
 value=json.loads(parts[-1]['content']); ref=value['observed_items'][0]['reference']
 name='use_tool'; args={{'name':'notification:send','arguments':{{'item_ref':ref,'title':'Title','body':'Body'}}}}
print(json.dumps({{'type':'result','subtype':'success','is_error':False,'permission_denials':[],
                  'structured_output':{{'kind':'tool','name':name,'arguments':args,'text':None}}}}))
""",
        encoding="utf-8",
    )


def _assert_ticks(context):
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
        tick()  # A fresh daemon for the same slot must not repeat either effect.
        assert state["reads"] == 1
        assert len(notices.calls) == 1
        now[0] += timedelta(minutes=5)
        tick()  # Same observed item revision in a later slot is read but not noticed twice.
        assert state["reads"] == 2
        assert len(notices.calls) == 1
    _invalidate_grant(client, session, "revoked")
    now[0] += timedelta(minutes=5)
    with current.local("daemon"):
        daemon = DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now[0], zone=UTC)
        assert not any(job.id.startswith("heinzel:agent-" + session.lower()) for job in daemon._jobs())
    assert state["reads"] == 2
    assert len(notices.calls) == 1
