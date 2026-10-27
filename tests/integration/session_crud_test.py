"""Real protected heads share durable identity and personal profile state without public private-data routes."""

import json
from datetime import UTC, datetime

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from typer.testing import CliRunner

from heinzel import CurrentUser, Module
from heinzel.heads import api, cli, mcp
from heinzel.heads._exposure import MCP_HTTP, exposed
from heinzel.heads.api._auth import ApiBearer
from heinzel.modules.heinzel._session_operations import register_session_operations

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 25, tzinfo=UTC)
BASE = "http://localhost"


@pytest.fixture
def kernel(make_kernel, tmp_path):
    module = Module("heinzel", "Durable personal session acceptance")
    register_session_operations(module)
    kernel = make_kernel(heinzel=module)
    kernel.bind(CurrentUser, CurrentUser(tmp_path / "state" / "identity"))
    return kernel


def protected(kernel):
    token = ApiBearer.issue(kernel.resolve(CurrentUser).owner(), "sessions", now=NOW)
    app = api.build(kernel, credentials=token, audience="sessions", clock=lambda: NOW)
    return TestClient(app, base_url=BASE), {"Authorization": f"Bearer {token.secret}"}


def test_private_http_flat_body_creation_profile_and_retry_survive_reopened_head(kernel):
    client, auth = protected(kernel)
    assert client.post("/heinzel/new-chat", json={"request_id": "new-1"}).status_code == 401
    payload = {"request_id": "new-1", "name": "Project helper", "appearance": {"hat": "teal"}}
    response = client.post("/heinzel/new-chat", headers=auth, json=payload)
    assert response.status_code == 200, response.text
    created = response.json()
    session_id = created["session_id"]
    assert response.headers["Cache-Control"] == "no-store"
    edited = client.post(
        "/heinzel/profile",
        headers=auth,
        json={
            "session_id": session_id,
            "request_id": "edit-1",
            "expected_revision": 1,
            "appearance": {"hat": "blue", "accessories": ["scarf"]},
        },
    )
    assert edited.status_code == 200, edited.text
    reopened, auth = protected(kernel)
    retried = reopened.post("/heinzel/new-chat", headers=auth, json=payload)
    assert retried.json()["session_id"] == session_id
    assert retried.json()["appearance"]["hat"] == "blue"
    listed = reopened.get("/heinzel/sessions", headers=auth).json()["sessions"]
    assert [(item["session_id"], item["name"], item["appearance"]["hat"]) for item in listed] == [
        (session_id, "Project helper", "blue")
    ]
    message = {"session_id": session_id, "request_id": "m1", "message": "Keep this request", "when_busy": "steer"}
    accepted = reopened.post("/heinzel/chat", headers=auth, json=message)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["disposition"] == "queued"
    assert reopened.post("/heinzel/chat", headers=auth, json=message).json() == accepted.json()
    snapshot = reopened.get("/heinzel/session", headers=auth, params={"session_id": session_id}).json()
    assert [(item["id"], item["text"]) for item in snapshot["messages"]] == [("m1", "Keep this request")]
    assert reopened.post("/heinzel/chat", headers=auth, params=message).status_code == 422
    assert reopened.post("/heinzel/chat", headers=auth, json={**message, "principal_id": "other"}).status_code == 422


def test_authenticated_creation_omits_optional_name_and_appearance_and_uses_server_defaults(kernel):
    client, auth = protected(kernel)
    response = client.post("/heinzel/new-chat", headers=auth, json={"request_id": "default-name"})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "New Heinzel"
    assert response.json()["appearance"]["hat"] is None
    schema = client.get("/openapi.json", headers=auth).json()
    assert schema["components"]["schemas"]["heinzel_new_chat_Input"]["required"] == ["request_id"]


def test_propose_activate_pause_and_resume_five_minute_check_in_through_private_api(kernel):
    client, auth = protected(kernel)
    created = client.post(
        "/heinzel/new-chat",
        headers=auth,
        json={"request_id": "check-in-create", "name": "Local helper", "appearance": {"hat": "teal"}},
    ).json()
    proposal = client.post(
        "/heinzel/propose-check-in",
        headers=auth,
        json={"session_id": created["session_id"], "request_id": "check-in-proposal", "timezone": "UTC"},
    )
    assert proposal.status_code == 200, proposal.text
    proposed = proposal.json()
    assert proposed["contracts"][0]["version"] == 1
    assert proposed["contracts"][0]["status"] == "proposed"
    assert proposed["schedule"]["status"] == "proposed"
    assert proposed["schedule"]["cron"] == "*/5 * * * *"

    activated = client.post(
        "/heinzel/schedule-control",
        headers=auth,
        json={
            "session_id": created["session_id"],
            "request_id": "check-in-activate",
            "expected_revision": 1,
            "action": "activate",
        },
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["schedule"]["status"] == "active"
    assert activated.json()["contracts"][0]["status"] == "approved"

    paused = client.post(
        "/heinzel/schedule-control",
        headers=auth,
        json={
            "session_id": created["session_id"],
            "request_id": "check-in-pause",
            "expected_revision": 2,
            "action": "pause",
        },
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["state"] == "paused"
    resumed = client.post(
        "/heinzel/schedule-control",
        headers=auth,
        json={
            "session_id": created["session_id"],
            "request_id": "check-in-resume",
            "expected_revision": 3,
            "action": "resume",
        },
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["state"] == "awake"
    assert resumed.json()["schedule"]["status"] == "active"


def test_cli_stdio_mcp_and_private_http_share_the_same_saved_personal_look(kernel):
    client, auth = protected(kernel)
    result = client.post(
        "/heinzel/new-chat",
        headers=auth,
        json={"request_id": "new-1", "name": "Helper", "appearance": {"shirt": "rose"}},
    ).json()
    runner = CliRunner().invoke(cli.build(kernel), ["heinzel", "sessions", "--json"])
    assert runner.exit_code == 0, runner.output
    listed = json.loads(runner.output)["sessions"]
    assert listed[0]["session_id"] == result["session_id"]
    assert listed[0]["appearance"]["shirt"] == "rose"
    edited = CliRunner().invoke(
        cli.build(kernel),
        ["heinzel", "profile", result["session_id"], "cli-edit", "1", "--appearance", '{"hat":"green"}', "--json"],
    )
    assert edited.exit_code == 0, edited.output
    assert json.loads(edited.output)["appearance"]["hat"] == "green"

    async def scenario():
        async with Client(mcp.build(kernel)) as connected:
            response = await connected.call_tool("heinzel_session", {"session_id": result["session_id"]})
            assert not response.is_error
            assert response.structured_content["appearance"]["hat"] == "green"
            assert response.structured_content["session_id"] == result["session_id"]

    anyio.run(scenario)


def test_public_http_schema_and_network_mcp_do_not_expose_private_session_operations(kernel):
    client = TestClient(api.build(kernel), base_url=BASE)
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(path.startswith("/heinzel/") for path in paths)
    assert client.get("/heinzel/sessions").status_code == 404
    assert not list(exposed(kernel, MCP_HTTP))
