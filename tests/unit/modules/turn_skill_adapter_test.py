"""Explicitly approved skill tools stage only private drafts in owner-bound durable turns."""

import asyncio
import json

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.modules.heinzel._engine import TurnAuthority, TurnDeps, TurnLimits
from heinzel.modules.heinzel._interactive_turn import InteractiveTurn
from heinzel.modules.heinzel._model_grants import ModelGrant, ModelGrants
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._turn_skill_adapter import TurnSkillAdapter

pytestmark = pytest.mark.unit


def _grant(service: SessionCrud, session: str, tools: tuple[str, ...]) -> ModelGrant:
    grant = ModelGrant(
        request_id="grant",
        revision=1,
        access="pydantic-ai",
        model="openai:model",
        requests=3,
        total_tokens=2000,
        output_tokens=200,
        timeout_seconds=30,
        message_cap_usd="1",
        session_cap_usd="2",
        owner_cap_usd="3",
        allowed_skill_tools=tools,
    )
    grants = ModelGrants(service)
    grants.propose(session, grant)
    grants.approve(session, "grant", grants.issue(session, "grant", "fixture-human-review"))
    return grant


def test_fake_model_stages_owned_private_draft_and_reply_survives_restart(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        repository = PersonalSessionRepository(current)
        service = SessionCrud(repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = service.new_chat("owner").session_id
        service.accept(session, MessageRequest(request_id="message", text="Draft a skill"))
        grant = _grant(service, session, ("skill.propose_draft",))
        calls = []

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            calls.append(1)
            if len(calls) == 1:
                assert "skill.propose_draft" in str(info.instructions)
                assert "skill.propose_sharing" not in str(info.instructions)
                return ModelResponse(
                    [
                        ToolCallPart(
                            "use_tool",
                            {
                                "name": "skill.propose_draft",
                                "arguments": {
                                    "request_id": "draft-one",
                                    "name": "review-work",
                                    "requested_scope": "team",
                                    "scope_ref": "team-1",
                                    "files": {
                                        "SKILL.md": "private instructions",
                                        "scripts/run.py": "PRIVATE_SCRIPT_CONTENT",
                                    },
                                },
                            },
                        )
                    ]
                )
            assert any(
                isinstance(part, ToolReturnPart) and '"status": "draft"' in str(part.content)
                for message in messages
                for part in message.parts
            )
            return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "Draft staged for review."})])

        runner = InteractiveTurn(service)
        result = asyncio.run(
            runner.run(session, "message", FunctionModel(model), limits=TurnLimits(), expected_grant=grant)
        )
        assert result.text == "Draft staged for review."
        assert len(calls) == 2
        events = repository.control_read(session).events
        assert "PRIVATE_SCRIPT_CONTENT" not in json.dumps([event.model_dump(mode="json") for event in events])
        draft_event = next(event for event in events if event.kind == "skill.draft")
        reference = draft_event.key.removeprefix("skill-draft-")
        draft = service.skill_draft(session, reference)
        assert draft.files["scripts/run.py"] == "PRIVATE_SCRIPT_CONTENT"
        activity = runner.activity(session)
        assert [(item.tool, item.status) for item in activity] == [("skill.propose_draft", "completed")]
        assert "PRIVATE_SCRIPT_CONTENT" not in activity[0].model_dump_json()
        reopened = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        retry = asyncio.run(
            InteractiveTurn(reopened).run(
                session, "message", FunctionModel(model), limits=TurnLimits(), expected_grant=grant
            )
        )
        assert retry == result
        assert len(calls) == 2
        assert not any(event.kind in {"skill.published", "skill.executed"} for event in events)
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"})
        )
        with current.using(stranger), pytest.raises(HeinzelError):
            InteractiveTurn(reopened).activity(session)
        body_file = (
            current.directory.parent
            / "heinzel-sessions"
            / current.principal().id
            / "control"
            / session
            / "turns"
            / f"{draft_event.data['body_id']}.jsonl"
        )
        body_file.write_text('"corrupt private body"\n')
        with pytest.raises(HeinzelError):
            runner.activity(session)


def test_skill_adapter_checks_exact_grant_lock_owner_and_private_sharing(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        service = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = service.new_chat("owner").session_id
        grant = _grant(service, session, ("skill.propose_draft", "skill.propose_sharing"))
        deps = TurnDeps(
            session,
            "turn-one",
            "asked",
            TurnAuthority(
                principal_id=current.principal().id,
                backend_id="offline",
                binding_revision=1,
                grant_revision=grant.revision,
                policy_revision=1,
            ),
        )
        arguments = {
            "request_id": "draft",
            "name": "review-work",
            "requested_scope": "team",
            "scope_ref": "team-1",
            "files": {"SKILL.md": "private", "scripts/a.py": "SECRET_SCRIPT"},
        }
        with repo.main_writer(session) as writer:
            tool = TurnSkillAdapter(service, deps, grant, writer)
            first = json.loads(tool.dispatch("skill.propose_draft", arguments))
            assert json.loads(tool.dispatch("skill.propose_draft", arguments)) == first
            changed = {**arguments, "files": {"SKILL.md": "CHANGED_SECRET"}}
            refused = tool.dispatch("skill.propose_draft", changed)
            assert "Could not confirm" in refused
            assert "CHANGED_SECRET" not in refused
            sharing = {
                "request_id": "share",
                "draft_request_id": first["request_id"],
                "draft_sha256": first["preview_sha256"],
            }
            proposal = json.loads(tool.dispatch("skill.propose_sharing", sharing))
            assert proposal["authority"] == "unverified"
            assert proposal["status"] == "awaiting_review"
            assert json.loads(tool.dispatch("skill.propose_sharing", sharing)) == proposal
            assert "Could not confirm" in tool.dispatch("skill.propose_sharing", {**sharing, "session_id": session})
        with pytest.raises(HeinzelError, match="turn lock"):
            tool.dispatch("skill.propose_draft", arguments)
        events = repo.control_read(session).events
        assert len([e for e in events if e.kind == "interactive.skill-tool"]) == 2
        assert "SECRET_SCRIPT" not in json.dumps([e.model_dump(mode="json") for e in events])
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"})
        )
        with current.using(stranger), pytest.raises(HeinzelError, match="unavailable"):
            tool.dispatch("skill.propose_draft", arguments)


def test_ungranted_skill_tool_cannot_stage_a_draft(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        service = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = service.new_chat("owner").session_id
        service.accept(session, MessageRequest(request_id="message", text="Create a skill"))
        grant = _grant(service, session, ())
        calls = []

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            calls.append(1)
            if len(calls) == 1:
                return ModelResponse(
                    [
                        ToolCallPart(
                            "use_tool",
                            {
                                "name": "skill.propose_draft",
                                "arguments": {
                                    "request_id": "draft",
                                    "name": "review-work",
                                    "requested_scope": "personal",
                                    "files": {"SKILL.md": "PRIVATE_DENIED_CONTENT"},
                                },
                            },
                        )
                    ]
                )
            assert "unavailable" in str(messages)
            return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "No skill created"})])

        runner = InteractiveTurn(service)
        result = asyncio.run(
            runner.run(session, "message", FunctionModel(model), limits=TurnLimits(), expected_grant=grant)
        )
        assert result.text == "No skill created"
        events = repo.control_read(session).events
        assert not any(e.kind in {"skill.draft", "interactive.skill-tool"} for e in events)
        assert "PRIVATE_DENIED_CONTENT" not in json.dumps([event.model_dump(mode="json") for event in events])
        assert [(item.tool, item.status) for item in runner.activity(session)] == [("unavailable", "denied")]
