"""Actual loaded core registry only; no network, provider, or account access."""

from dataclasses import replace
from pathlib import Path

import pytest

from heinzel import CurrentUser, HeinzelError, Module
from heinzel.kernel._exposure import exposed
from heinzel.modules.heinzel._core_metadata_policy import CoreMetadataIdentity, core_metadata_catalog
from heinzel.modules.heinzel._model_grants import ModelGrant, ModelGrants
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._tool_review import ToolReviewFacade
from heinzel.modules.system._service import SystemService

pytestmark = pytest.mark.unit


def test_real_core_review_and_locked_invocation_deny_other_tools(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1))
        session = crud.new_chat("first").session_id
        catalog = core_metadata_catalog(kernel, current, crud, session)
        page = ToolReviewFacade(catalog).review(session)
        assert len(page.items) == 1
        descriptor = page.items[0]
        assert descriptor.identity == "system:modules"
        assert descriptor.builtin
        assert descriptor.fixed_arguments == {}
        assert "no publisher signature verified" in descriptor.code_evidence
        assert len(descriptor.implementation_sha256) == 64
        grant = ModelGrant(
            request_id="grant",
            revision=1,
            access="pydantic-ai",
            model="openai:model",
            requests=1,
            total_tokens=1000,
            output_tokens=100,
            timeout_seconds=10,
            message_cap_usd="1",
            session_cap_usd="2",
            owner_cap_usd="3",
            allowed_tools={descriptor.identity: descriptor.binding_sha256},
        )
        grants = ModelGrants(crud)
        grants.propose(session, grant)
        grants.approve(session, "grant", grants.issue(session, "grant", "fixture-human-review"))
        with repo.main_writer(session) as writer:
            result = catalog.invoke(grant, "system:modules", {}, turn_writer=writer)
            assert '"qualname": "system"' in result
            with pytest.raises(HeinzelError):
                catalog.invoke(grant, "system:modules", {"account": "x"}, turn_writer=writer)
            with pytest.raises(HeinzelError):
                catalog.invoke(grant, "system.runner.chat:list", {}, turn_writer=writer)
        with pytest.raises(HeinzelError):
            catalog.invoke(grant, "system:modules", {}, turn_writer=writer)


def test_spoofed_plugin_and_changed_source_or_dependency_are_rejected(make_kernel, tmp_path, monkeypatch):
    kernel = make_kernel(builtins=True)
    identity = CoreMetadataIdentity(kernel)
    item = next(
        item for item in exposed(kernel, "mcp") if item.record.qualname == "system" and item.operation.name == "modules"
    )
    assert len(identity(item)) == 64
    fake = Module("system", "Spoof")
    spoof = replace(item, record=replace(item.record, module=fake))
    with pytest.raises(HeinzelError):
        identity(spoof)
    with monkeypatch.context() as patch:
        patch.setattr(SystemService, "modules", lambda _: [])
        with pytest.raises(HeinzelError):
            identity(item)
    original_read = Path.read_bytes
    with monkeypatch.context() as patch:

        def changed(path):
            body = original_read(path)
            return body + b"\n# changed" if path == identity._source_paths[0] else body

        patch.setattr(Path, "read_bytes", changed)
        with pytest.raises(HeinzelError):
            identity(item)
