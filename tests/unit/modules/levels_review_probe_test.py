"""The reviewer's probes for slice 14 (ADR 0158): smuggled aliases, bound schedules, retries and previews."""

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.kernel._claude_code import COMMAND_FLAGS, command_flags
from heinzel.modules.heinzel._autonomous_schedule import model_binding_fingerprint
from heinzel.modules.heinzel._level_operations import level_view
from tests.unit.modules.levels_profile_test import chat_service, set_level
from tests.unit.modules.user_chat_plugin_test import Settings
from tests.unit.modules.user_chat_test import setup_chat

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "smuggled",
    [
        "--dangerously-skip-permissions",
        "-p",
        "sonnet ",
        " sonnet",
        "sonnet=opus",
        "--model=opus",
        "Sonnet",
        "sonnet\n--x",
        "claude-opus-4-1",
        "",
        "opus\x00",
    ],
)
def test_probe_only_declared_aliases_pass(smuggled):
    with pytest.raises(HeinzelError):
        command_flags(smuggled)
    assert command_flags(None) == COMMAND_FLAGS


def test_probe_schedule_binding_sees_the_heinzels_level(tmp_path):
    """F1: what an approved schedule or draft binds to must change when the model its turns run on changes."""
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = chat_service(crud, current, Settings(model="house-default", effort="medium"))
        before_model = chat.plugin_selected(session).offer.id
        before = model_binding_fingerprint(chat, session)
        set_level(crud, session, "lvl-1", level="high")
        after_model = chat.plugin_selected(session).offer.id
        after = model_binding_fingerprint(chat, session)
    assert (before_model, after_model) == ("house-small", "house-big")
    assert before != after, "an approved schedule keeps running after its Heinzel's level moved it to another model"


def test_probe_set_level_retry_is_idempotent_after_a_later_change(tmp_path, make_kernel):
    """F3: an exact retry of set-level must replay, not refuse, once another field changed since."""
    from heinzel.kernel import OperationLet

    kernel = make_kernel(builtins=True)
    op = next(o.func for r, o in kernel.lets(OperationLet) if r.qualname == "heinzel" and o.name == "set-level")
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = chat_service(crud, current, Settings())
        first = op(chat, crud, session, "lvl-1", 1, level="high")
        op(chat, crud, session, "lvl-2", 2, provider="acme")
        retried = op(chat, crud, session, "lvl-1", 1, level="high")
    assert first.profile_revision == 2
    assert retried.level == "high"


def test_probe_preview_on_a_non_claude_access_names_no_claude_alias(tmp_path):
    """F5: with an access that is neither a plugin nor Claude Code, the preview must not claim a Claude alias."""
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = chat_service(crud, current, Settings(access="pydantic-ai"))
        view = level_view(chat, crud, session)
    assert view.runs_as is None or view.runs_as.model not in {"haiku", "sonnet", "opus"}, view
