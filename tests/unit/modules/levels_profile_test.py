"""A Heinzel's level on its profile, its two operations, and a turn's model chosen by it (ADR 0158)."""

import asyncio
from dataclasses import dataclass

import pytest

from heinzel import CurrentUser, HeinzelError, ModelOffer, UsageEntry
from heinzel.kernel import OperationLet
from heinzel.modules.heinzel._level_operations import level_view
from heinzel.modules.heinzel._levels import HeinzelIntelligence, LevelChange, changed
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests.unit.modules.user_chat_plugin_test import Models, Plugin, Settings
from tests.unit.modules.user_chat_test import setup_chat

pytestmark = pytest.mark.unit


@dataclass
class HousePlugin(Plugin):
    """A plugin with three models, the default first; it marks Low and High, not Medium."""

    def models(self) -> list[ModelOffer]:
        return [
            ModelOffer(id="house-default", thinking="adaptive"),
            ModelOffer(id="house-small", thinking="adaptive", level="low"),
            ModelOffer(id="house-big", thinking="budget", efforts=["low", "medium", "high"], level="high"),
        ]


def chat_service(crud, current, settings: Settings, plugin: Plugin | None = None):
    return UserChatService(
        crud,
        current,
        RunnerSnapshotService(),
        load=lambda: settings,  # type: ignore[arg-type, return-value]
        models=Models(plugin or HousePlugin()),
        usage=list[UsageEntry]().append,
    )


def set_level(crud, session, request, **fields):
    _current, revision = crud.intelligence(session)
    return crud.profile(session, request, revision, level=LevelChange(**fields))


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
        set_level(crud, session, "lvl-1", level="high", provider="acme", model="house-big")
        crud.profile(session, "name-1", 2, name="Helper")
        intelligence, revision = crud.intelligence(session)
        body = stored_profile(crud, session)
    assert intelligence == HeinzelIntelligence(level="high", provider="acme", model="house-big")
    assert revision == 3
    assert body["intelligence"] == {"level": "high", "provider": "acme", "model": "house-big"}
    assert body["name"] == "Helper"


def test_setting_the_level_is_idempotent_and_checks_the_revision(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        _current, crud, session = setup_chat(current)
        wanted = LevelChange(level="low")
        crud.profile(session, "lvl-1", 1, level=wanted)
        crud.profile(session, "lvl-1", 1, level=wanted)
        assert crud.intelligence(session) == (HeinzelIntelligence(level="low"), 2)
        crud.profile(session, "lvl-2", 2, level=LevelChange(provider="acme"))
        crud.profile(session, "lvl-1", 1, level=wanted)  # an exact retry replays after a later change
        assert crud.intelligence(session) == (HeinzelIntelligence(level="low", provider="acme"), 3)
        with pytest.raises(HeinzelError, match="already identifies a different profile update"):
            crud.profile(session, "lvl-1", 1, level=LevelChange(level="high"))
        with pytest.raises(HeinzelError, match="reload before setting its level"):
            crud.profile(session, "lvl-3", 1, level=LevelChange(level="high"))
        with pytest.raises(HeinzelError, match="give a level"):
            crud.profile(session, "lvl-4", 3, level=LevelChange())
        with pytest.raises(HeinzelError, match="reload before saving your appearance"):
            crud.profile(session, "name-2", 1, name="Stale")


def test_changed_sets_clears_and_refuses():
    current = HeinzelIntelligence(level="high", provider="acme", model="house-big")
    assert changed(current, None, "", "") == HeinzelIntelligence(level="high")
    assert changed(current, "low", None, None) == current.model_copy(update={"level": "low"})
    with pytest.raises(HeinzelError, match="Couldn't set this Heinzel's level: give a level"):
        changed(current, None, None, None)
    with pytest.raises(HeinzelError, match="Couldn't set this Heinzel's level: the provider or model"):
        changed(current, None, "Not A Name", None)


@pytest.mark.parametrize(
    ("level", "model", "effort", "ran", "source"),
    [
        ("low", "house-small", "low", "low", "declared"),
        ("high", "house-big", "high", "high", "declared"),
        ("medium", "house-small", "low", "low", "nearest"),
        ("auto", "house-small", "low", "low", "nearest"),
    ],
)
def test_a_turn_runs_on_the_model_the_provider_marks_and_records_it(tmp_path, level, model, effort, ran, source):  # noqa: PLR0913, PLR0917 - one row of the table
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        if level != "auto":
            set_level(crud, session, "lvl-1", level=level)
        chat = chat_service(crud, current, Settings(model="house-default", effort="medium"))
        replied = asyncio.run(chat.answer(session, "hello"))
    binding = replied.message.backend_binding
    assert (binding.backend, binding.model, binding.effort) == ("acme", model, effort)
    assert binding.level is not None
    assert (binding.level.asked, binding.level.ran, binding.level.source) == (level, ran, source)


def test_a_plugin_that_marks_no_level_runs_its_pinned_default_and_records_the_level(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        set_level(crud, session, "lvl-1", level="high")
        chat = chat_service(crud, current, Settings(model="slow", effort="medium"), Plugin())
        replied = asyncio.run(chat.answer(session, "hello"))
    binding = replied.message.backend_binding
    assert (binding.model, binding.effort) == ("slow", "medium")
    assert binding.level is not None
    assert (binding.level.asked, binding.level.source) == ("high", "provider-default")


def test_the_heinzels_provider_overrides_the_configured_access(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        set_level(crud, session, "lvl-1", provider="acme", level="high")
        chat = chat_service(crud, current, Settings(access="claude-code"))
        plugin = chat.plugin_selected(session)
        set_level(crud, session, "lvl-2", provider="elsewhere")
        with pytest.raises(HeinzelError, match="its AI provider elsewhere is neither the configured AI access"):
            chat.plugin_selected(session)
    assert plugin is not None
    assert plugin.level is not None
    assert (plugin.source.name, plugin.offer.id, plugin.level.ran) == ("acme", "house-big", "high")


def test_a_level_change_during_a_turn_is_seen_without_calling_the_plugin(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = chat_service(crud, current, Settings())
        plugin = chat.plugin_selected(session)
        assert plugin is not None
        assert chat._plugin_held(session) == plugin.held()
        set_level(crud, session, "lvl-1", level="low")
        assert chat._plugin_held(session) != plugin.held()
    assert chat._plugin_held() == chat.plugin_selected().held()  # no Heinzel: as Settings say


def test_claude_code_runs_its_levels_alias_and_refuses_an_exact_model(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = chat_service(crud, current, Settings(access="claude-code"))
        choice = chat.claude_level(session)
        set_level(crud, session, "lvl-1", model="claude-x")
        with pytest.raises(HeinzelError, match="lists no models"):
            chat.claude_level(session)
    assert choice is not None
    assert (choice.model, choice.effort) == ("sonnet", None)
    assert (choice.record.asked, choice.record.ran, choice.record.rule, choice.record.source) == (
        "auto",
        "medium",
        "auto-fixed-medium",
        "declared",
    )


def test_the_level_view_previews_the_turn_and_says_why_it_cant_run(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        plugin_chat = chat_service(crud, current, Settings())
        claude_chat = chat_service(crud, current, Settings(access="claude-code"))
        shown, recorded = level_view(plugin_chat, crud, session), level_view(claude_chat, crud, session)
        set_level(crud, session, "lvl-1", model="missing")
        refused, claude = level_view(plugin_chat, crud, session), level_view(claude_chat, crud, session)
    assert shown.runs_as is not None
    assert (shown.runs_as.provider, shown.runs_as.model, shown.runs_as.effort) == ("acme", "house-small", "low")
    assert (shown.level, shown.profile_revision, shown.problem) == ("auto", 1, None)
    assert recorded.runs_as is not None
    assert (recorded.runs_as.provider, recorded.runs_as.model, recorded.runs_as.level.source) == (
        None,
        "sonnet",
        "declared",
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
        chat = chat_service(crud, current, Settings())
        before = operations["level"](chat, crud, session)
        after = operations["set-level"](chat, crud, session, "lvl-1", 1, level="high", provider="acme")
        again = operations["set-level"](chat, crud, session, "lvl-1", 1, level="high", provider="acme")
    assert (before.level, before.profile_revision) == ("auto", 1)
    assert (after.level, after.provider, after.profile_revision) == ("high", "acme", 2)
    assert after.runs_as is not None
    assert (after.runs_as.model, after.runs_as.effort) == ("house-big", "high")
    assert again == after
