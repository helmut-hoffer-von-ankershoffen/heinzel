"""Real protected heads share durable identity and personal profile state without public private-data routes."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from typer.testing import CliRunner

from heinzel import CurrentUser, Module
from heinzel.heads import api, cli, mcp
from heinzel.heads._exposure import MCP_HTTP, exposed
from heinzel.heads.api._auth import ApiBearer
from heinzel.kernel._approval import ApprovalIntent, ApprovalRecord, approval_proof_hash, arguments_hash
from heinzel.modules.heinzel._session_operations import register_session_operations
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 25, tzinfo=UTC)
BASE = "http://localhost"


def _seed_test_approval(kernel, session_id, request_id, revision, action, contract_version, preview_hash):  # noqa: PLR0913, PLR0917
    current = kernel.resolve(CurrentUser)
    now = datetime.now(UTC)
    proof = sha256(f"proof:{request_id}".encode()).hexdigest()
    intent = ApprovalIntent(
        owner=current.owner().id,
        audience="personal-check-in",
        session=session_id,
        action=action,
        resource="five-minute-check-in",
        arguments_hash=arguments_hash(
            {
                "request_id": request_id,
                "expected_revision": revision,
                "contract_version": contract_version,
                "preview_hash": preview_hash,
            }
        ),
        contract_revision=contract_version,
        grant_revision=0,
        policy_revision=0,
    )
    record = ApprovalRecord(
        intent=intent,
        proof_hash=approval_proof_hash(proof),
        nonce=sha256(f"nonce:{request_id}".encode()).hexdigest(),
        provenance="test-only-synthetic-decision",
        decision="approve",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    repository = PersonalSessionRepository(current)
    with current.local("cli"), repository.control_writer(session_id) as writer:
        writer.append("approval.issued", key=record.proof_hash, data=record.model_dump(mode="json"))
    return proof


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


def test_propose_activate_pause_and_resume_five_minute_check_in_through_private_api(kernel):  # noqa: PLR0915
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
    assert proposed["state"] == "proposed"
    assert proposed["schedule"]["cron"] == "*/5 * * * *"

    activation = {
        "session_id": created["session_id"],
        "request_id": "check-in-activate",
        "expected_revision": 1,
        "action": "activate",
        "expected_contract_version": proposed["schedule"]["contract_version"],
        "expected_preview_hash": proposed["schedule"]["preview_hash"],
    }
    denied = client.post("/heinzel/schedule-control", headers=auth, json=activation)
    assert denied.status_code == 400
    approval = _seed_test_approval(
        kernel,
        created["session_id"],
        activation["request_id"],
        activation["expected_revision"],
        activation["action"],
        activation["expected_contract_version"],
        activation["expected_preview_hash"],
    )
    unsafe_cli = CliRunner().invoke(
        cli.build(kernel),
        [
            "heinzel",
            "schedule-control",
            created["session_id"],
            activation["request_id"],
            str(activation["expected_revision"]),
            activation["action"],
            "--approval-proof",
            approval,
            "--expected-contract-version",
            str(activation["expected_contract_version"]),
            "--expected-preview-hash",
            activation["expected_preview_hash"],
            "--json",
        ],
    )
    assert unsafe_cli.exit_code == 1
    assert "not exposed in process arguments" in unsafe_cli.output
    activated = client.post(
        "/heinzel/schedule-control",
        headers=auth,
        json={**activation, "approval_proof": approval},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["schedule"]["status"] == "active"
    assert activated.json()["contracts"][0]["status"] == "approved"
    web_client = TestClient(api.build(kernel, head="web", token="check-in-web"), base_url=BASE)
    web_retry = web_client.post(
        "/heinzel/schedule-control",
        headers={"Authorization": "Bearer check-in-web"},
        json={**activation, "approval_proof": approval},
    )
    assert web_retry.status_code == 200, web_retry.text
    assert web_retry.json()["schedule"]["status"] == "active"

    cli_retry = CliRunner().invoke(
        cli.build(kernel),
        [
            "heinzel",
            "schedule-control",
            created["session_id"],
            activation["request_id"],
            str(activation["expected_revision"]),
            activation["action"],
            "--approval-proof",
            "-",
            "--expected-contract-version",
            str(activation["expected_contract_version"]),
            "--expected-preview-hash",
            activation["expected_preview_hash"],
            "--json",
        ],
        input=approval + "\n",
    )
    assert cli_retry.exit_code == 0, cli_retry.output
    assert approval not in cli_retry.output
    assert json.loads(cli_retry.output)["schedule"]["status"] == "active"

    async def exact_retry_from_stdio_mcp():
        async with Client(mcp.build(kernel)) as connected:
            tool = next(
                item for item in (await connected.list_tools()).tools if item.name == "heinzel_schedule_control"
            )
            assert "approval_proof" not in tool.input_schema["properties"]
            result = await connected.call_tool("heinzel_schedule_control", activation)
            assert not result.is_error
            assert result.structured_content["schedule"]["status"] == "active"
            assert approval not in str(result.content)

    anyio.run(exact_retry_from_stdio_mcp)
    with kernel.resolve(CurrentUser).local("cli"):
        approval_events = (
            PersonalSessionRepository(kernel.resolve(CurrentUser), clock=lambda: NOW)
            .control_read(created["session_id"])
            .events
        )
    activated_events = [event for event in approval_events if event.kind == "schedule.activated"]
    assert len(activated_events) == 1
    assert activated_events[0].data["proof_hash"] == approval_proof_hash(approval)
    assert approval not in json.dumps([event.model_dump(mode="json") for event in approval_events])
    assert not any(event.kind == "approval.intent" for event in approval_events)

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
    resume = {
        "session_id": created["session_id"],
        "request_id": "check-in-resume",
        "expected_revision": 3,
        "action": "resume",
        "expected_contract_version": proposed["schedule"]["contract_version"],
        "expected_preview_hash": proposed["schedule"]["preview_hash"],
    }
    resume_proof = _seed_test_approval(
        kernel,
        created["session_id"],
        resume["request_id"],
        resume["expected_revision"],
        resume["action"],
        resume["expected_contract_version"],
        resume["expected_preview_hash"],
    )
    resumed = client.post(
        "/heinzel/schedule-control",
        headers=auth,
        json={**resume, "approval_proof": resume_proof},
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


def test_stdio_mcp_can_create_propose_inspect_and_pause_but_cannot_activate_without_human_proof(kernel):
    client, auth = protected(kernel)

    async def create_and_inspect_tools():
        async with Client(mcp.build(kernel)) as connected:
            available = {tool.name for tool in (await connected.list_tools()).tools}
            assert {
                "heinzel_new_chat",
                "heinzel_sessions",
                "heinzel_session",
                "heinzel_propose_check_in",
                "heinzel_schedule_control",
            } <= available
            created = await connected.call_tool("heinzel_new_chat", {"request_id": "mcp-create", "name": "MCP helper"})
            assert not created.is_error
            session = created.structured_content["session_id"]
            listed = await connected.call_tool("heinzel_sessions", {})
            assert not listed.is_error
            assert any(item["session_id"] == session for item in listed.structured_content["sessions"])
            inspected = await connected.call_tool("heinzel_session", {"session_id": session})
            assert not inspected.is_error
            assert inspected.structured_content["schedule"] is None
            proposed = await connected.call_tool(
                "heinzel_propose_check_in",
                {"session_id": session, "request_id": "mcp-proposal", "timezone": "UTC"},
            )
            assert not proposed.is_error
            assert proposed.structured_content["schedule"]["status"] == "proposed"
            return session, proposed.structured_content["schedule"]

    session, schedule = anyio.run(create_and_inspect_tools)
    activation = {
        "session_id": session,
        "request_id": "mcp-human-activation",
        "expected_revision": schedule["revision"],
        "action": "activate",
        "expected_contract_version": schedule["contract_version"],
        "expected_preview_hash": schedule["preview_hash"],
    }

    async def reject_unapproved_mcp_activation():
        async with Client(mcp.build(kernel)) as connected:
            result = await connected.call_tool("heinzel_schedule_control", activation)
            assert result.is_error

    anyio.run(reject_unapproved_mcp_activation)
    proof = _seed_test_approval(
        kernel,
        session,
        activation["request_id"],
        activation["expected_revision"],
        activation["action"],
        activation["expected_contract_version"],
        activation["expected_preview_hash"],
    )
    activated = client.post("/heinzel/schedule-control", headers=auth, json={**activation, "approval_proof": proof})
    assert activated.status_code == 200, activated.text

    async def pause_and_attempt_resume():
        async with Client(mcp.build(kernel)) as connected:
            paused = await connected.call_tool(
                "heinzel_schedule_control",
                {"session_id": session, "request_id": "mcp-pause", "expected_revision": 2, "action": "pause"},
            )
            assert not paused.is_error
            assert paused.structured_content["schedule"]["status"] == "paused"
            inspected = await connected.call_tool("heinzel_session", {"session_id": session})
            assert inspected.structured_content["schedule"]["status"] == "paused"
            resumed_without_proof = await connected.call_tool(
                "heinzel_schedule_control",
                {"session_id": session, "request_id": "mcp-resume-denied", "expected_revision": 3, "action": "resume"},
            )
            assert resumed_without_proof.is_error

    anyio.run(pause_and_attempt_resume)

    snapshot = client.get("/heinzel/session", headers=auth, params={"session_id": session})
    assert snapshot.status_code == 200
    assert snapshot.json()["schedule"]["status"] == "paused"


def test_public_http_schema_and_network_mcp_do_not_expose_private_session_operations(kernel):
    client = TestClient(api.build(kernel), base_url=BASE)
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(path.startswith("/heinzel/") for path in paths)
    assert client.get("/heinzel/sessions").status_code == 404
    assert not list(exposed(kernel, MCP_HTTP))


def test_scheduled_agent_proposal_requires_private_identity_and_remains_inert(kernel):
    client, auth = protected(kernel)
    session_id = client.post("/heinzel/new-chat", headers=auth, json={"request_id": "create-preview"}).json()[
        "session_id"
    ]
    payload = {
        "session_id": session_id,
        "request_id": "review-1",
        "contract": "Produce a short check-in",
        "max_usd": 0.1,
        "timeout_seconds": 30,
    }
    assert client.post("/heinzel/propose-scheduled-agent", json=payload).status_code == 401
    response = client.post("/heinzel/propose-scheduled-agent", headers=auth, json=payload)
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal["status"] == "proposed"
    assert proposal["contract"] == payload["contract"]
    assert proposal["permitted_tools"] == []
    inspected = client.get(
        "/heinzel/scheduled-agent-proposal", headers=auth, params={"session_id": session_id, "request_id": "review-1"}
    )
    assert inspected.status_code == 200, inspected.text
    assert inspected.json() == proposal
    assert client.get("/heinzel/session", headers=auth, params={"session_id": session_id}).json()["schedule"] is None
