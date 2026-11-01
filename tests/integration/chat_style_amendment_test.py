"""Owner-private chat may propose revisions without granting or starting an agent."""

import json
import sys

import pytest

from heinzel import CurrentUser
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._user_chat import UserChatService
from tests.integration.chat_test import AUTH, web_client
from tests.unit.modules.user_chat_test import FixtureConfig

pytestmark = pytest.mark.integration


def test_chat_style_color_amendment_remains_an_inert_revision(make_kernel, tmp_path, monkeypatch):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    service = kernel.resolve(UserChatService)
    executable = tmp_path / "fake-model"
    calls = tmp_path / "calls"
    captured = tmp_path / "payloads"
    assert executable.parent.resolve().is_relative_to(tmp_path.resolve())
    assert not executable.exists()
    assert not executable.is_symlink()
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
with open({str(captured)!r},'a') as out: out.write(json.dumps(payload)+'\\n')
if n in (0,3,5):
 revision=0 if n == 0 else 1
 contract='Initial draft' if n == 0 else ('Use blue for the agent style' if n == 3 else 'Stale replacement')
 args={{'expected_revision':revision,'contract':contract,'timeout_seconds':30}}
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':'agent:propose','arguments':args}},'text':None}}
elif n == 2:
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':'agent:current','arguments':{{}}}},'text':None}}
else:
 name=next(t['name'] for t in payload['tools'] if t['name'] != 'use_tool')
 wire={{'kind':'tool','name':name,'arguments':{{'report':'Ready for review'}},'text':None}}
print(json.dumps({{'type':'result','subtype':'success','is_error':False,
                   'permission_denials':[], 'structured_output':wire}}))
""")
    assert executable.resolve().is_relative_to(tmp_path.resolve())
    assert not executable.samefile(sys.executable)
    assert not executable.samefile(sys._base_executable)
    executable.chmod(0o700)
    monkeypatch.setattr(service, "_load", lambda: FixtureConfig(executable))
    client = web_client(kernel, tmp_path)
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "create"}).json()["session_id"]

    def turn(request_id, message):
        accepted = client.post(
            "/api/heinzel/chat",
            headers=AUTH,
            json={"session_id": session, "request_id": request_id, "message": message},
        )
        assert accepted.status_code == 200, accepted.text
        answered = client.post(
            "/api/heinzel/answer-chat",
            headers=AUTH,
            json={"session_id": session, "request_id": request_id},
        )
        assert answered.status_code == 200, answered.text
        assert answered.json()["message"]["text"] == "Ready for review"

    route = "/api/heinzel/latest-scheduled-agent-proposal"
    params = {"session_id": session}
    turn("initial", "Draft an agent contract")
    initial = client.get(route, headers=AUTH, params=params).json()
    assert initial["revision"] == 1
    assert initial["contract"] == "Initial draft"
    turn("amend", "Change the agent style to blue")
    amended = client.get(route, headers=AUTH, params=params).json()
    assert amended["revision"] == 2
    assert amended["contract"] == "Use blue for the agent style"
    assert amended["status"] == "proposed"
    assert amended["permitted_tools"] == []
    assert amended["preview_sha256"] != initial["preview_sha256"]
    turn("stale", "Try an old revision")
    assert client.get(route, headers=AUTH, params=params).json() == amended
    assert client.get(route, params=params).status_code == 401
    assert client.get("/api/heinzel/session", headers=AUTH, params=params).json()["schedule"] is None
    activity = client.get("/api/heinzel/tool-activity", headers=AUTH, params=params).json()["items"]
    assert ("agent:current", "completed") in [(item["tool"], item["status"]) for item in activity]
    assert ("agent:propose", "denied") in [(item["tool"], item["status"]) for item in activity]
    payloads = [json.loads(line) for line in captured.read_text().splitlines()]
    assert "Initial draft" in json.dumps(payloads[3]["messages"])
    with current.local("cli"):
        events = crud.repository.control_read(session).events
        assert sum(event.kind == "agent.proposed" for event in events) == 2
        assert not any("approved" in event.kind or "activated" in event.kind for event in events)
