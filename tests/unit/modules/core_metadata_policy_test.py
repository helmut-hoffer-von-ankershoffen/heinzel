"""Actual loaded core registry only; no network, provider, or account access."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from heinzel import CurrentUser, HeinzelError, Module
from heinzel.kernel._exposure import exposed
from heinzel.modules.heinzel._core_metadata_policy import _modules_result, core_metadata_catalog
from heinzel.modules.heinzel._host_tools import _MAX_RESULT
from heinzel.modules.heinzel._model_grants import ModelGrant, ModelGrants
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._tool_review import ToolReviewFacade
from heinzel.modules.system._metadata_identity import CoreMetadataIdentity
from heinzel.modules.system._service import SystemService

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("count", [12, 240])
def test_large_installed_module_catalog_stays_discoverable_within_chat_budget(
    make_kernel, tmp_path, count, record_testsuite_property
):
    class Calendar:
        def events(self, window):
            pytest.fail("Metadata discovery must not read a provider")

        def free_busy(self, people, window):
            pytest.fail("Metadata discovery must not read a provider")

    installed = {}
    for index in range(count):
        module = Module(f"fixture{index}", "Fictional café service. " * 200)
        module.provides("calendar", modes={"read"})(Calendar)
        installed[module.name] = module
    kernel = make_kernel(builtins=True, **installed)
    service = kernel.resolve_builtin_service(SystemService, "system")
    ordinary = [row.model_dump(mode="json") for row in service.modules()]
    ordinary_bytes = len(json.dumps(ordinary, ensure_ascii=False, separators=(",", ":")).encode())
    assert ordinary_bytes > 16_000
    # Suite properties support xUnit2; include the catalog size to identify each case.
    record_testsuite_property(f"administrative_result_bytes[{count}]", ordinary_bytes)
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1))
        session = crud.new_chat("first").session_id
        catalog = core_metadata_catalog(kernel, current, crud, session)
        preview = catalog.preview()[0]
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
            allowed_tools={preview.identity: preview.binding_sha256},
        )
        grants = ModelGrants(crud)
        grants.propose(session, grant)
        grants.approve(session, "grant", grants.issue(session, "grant", "fixture-human-review"))
        with repo.main_writer(session) as writer:
            text = catalog.invoke(grant, "system:modules", {}, turn_writer=writer)
            assert len(text.encode("utf-8")) <= 16_000
            result = json.loads(text)
            record_testsuite_property(f"chat_result_bytes[{count}]", len(text.encode("utf-8")))
            record_testsuite_property(f"omitted_modules[{count}]", result["omitted_modules"])
            assert result["total_modules"] == len(ordinary)
            assert len(result["modules"]) + result["omitted_modules"] == len(ordinary)
            assert result["full_listing"] == "hz system modules --json"
            actual = {row["qualname"]: row["lets"] for row in result["modules"]}
            assert actual.items() <= {row["qualname"]: row["lets"] for row in ordinary}.items()
            if count == 12:
                assert result["omitted_modules"] == 0
                assert actual["fixture11"]["ProviderLet"] == ["fixture11:calendar"]
            else:
                assert result["omitted_modules"] > 0
            with pytest.raises(HeinzelError):
                catalog.invoke(grant, "system:modules", {"limit": 1}, turn_writer=writer)
            with pytest.raises(HeinzelError):
                catalog.invoke(grant, "fixture0:events", {}, turn_writer=writer)
    assert [row.model_dump(mode="json") for row in service.modules()] == ordinary


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
            rows = [
                row.model_dump(mode="json") for row in kernel.resolve_builtin_service(SystemService, "system").modules()
            ]
            full = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
            # The real core catalog may outgrow the chat limit; then the bounded projection is the exact result.
            expected = full if len(full.encode()) <= _MAX_RESULT else _modules_result(full, _MAX_RESULT)
            assert result == expected
            assert len(result.encode()) <= _MAX_RESULT
            listed = json.loads(result)
            assert any(
                item["qualname"] == "system" for item in (listed if isinstance(listed, list) else listed["modules"])
            )
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


def test_untrusted_rebound_verifier_cannot_authorize_core_catalog(make_kernel, tmp_path):
    from heinzel.kernel import CoreMetadataVerifier

    kernel = make_kernel(builtins=True)
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=1)
        )
        session = crud.new_chat("bound").session_id
        kernel.bind(CoreMetadataVerifier, CoreMetadataIdentity(kernel))
        with pytest.raises(HeinzelError, match="instance changed"):
            core_metadata_catalog(kernel, current, crud, session)


def test_same_type_rebound_core_service_does_not_pass_provenance(make_kernel):
    kernel = make_kernel(builtins=True)
    identity = CoreMetadataIdentity(kernel)
    item = next(
        item for item in exposed(kernel, "mcp") if item.record.qualname == "system" and item.operation.name == "modules"
    )
    assert identity(item)
    kernel.bind(SystemService, SystemService(kernel))
    with pytest.raises(HeinzelError, match="registration or source changed"):
        identity(item)
