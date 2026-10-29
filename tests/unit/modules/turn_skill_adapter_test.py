"""Real private skill services through a host-bound adapter and offline Pydantic model."""

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.modules.heinzel._engine import TurnAuthority, TurnDeps
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        not hasattr(SessionCrud, "propose_skill_sharing"), reason="requires reviewed private skill sharing foundation"
    ),
]

# The isolated turn branch predates the skill foundation. Load the adapter from this checkout while the test
# runner supplies the reviewed skill foundation on PYTHONPATH; after integration a normal package import suffices.
source = Path(__file__).resolve().parents[3] / "src/heinzel/modules/heinzel/_turn_skill_adapter.py"
spec = importlib.util.spec_from_file_location("heinzel.modules.heinzel._turn_skill_adapter", source)
assert spec
assert spec.loader
adapter_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter_module)
TurnSkillAdapter = adapter_module.TurnSkillAdapter


def test_host_scoped_skill_proposals_and_fake_model_never_leak_private_files(tmp_path):
    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        repository = PersonalSessionRepository(current)
        service = SessionCrud(repository, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = service.new_chat("owner").session_id
        deps = TurnDeps(
            session,
            "turn-one",
            "asked",
            TurnAuthority(
                principal_id=current.principal().id,
                backend_id="offline",
                binding_revision=1,
                grant_revision=1,
                policy_revision=1,
            ),
        )
        tool = TurnSkillAdapter(service, deps)
        calls = 0

        def model(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
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
            return ModelResponse([TextPart("Draft staged for review.")])

        agent = Agent(FunctionModel(model))

        @agent.tool_plain
        def use_tool(name: str, arguments: dict) -> str:
            return tool.dispatch(name, arguments)

        result = agent.run_sync("Create the skill")
        assert result.output == "Draft staged for review."
        events = repository.control_read(session).events
        tool_events = [event for event in events if event.kind == "interactive.skill-tool"]
        assert len(tool_events) == 1
        assert tool_events[0].data == {"tool": "skill.propose_draft", "status": "completed"}
        assert "PRIVATE_SCRIPT_CONTENT" not in json.dumps([e.model_dump(mode="json") for e in events])
        event = next(e for e in events if e.kind == "skill.draft")
        reference = event.key.removeprefix("skill-draft-")
        draft = service.skill_draft(session, reference)
        assert draft.files["scripts/run.py"] == "PRIVATE_SCRIPT_CONTENT"
        retry_args = {
            "request_id": "draft-one",
            "name": "review-work",
            "requested_scope": "team",
            "scope_ref": "team-1",
            "files": {"SKILL.md": "private instructions", "scripts/run.py": "PRIVATE_SCRIPT_CONTENT"},
        }
        reopened_service = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        reopened = TurnSkillAdapter(reopened_service, deps)
        assert json.loads(reopened.dispatch("skill.propose_draft", retry_args))["request_id"] == reference
        changed = {**retry_args, "files": {"SKILL.md": "different PRIVATE_SCRIPT_CONTENT"}}
        refused = reopened.dispatch("skill.propose_draft", changed)
        assert "Could not confirm" in refused
        assert "PRIVATE_SCRIPT_CONTENT" not in refused
        body = {"request_id": "share-one", "draft_request_id": reference, "draft_sha256": draft.preview_sha256}
        first = json.loads(tool.dispatch("skill.propose_sharing", body))
        assert first["authority"] == "unverified"
        assert first["status"] == "awaiting_review"
        assert json.loads(tool.dispatch("skill.propose_sharing", body)) == first
        assert len([e for e in repository.control_read(session).events if e.kind == "interactive.skill-tool"]) == 2
        assert not any(
            e.kind in {"approval.issued", "skill.published", "skill.executed"}
            for e in repository.control_read(session).events
        )
        assert "PRIVATE_SCRIPT_CONTENT" not in tool.dispatch(
            "skill.propose_draft", {"files": {"bad": "PRIVATE_SCRIPT_CONTENT"}}
        )
        assert "Could not confirm" in tool.dispatch("skill.propose_sharing", {**body, "session_id": session})
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"})
        )
        with current.using(stranger), pytest.raises(HeinzelError, match="unavailable"):
            tool.dispatch("skill.propose_sharing", body)
