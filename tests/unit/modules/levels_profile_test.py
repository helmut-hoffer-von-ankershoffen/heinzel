"""A Heinzel's level on its profile, its two operations, and a turn's model chosen by it (ADR 0158)."""

import asyncio
import json
from dataclasses import dataclass

import pytest

from heinzel import CurrentUser, HeinzelError, ModelOffer, UsageEntry
from heinzel.kernel import OperationLet
from heinzel.modules.heinzel._level_operations import changed, level_view
from heinzel.modules.heinzel._levels import HeinzelIntelligence
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests.unit.modules.user_chat_plugin_test import Models, Plugin, Settings
from tests.unit.modules.user_chat_test import setup_chat

pytestmark = pytest.mark.unit


@dataclass
class TieredPlugin(Plugin):
    """A plugin whose models carry tier words, the default first."""

    def models(self) -> list[ModelOffer]:
        return [
            ModelOffer(id="acme-sonnet", thinking="adaptive"),
            ModelOffer(id="acme-haiku", thinking="adaptive"),
            ModelOffer(id="acme-opus", thinking="budget", efforts=["low", "medium", "high"]),
        ]


def chat_service(crud, current, settings: Settings, *, one_chat: bool, plugin: Plugin | None = None):
    return UserChatService(
        crud,
        current,
        RunnerSnapshotService(),
        load=lambda: settings,  # type: ignore[arg-type, return-value]
        models=Models(plugin or TieredPlugin()),
        usage=list[UsageEntry]().append,
        levels=lambda: one_chat,
    )


def set_level(crud, session, request, **fields):
    current, revision = crud.intelligence(session)
    return crud.profile(session, request, revision, intelligence=changed(current, **fields))


def stored_profile(crud, session) -> dict:
    events = crud.repository.control_read(session).events
    event = next(e for e in reversed(events) if e.kind == "profile.updated")
    return crud.read_body(session, event)


def test_a_new_heinzel_stores_no_intelligence_and_reads_auto(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        _current, crud, session = setup_chat(current)
        body = stored_profile(crud, session)
        intelligence, revision = crud.intelligence(session)
    assert "intelligence" not in body
    assert (intelligence, revision) == (HeinzelIntelligence(), 1)


def test_the_level_is_sticky_and_survives_a_name_change(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        _current, crud, session = setup_chat(current)
        set_level(crud, session, "lvl-1", level="high", provider="acme", model="acme-opus")
        crud.profile(session, "name-1", 2, name="Helper")
        intelligence, revision = crud.intelligence(session)
        body = stored_profile(crud, session)
    assert intelligence == HeinzelIntelligence(level="high", provider="acme", model="acme-opus")
    assert revision == 3
    assert body["intelligence"] == {"level": "high", "provider": "acme", "model": "acme-opus"}
    assert body["name"] == "Helper"


def test_setting_the_level_is_idempotent_and_checks_the_revision(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        _current, crud, session = setup_chat(current)
        wanted = HeinzelIntelligence(level="low")
        crud.profile(session, "lvl-1", 1, intelligence=wanted)
        crud.profile(session, "lvl-1", 1, intelligence=wanted)
        assert crud.intelligence(session) == (wanted, 2)
        with pytest.raises(HeinzelError, match="already identifies a different profile update"):
            crud.profile(session, "lvl-1", 1, intelligence=HeinzelIntelligence(level="high"))
        with pytest.raises(HeinzelError, match="reload before setting its level"):
            crud.profile(session, "lvl-2", 1, intelligence=HeinzelIntelligence(level="high"))
        with pytest.raises(HeinzelError, match="reload before saving your appearance"):
            crud.profile(session, "name-2", 1, name="Stale")


def test_changed_sets_clears_and_refuses():
    current = HeinzelIntelligence(level="high", provider="acme", model="acme-opus")
    assert changed(current, None, "", "") == HeinzelIntelligence(level="high")
    assert changed(current, "low", None, None) == current.model_copy(update={"level": "low"})
    with pytest.raises(HeinzelError, match="Couldn't set this Heinzel's level: give a level"):
        changed(current, None, None, None)
    with pytest.raises(HeinzelError, match="Couldn't set this Heinzel's level: the provider or model"):
        changed(current, None, "Not A Name", None)


@pytest.mark.parametrize(("level", "model", "effort"), [("low", "acme-haiku", "low"), ("high", "acme-opus", "high")])
def test_with_one_chat_on_a_turn_runs_on_the_levels_model_and_records_it(tmp_path, level, model, effort):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        set_level(crud, session, "lvl-1", level=level)
        chat = chat_service(crud, current, Settings(model="acme-sonnet", effort="medium"), one_chat=True)
        replied = asyncio.run(chat.answer(session, "hello"))
    binding = replied.message.backend_binding
    assert (binding.backend, binding.model, binding.effort) == ("acme", model, effort)
    assert binding.level is not None
    assert (binding.level.asked, binding.level.ran, binding.level.source) == (level, level, "table")


def test_with_one_chat_off_the_configured_model_runs_and_nothing_is_recorded(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        set_level(crud, session, "lvl-1", level="high")
        chat = chat_service(crud, current, Settings(model="acme-sonnet", effort="medium"), one_chat=False)
        replied = asyncio.run(chat.answer(session, "hello"))
    binding = replied.message.backend_binding
    assert (binding.model, binding.effort, binding.level) == ("acme-sonnet", "medium", None)
    assert "level" not in json.loads(binding.model_dump_json(exclude_defaults=False))


def test_the_heinzels_provider_overrides_the_configured_access(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        set_level(crud, session, "lvl-1", provider="acme")
        chat = chat_service(crud, current, Settings(access="claude-code"), one_chat=True)
        plugin = chat.plugin_selected(session)
        set_level(crud, session, "lvl-2", provider="elsewhere")
        with pytest.raises(HeinzelError, match="its AI provider elsewhere is neither the configured AI access"):
            chat.plugin_selected(session)
    assert plugin is not None
    assert (plugin.source.name, plugin.offer.id, plugin.level.ran) == ("acme", "acme-sonnet", "medium")


def test_a_level_change_during_a_turn_is_seen_without_calling_the_plugin(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = chat_service(crud, current, Settings(), one_chat=True)
        plugin = chat.plugin_selected(session)
        assert plugin is not None
        assert chat._plugin_held(session) == plugin.held()
        set_level(crud, session, "lvl-1", level="low")
        assert chat._plugin_held(session) != plugin.held()


def test_claude_code_records_the_level_and_refuses_an_exact_model(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = chat_service(crud, current, Settings(access="claude-code"), one_chat=True)
        record = chat.claude_level(session)
        off = chat_service(crud, current, Settings(access="claude-code"), one_chat=False).claude_level(session)
        set_level(crud, session, "lvl-1", model="claude-x")
        with pytest.raises(HeinzelError, match="lists no models"):
            chat.claude_level(session)
    assert record is not None
    assert (record.asked, record.ran, record.rule, record.source) == (
        "auto",
        "medium",
        "auto-fixed-medium",
        "provider-default",
    )
    assert off is None


def test_the_level_view_previews_the_turn_and_says_why_it_cant_run(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        on = chat_service(crud, current, Settings(), one_chat=True)
        off = chat_service(crud, current, Settings(), one_chat=False)
        shown, hidden = level_view(on, crud, session), level_view(off, crud, session)
        claude_on = chat_service(crud, current, Settings(access="claude-code"), one_chat=True)
        recorded = level_view(claude_on, crud, session)
        set_level(crud, session, "lvl-1", model="missing")
        refused = level_view(on, crud, session)
        claude = level_view(chat_service(crud, current, Settings(access="claude-code"), one_chat=True), crud, session)
    assert shown.runs_as is not None
    assert (shown.runs_as.provider, shown.runs_as.model, shown.runs_as.effort) == ("acme", "acme-sonnet", "medium")
    assert (shown.level, shown.profile_revision, shown.problem) == ("auto", 1, None)
    assert (hidden.runs_as, hidden.problem) == (None, None)
    assert recorded.runs_as is not None
    assert (recorded.runs_as.provider, recorded.runs_as.model, recorded.runs_as.level.source) == (
        None,
        None,
        "provider-default",
    )
    assert refused.runs_as is None
    assert refused.problem is not None
    assert "Couldn't run this Heinzel on the exact model missing" in refused.problem
    assert claude.problem is not None
    assert "lists no models" in claude.problem


def test_the_operations_are_private_and_never_on_mcp(make_kernel):
    kernel = make_kernel(builtins=True)
    found = {
        operation.name: operation
        for record, operation in kernel.lets(OperationLet)
        if record.qualname == "heinzel" and operation.name in {"level", "set-level"}
    }
    assert set(found) == {"level", "set-level"}
    for operation in found.values():
        assert operation.private
        assert operation.heads == frozenset({"cli", "web", "api-private"})
    assert found["level"].read_only
    assert not found["set-level"].read_only


def test_the_operations_read_and_set_the_level(tmp_path, make_kernel):
    kernel = make_kernel(builtins=True)
    operations = {
        operation.name: operation.func
        for record, operation in kernel.lets(OperationLet)
        if record.qualname == "heinzel" and operation.name in {"level", "set-level"}
    }
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = chat_service(crud, current, Settings(), one_chat=True)
        before = operations["level"](chat, crud, session)
        after = operations["set-level"](chat, crud, session, "lvl-1", 1, level="high", provider="acme")
        again = operations["set-level"](chat, crud, session, "lvl-1", 1, level="high", provider="acme")
    assert (before.level, before.profile_revision) == ("auto", 1)
    assert (after.level, after.provider, after.profile_revision) == ("high", "acme", 2)
    assert after.runs_as is not None
    assert (after.runs_as.model, after.runs_as.effort) == ("acme-opus", "high")
    assert again == after
