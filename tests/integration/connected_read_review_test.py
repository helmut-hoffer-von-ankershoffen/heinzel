"""Private human review of an owner-bound connected read with an inert fake provider."""

import pytest

from heinzel import (
    Config,
    ConnectedReadLease,
    ConnectedReadReview,
    ConnectedReadService,
    CurrentUser,
    HeinzelError,
    Module,
)
from heinzel.heads._exposure import exposed
from heinzel.kernel._approval import arguments_hash
from heinzel.modules.heinzel._capability_review import describe_capability_read
from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants
from tests.integration.chat_test import AUTH, web_client

pytestmark = pytest.mark.integration


def _fail_if(state, where):
    if state.get("failure") == where:
        msg = f"secret-sentinel-{where}"
        raise HeinzelError(msg)


def _mutate_if_requested(state, fixed_arguments):
    if state.get("failure") == "mutate":
        fixed_arguments["window"]["since"] = "2025-01-01T00:00:00Z"


def test_human_action_only_approves_exact_private_connected_read(make_kernel, tmp_path, monkeypatch):  # noqa: PLR0915
    state = {"opens": 0, "reads": 0, "account": "account-one"}
    holder = {}

    class Lease(ConnectedReadLease):
        def __init__(self, session, operation, fixed):
            self.session, self.operation, self.fixed = session, operation, fixed

        def review(self):
            _fail_if(state, "review")
            descriptor = describe_capability_read(holder["kernel"], "calendar", "events")
            return ConnectedReadReview(
                owner=holder["current"].principal().id,
                session=self.session,
                operation=self.operation,
                schema_sha256=arguments_hash(descriptor.schema),
                source_revision="source-one",
                account_identity=state["account"],
                provider_scopes=("read",),
                fixed_arguments=self.fixed,
            )

        def read(self, _arguments):
            state["reads"] += 1
            return {"events": []}

        def close(self):
            _fail_if(state, "close")

    class Provider(ConnectedReadService):
        def open_lease(self, session, operation, fixed_arguments):
            state["opens"] += 1
            _fail_if(state, "open")
            _mutate_if_requested(state, fixed_arguments)
            return Lease(session, operation, dict(fixed_arguments))

    class Calendar:
        def events(self, _window):
            return []

        def free_busy(self, _people, _window):
            return []

    module = Module("fictional", "Offline fixture")
    module.service(Provider)
    module.provides("calendar", modes={"read"}, name="fictional", hosts=["fixture.invalid"])(Calendar)
    from tests.unit.modules.user_chat_test import fake_claude

    executable = fake_claude(tmp_path, tool="connected_read", arguments={})
    monkeypatch.setenv("HZ_SYSTEM_RUNNER__CLAUDE_EXECUTABLE", str(executable))
    monkeypatch.setenv("HZ_CAPABILITIES__AI_ACCESS", "claude-code")
    monkeypatch.setenv("HZ_CAPABILITIES__AGENT_RUNNER", "claude-code")
    config = Config(files=(), data={"capabilities": {"calendar": "fictional"}})
    kernel = make_kernel(builtins=True, config=config, fictional=module)
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    holder.update(kernel=kernel, current=current)
    monkeypatch.setattr(Config, "load", classmethod(lambda _cls, _cwd=None: config))
    client = web_client(kernel, tmp_path)
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"}).json()["session_id"]
    body = {
        "session_id": session,
        "request_id": "grant-one",
        "capability": "calendar",
        "method": "events",
        "fixed_arguments": {"window": {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}},
    }
    assert client.post("/api/heinzel/propose-connected-read", json=body).status_code == 401
    missing = client.post(
        "/api/heinzel/propose-connected-read",
        headers=AUTH,
        json={**body, "session_id": "hz-01ARZ3NDEKTSV4RRFFQ69G5FAV"},
    )
    assert missing.status_code == 400
    assert state["opens"] == 0
    assert client.get("/api/heinzel/connected-read-state", params={"session_id": session}).status_code == 401
    options = client.get("/api/heinzel/connected-read-options", headers=AUTH, params={"session_id": session})
    assert options.status_code == 200
    assert any(option["capability"] == "calendar" and option["method"] == "events" for option in options.json())
    assert state["opens"] == 0
    assert (
        client.get("/api/heinzel/connected-read-state", headers=AUTH, params={"session_id": session}).json()["status"]
        == "none"
    )
    for failure in ("open", "review", "close", "mutate"):
        state["failure"] = failure
        failed = client.post("/api/heinzel/propose-connected-read", headers=AUTH, json=body)
        assert failed.status_code == 400
        assert "secret-sentinel" not in failed.text
    state["failure"] = None
    proposed = client.post("/api/heinzel/propose-connected-read", headers=AUTH, json=body)
    assert proposed.status_code == 200, proposed.text
    assert state["reads"] == 0
    assert (
        client.get("/api/heinzel/connected-read-state", headers=AUTH, params={"session_id": session}).json()["status"]
        == "proposed"
    )
    params = {"durable_session": session, "request_id": "grant-one"}
    stage = client.post("/api/system/runner/chat/propose-connected-read-review", headers=AUTH, params=params)
    assert stage.status_code == 200, stage.text
    chat = stage.json()
    action = next(item for item in chat["actions"] if item["operation"] == "approve-connected-read")
    assert action["arguments"]["review"] == proposed.json()
    assert "approval_proof" not in action["arguments"]
    assert (
        client.post(
            "/api/heinzel/approve-connected-read",
            headers=AUTH,
            json={
                "session_id": session,
                "request_id": "grant-one",
                "review": proposed.json(),
                "approval_proof": "fake",
            },
        ).status_code
        == 400
    )
    assert "heinzel_approve_connected_read" not in {item.name for item in exposed(kernel, "mcp-stdio")}
    assert state["reads"] == 0
    state["account"] = "different-account"
    stale = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": chat["id"], "action": action["id"]}
    )
    assert stale.status_code == 400
    assert state["reads"] == 0
    state["account"] = "account-one"
    result = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": chat["id"], "action": action["id"]}
    )
    assert result.status_code == 200, result.text
    assert next(item for item in result.json()["actions"] if item["id"] == action["id"])["status"] == "done"
    assert state["reads"] == 0
    again = client.post("/api/system/runner/chat/propose-connected-read-review", headers=AUTH, params=params)
    assert again.json()["actions"] == result.json()["actions"]
    with current.local("cli"):
        assert kernel.resolve(ConnectedReadGrants).approved(session).review.account_identity == "account-one"
    accepted = client.post(
        "/api/heinzel/chat",
        headers=AUTH,
        json={"session_id": session, "request_id": "ask", "message": "Read the approved source."},
    )
    assert accepted.status_code == 200, accepted.text
    answer = client.post("/api/heinzel/answer-chat", headers=AUTH, json={"session_id": session, "request_id": "ask"})
    assert answer.status_code == 200, answer.text
    assert state["reads"] == 1
    activity = client.get("/api/heinzel/tool-activity", headers=AUTH, params={"session_id": session}).json()
    assert [(entry["tool"], entry["status"]) for entry in activity["items"]] == [
        ("connected_read", "requested"),
        ("connected_read", "completed"),
    ]
    revoke = client.post(
        "/api/heinzel/revoke-connected-read", headers=AUTH, json={"session_id": session, "request_id": "grant-one"}
    )
    assert revoke.status_code == 200, revoke.text
    with (
        current.local("cli"),
        kernel.resolve(ConnectedReadGrants).crud.repository.main_writer(session) as writer,
        pytest.raises(HeinzelError),
    ):
        kernel.resolve(ConnectedReadGrants).read(session, writer)
    assert state["reads"] == 1
    later = client.post(
        "/api/heinzel/chat",
        headers=AUTH,
        json={"session_id": session, "request_id": "after-revocation", "message": "Continue without the source."},
    )
    assert later.status_code == 200, later.text
    answered = client.post(
        "/api/heinzel/answer-chat",
        headers=AUTH,
        json={"session_id": session, "request_id": "after-revocation"},
    )
    assert answered.status_code == 200, answered.text
    assert state["reads"] == 1
