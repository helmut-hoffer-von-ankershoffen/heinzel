"""Owner-private real chat operations with a fake Claude executable and no external calls."""

import pytest

from heinzel import CurrentUser
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests.integration.chat_test import AUTH, web_client
from tests.unit.modules.user_chat_test import FixtureConfig, fake_claude

pytestmark = pytest.mark.integration


def test_private_answer_and_activity_operations_use_durable_request(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    executable = fake_claude(tmp_path)
    kernel.bind(
        UserChatService, UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
    )
    client = web_client(kernel, tmp_path)
    created = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"})
    assert created.status_code == 200, created.text
    session = created.json()["session_id"]
    accepted = client.post(
        "/api/heinzel/chat", headers=AUTH, json={"session_id": session, "request_id": "hello", "message": "Hello"}
    )
    assert accepted.status_code == 200, accepted.text
    request = {"session_id": session, "request_id": "hello"}
    assert client.post("/api/heinzel/answer-chat", json=request).status_code == 401
    answer = client.post("/api/heinzel/answer-chat", headers=AUTH, json=request)
    assert answer.status_code == 200, answer.text
    assert answer.json()["message"]["text"] == "A real recorded reply"
    again = client.post("/api/heinzel/answer-chat", headers=AUTH, json=request)
    assert again.status_code == 200
    assert again.json() == answer.json()
    activity = client.get("/api/heinzel/tool-activity", headers=AUTH, params={"session_id": session})
    assert activity.status_code == 200
    assert activity.json()["items"][0]["status"] == "denied"
    assert (tmp_path / "model-calls").read_text() == "xx"


def test_profile_suggestion_is_owner_private_and_never_applied_by_model(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    executable = fake_claude(
        tmp_path,
        tool="profile:propose",
        arguments={
            "expected_revision": 1,
            "name": "Suggested",
            "appearance": {"age": "grown", "build": "round"},
        },
    )
    kernel.bind(
        UserChatService, UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
    )
    client = web_client(kernel, tmp_path)
    created = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new", "name": "Original"})
    assert created.status_code == 200, created.text
    session = created.json()["session_id"]
    accepted = client.post(
        "/api/heinzel/chat",
        headers=AUTH,
        json={"session_id": session, "request_id": "change-look", "message": "Suggest a different look"},
    )
    assert accepted.status_code == 200, accepted.text
    answered = client.post(
        "/api/heinzel/answer-chat", headers=AUTH, json={"session_id": session, "request_id": "change-look"}
    )
    assert answered.status_code == 200, answered.text
    suggestion = client.get("/api/heinzel/latest-profile-proposal", headers=AUTH, params={"session_id": session})
    assert suggestion.status_code == 200, suggestion.text
    assert suggestion.json()["name"] == "Suggested"
    assert suggestion.json()["status"] == "proposed"
    unchanged = client.get("/api/heinzel/session", headers=AUTH, params={"session_id": session})
    assert unchanged.json()["name"] == "Original"
    assert unchanged.json()["profile_revision"] == 1
    assert client.get("/api/heinzel/latest-profile-proposal", params={"session_id": session}).status_code == 401


def test_subscription_quota_error_has_safe_code_and_is_not_replayed(make_kernel, tmp_path):
    import json

    from heinzel.modules.system.runner._process import Outcome

    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    executable = fake_claude(tmp_path)
    calls = []

    def execute(command, **_kwargs):
        if command[1:] == ["auth", "status", "--json"]:
            return Outcome(
                0, json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}), ""
            )
        if command[1:] == ["--version"]:
            return Outcome(0, "2.1.312 (Claude Code)\n", "")
        calls.append(1)
        return Outcome(
            1,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "api_error_status": 429,
                    "api_error": "usage_limit_reached",
                    "result": "sensitive provider details",
                }
            ),
            "",
        )

    kernel.bind(
        UserChatService,
        UserChatService(
            crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable), execute=execute
        ),
    )
    client = web_client(kernel, tmp_path)
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "quota"}).json()["session_id"]
    client.post("/api/heinzel/chat", headers=AUTH, json={"session_id": session, "request_id": "hello", "message": "Hi"})
    body = {"session_id": session, "request_id": "hello"}
    response = client.post("/api/heinzel/answer-chat", headers=AUTH, json=body)
    assert response.status_code == 400
    assert response.json()["code"] == "subscription_usage_limit"
    assert "provider settings" in response.json()["error"]
    assert "sensitive" not in response.text
    retry = client.post("/api/heinzel/answer-chat", headers=AUTH, json=body)
    assert retry.status_code == 400
    assert calls == [1]


def test_owner_explicitly_sets_aside_uncertain_turn_and_can_continue(make_kernel, tmp_path):
    import json

    from heinzel.heads._exposure import exposed
    from heinzel.modules.system.runner._process import Outcome, run_bounded

    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    executable = fake_claude(tmp_path)
    quota_calls = []

    def execute(command, **kwargs):
        if "--json-schema" in command and not quota_calls:
            quota_calls.append(1)
            return Outcome(
                1,
                json.dumps(
                    {"type": "result", "is_error": True, "api_error_status": 429, "api_error": "usage_limit_reached"}
                ),
                "",
            )
        return run_bounded(command, **kwargs)

    kernel.bind(
        UserChatService,
        UserChatService(
            crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable), execute=execute
        ),
    )
    client = web_client(kernel, tmp_path)
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "resume"}).json()["session_id"]
    first = {"session_id": session, "request_id": "first"}
    client.post("/api/heinzel/chat", headers=AUTH, json={**first, "message": "First request"})
    assert (
        client.post("/api/heinzel/answer-chat", headers=AUTH, json=first).json()["code"] == "subscription_usage_limit"
    )
    second = {"session_id": session, "request_id": "second"}
    client.post("/api/heinzel/chat", headers=AUTH, json={**second, "message": "Second request"})
    blocked = client.post("/api/heinzel/answer-chat", headers=AUTH, json=second)
    assert blocked.status_code == 400
    assert "uncertain" in blocked.json()["error"]
    state_path = "/api/heinzel/chat-turn-state"
    assert client.get(state_path, headers=AUTH, params=first).json()["status"] == "unresolved"
    path = "/api/heinzel/abandon-chat-turn"
    assert client.post(path, json={**first, "acknowledge_uncertain": True}).status_code == 401
    assert client.post(path, headers=AUTH, json={**first, "acknowledge_uncertain": False}).status_code == 400
    resolved = client.post(path, headers=AUTH, json={**first, "acknowledge_uncertain": True})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json() == {**first, "status": "abandoned"}
    assert client.get(state_path, headers=AUTH, params=first).json()["status"] == "abandoned"
    assert client.post(path, headers=AUTH, json={**first, "acknowledge_uncertain": True}).json() == resolved.json()
    assert client.post("/api/heinzel/answer-chat", headers=AUTH, json=first).status_code == 400
    result = client.post("/api/heinzel/answer-chat", headers=AUTH, json=second)
    assert result.status_code == 200, result.text
    assert result.json()["message"]["text"] == "A real recorded reply"
    assert quota_calls == [1]
    context = (tmp_path / "prompt-input").read_text()
    assert "First request" in context
    assert "set aside with an uncertain outcome" in context
    assert "Second request" in context
    assert "heinzel_abandon_chat_turn" not in {item.name for item in exposed(kernel, "mcp-stdio")}


def test_schedule_card_and_daemon_registry_run_one_owner_bound_fake_turn(make_kernel, tmp_path, settings, monkeypatch):  # noqa: PLR0915 - complete reviewed lifecycle
    from datetime import UTC, datetime

    from heinzel.heads._exposure import exposed
    from heinzel.kernel import CompiledJobs
    from heinzel.modules.heinzel._autonomous_schedule import AutonomousSchedules
    from heinzel.modules.system.daemon import DaemonService, DaemonSettings

    executable = fake_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HZ_SYSTEM_RUNNER__CLAUDE_EXECUTABLE", str(executable))
    monkeypatch.setenv("HZ_CAPABILITIES__AI_ACCESS", "claude-code")
    monkeypatch.setenv("HZ_CAPABILITIES__AGENT_RUNNER", "claude-code")
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    kernel.resolve(UserChatService)
    kernel.resolve(AutonomousSchedules)
    client = web_client(kernel, tmp_path)
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "job"}).json()["session_id"]
    with current.local("cli"):
        contract = crud.propose_scheduled_agent(session, "contract", "Make a brief report", None, 30)
    card = client.post(
        "/api/system/runner/chat/propose-agent-contract",
        headers=AUTH,
        params={"durable_session": session, "request_id": "contract"},
    ).json()
    action = next(a for a in card["actions"] if a["operation"] == "accept-agent-contract")
    confirmed = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": card["id"], "action": action["id"]}
    )
    assert confirmed.status_code == 200, confirmed.text
    args = {
        "session_id": session,
        "request_id": "schedule",
        "contract_request_id": "contract",
        "cadence_seconds": 300,
        "max_requests": 4,
        "timeout_seconds": 30,
    }
    proposed = client.post("/api/heinzel/propose-agent-schedule", headers=AUTH, json=args)
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["contract_sha256"] == contract.preview_sha256
    assert proposed.json()["first_run"] == "current-slot-after-approval"
    params = {"durable_session": session, "request_id": "schedule"}
    card = client.post("/api/system/runner/chat/propose-agent-schedule-review", headers=AUTH, params=params)
    assert card.status_code == 200, card.text
    action = next(a for a in card.json()["actions"] if a["operation"] == "approve-agent-schedule")
    assert action["arguments"]["review"] == proposed.json()
    direct = client.post(
        "/api/heinzel/approve-agent-schedule",
        headers=AUTH,
        json={"session_id": session, "request_id": "schedule", "review": proposed.json(), "approval_proof": "fake"},
    )
    assert direct.status_code == 400
    exposed_names = {item.name for item in exposed(kernel, "mcp-stdio")}
    assert "heinzel_approve_agent_schedule" not in exposed_names
    assert "heinzel_propose_agent_schedule" not in exposed_names
    assert "system_runner_chat_propose_agent_schedule_review" not in exposed_names
    result = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": card.json()["id"], "action": action["id"]}
    )
    assert result.status_code == 200, result.text
    assert next(a for a in result.json()["actions"] if a["id"] == action["id"])["status"] == "done"
    from heinzel.modules.system.runner.chat._models import ChatMessage
    from heinzel.modules.system.runner.chat._service import transcript

    visible = transcript([ChatMessage.model_validate(item) for item in result.json()["messages"]])
    assert proposed.json()["access_fingerprint"] not in visible
    assert proposed.json()["owner"] not in visible
    state = client.get(
        "/api/heinzel/agent-schedule", headers=AUTH, params={"session_id": session, "request_id": "schedule"}
    )
    assert state.json()["status"] == "approved"
    with current.local("daemon"):
        jobs = kernel.resolve(CompiledJobs).jobs
        job = next(job for job in jobs if job.name.startswith("agent-" + session.lower()))
        assert job.scheduled_only
        assert job.retries == 0
        now = datetime.now(UTC)
        daemon = DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now, zone=UTC)
        registered = [item for item in daemon._jobs() if item.id == "heinzel:" + job.name]
        assert len(registered) == 1
        monkeypatch.setattr(daemon, "_jobs", lambda: registered)
        monkeypatch.setattr(daemon, "_post_notices", lambda _log: None)
        report = daemon.tick()
        assert any(item.job == "heinzel:" + job.name and item.disposition == "rendered" for item in report.runs)
        count = (tmp_path / "model-calls").read_text()
        restarted = DaemonService(kernel, settings, DaemonSettings(), clock=lambda: now, zone=UTC)
        monkeypatch.setattr(restarted, "_jobs", lambda: registered)
        monkeypatch.setattr(restarted, "_post_notices", lambda _log: None)
        restarted.tick()
        assert (tmp_path / "model-calls").read_text() == count
    paused = client.post(
        "/api/heinzel/pause-agent-schedule", headers=AUTH, json={"session_id": session, "request_id": "schedule"}
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    listed = client.get("/api/heinzel/agent-schedules", headers=AUTH, params={"session_id": session})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["status"] == "paused"
    assert client.get("/api/heinzel/agent-schedules", params={"session_id": session}).status_code == 401
    assert client.get("/api/heinzel/session", headers=AUTH, params={"session_id": session}).json()["schedule"] is None


def test_chat_schedule_suggestion_is_private_and_inert(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    executable = fake_claude(
        tmp_path,
        tool="schedule:propose",
        arguments={
            "expected_revision": 0,
            "trigger": {"cron": "0 9 * * 1", "timezone": "UTC"},
            "suggested_reads": [],
        },
    )
    kernel.bind(
        UserChatService, UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
    )
    client = web_client(kernel, tmp_path)
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"}).json()["session_id"]
    accepted = client.post(
        "/api/heinzel/chat", headers=AUTH, json={"session_id": session, "request_id": "hello", "message": "Schedule it"}
    )
    assert accepted.status_code == 200
    response = client.post(
        "/api/heinzel/answer-chat", headers=AUTH, json={"session_id": session, "request_id": "hello"}
    )
    assert response.status_code == 200, response.text
    params = {"session_id": session}
    assert client.get("/api/heinzel/latest-schedule-suggestion-view", params=params).status_code == 401
    suggestion = client.get("/api/heinzel/latest-schedule-suggestion-view", headers=AUTH, params=params)
    assert suggestion.status_code == 200, suggestion.text
    assert suggestion.json()["status"] == "suggested"
    assert suggestion.json()["trigger"] == {"cron": "0 9 * * 1", "timezone": "UTC"}
    assert client.get("/api/heinzel/session", headers=AUTH, params=params).json()["schedule"] is None


def test_chat_source_suggestion_private_inert_and_replays_without_provider(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    executable = fake_claude(
        tmp_path,
        tool="source:propose",
        arguments={
            "expected_revision": 0,
            "capability": "documents",
            "method": "read",
            "fixed_arguments": {"ref": "https://example.invalid/document"},
        },
    )
    kernel.bind(
        UserChatService, UserChatService(crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable))
    )
    client = web_client(kernel, tmp_path)
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"}).json()["session_id"]
    assert (
        client.post(
            "/api/heinzel/chat",
            headers=AUTH,
            json={"session_id": session, "request_id": "hello", "message": "Please examine this source"},
        ).status_code
        == 200
    )
    answer = client.post("/api/heinzel/answer-chat", headers=AUTH, json={"session_id": session, "request_id": "hello"})
    assert answer.status_code == 200, answer.text
    params = {"session_id": session}
    assert client.get("/api/heinzel/latest-source-suggestion-view", params=params).status_code == 401
    suggestion = client.get("/api/heinzel/latest-source-suggestion-view", headers=AUTH, params=params)
    assert suggestion.status_code == 200, suggestion.text
    assert suggestion.json()["fixed_arguments"] == {"ref": "https://example.invalid/document"}
    assert suggestion.json()["status"] == "suggested"
    prepare = {
        "session_id": session,
        "request_id": "prepare",
        "suggestion_request": suggestion.json()["request_id"],
        "suggestion_revision": 2,
    }
    assert client.post("/api/heinzel/prepare-suggested-source", headers=AUTH, json=prepare).status_code == 400
    assert (
        client.post(
            "/api/heinzel/prepare-suggested-source", headers=AUTH, json={**prepare, "fixed_arguments": {"ref": "other"}}
        ).status_code
        == 422
    )
    assert client.post("/api/heinzel/prepare-suggested-source", json=prepare).status_code == 401
    state = client.get("/api/heinzel/connected-read-state", headers=AUTH, params=params)
    assert state.json()["status"] == "none"
    replay = client.post("/api/heinzel/answer-chat", headers=AUTH, json={"session_id": session, "request_id": "hello"})
    assert replay.status_code == 200
    assert replay.json() == answer.json()
