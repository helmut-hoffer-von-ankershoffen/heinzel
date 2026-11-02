"""A conversational appearance suggestion requires an owner save and remains private."""

import sys

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests.integration.chat_test import AUTH, web_client
from tests.unit.modules.user_chat_test import FixtureConfig

pytestmark = pytest.mark.integration


def _model(tmp_path, look):
    executable = tmp_path / "fake-claude"
    assert executable.parent.resolve().is_relative_to(tmp_path.resolve())
    assert not executable.exists()
    assert not executable.is_symlink()
    executable.write_text(f"""#!{sys.executable}
import json,sys
if sys.argv[1:] == ['auth','status','--json']:
 print(json.dumps({{'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty'}})); raise SystemExit
if sys.argv[1:] == ['--version']:
 print('2.1.312 (Claude Code)'); raise SystemExit
payload=json.load(sys.stdin)
if len(payload['messages']) == 1:
 wire={{'kind':'tool','name':'use_tool','arguments':{{'name':'profile:propose','arguments':{{'expected_revision':1,'name':'Original','appearance':{look!r}}}}},'text':None}}
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
    return executable


def test_chat_color_suggestion_requires_owner_save_and_survives_reopen(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    look = {"age": "old", "beard": "long", "hat": "blue", "shirt": "rose", "build": "round", "accessories": ["scarf"]}
    executable = _model(tmp_path, look)
    kernel.bind(
        UserChatService, UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
    )
    client = web_client(kernel, tmp_path)
    initial = {**look, "hat": "green"}
    created = client.post(
        "/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new", "name": "Original", "appearance": initial}
    )
    assert created.status_code == 200
    session = created.json()["session_id"]
    params = {"session_id": session}
    assert (
        client.post(
            "/api/heinzel/chat",
            headers=AUTH,
            json={**params, "request_id": "look", "message": "Make the hat blue, keep my other preferences"},
        ).status_code
        == 200
    )
    assert (
        client.post("/api/heinzel/answer-chat", headers=AUTH, json={**params, "request_id": "look"}).status_code == 200
    )
    suggestion = client.get("/api/heinzel/latest-profile-proposal", headers=AUTH, params=params).json()
    assert suggestion["appearance"] == look
    assert suggestion["status"] == "proposed"
    before = client.get("/api/heinzel/session", headers=AUTH, params=params).json()
    assert before["appearance"] == initial
    assert before["profile_revision"] == 1
    save = {
        **params,
        "request_id": "save-look",
        "expected_revision": 1,
        "name": suggestion["name"],
        "appearance": suggestion["appearance"],
    }
    assert client.post("/api/heinzel/profile", json=save).status_code == 401
    invalid = {**save, "appearance": {**look, "hat": "neon"}}
    assert client.post("/api/heinzel/profile", headers=AUTH, json=invalid).status_code == 422
    result = client.post("/api/heinzel/profile", headers=AUTH, json=save)
    assert result.status_code == 200, result.text
    assert result.json()["appearance"] == look
    assert result.json()["profile_revision"] == 2
    reopened = client.get("/api/heinzel/session", headers=AUTH, params=params).json()
    assert reopened["appearance"] == look
    assert reopened["name"] == "Original"
    assert client.post("/api/heinzel/profile", headers=AUTH, json={**save, "request_id": "stale"}).status_code != 200
    foreign = CurrentUser(tmp_path / "other-owner" / "identity")
    with foreign.local("cli"):
        foreign_crud = SessionCrud(PersonalSessionRepository(foreign), crud.costs)
        with pytest.raises(HeinzelError):
            foreign_crud.snapshot(session)
