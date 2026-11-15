"""The access service's listing, switch and doctor check in each state the bindings can be in (ADR 0057, 0159)."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rich.console import Console

from heinzel import AccessStatus, HeinzelError, Status
from heinzel.modules.ai.access import render, render_test
from heinzel.modules.ai.access._models import AccessTestReport, Bindings
from heinzel.modules.ai.access._service import AccessService, setup_of

pytestmark = pytest.mark.unit


def adapter(name: str) -> SimpleNamespace:
    let = SimpleNamespace(capability="ai-access", adapter=name)
    return SimpleNamespace(let=let, module="ai.access", distribution="heinzel")


class Option:
    def __init__(self, status: AccessStatus | Exception) -> None:
        self.answer = status

    def status(self) -> AccessStatus:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def service(monkeypatch, tmp_path, options: dict[str, Option], bound: Bindings, runners=frozenset[str]()):
    view = Mock(adapters=[adapter(name) for name in options])
    view.hosts.return_value = ((), None)
    view.installed.return_value = runners
    editor = Mock()
    monkeypatch.setattr(AccessService, "bindings", staticmethod(lambda: bound))
    found = AccessService(options, view, editor, Mock(state_dir=tmp_path), Mock())  # type: ignore[arg-type]
    return found, editor


def plain(**fields: object) -> AccessStatus:
    return AccessStatus(**{"title": "Acme", "summary": "", "installed": True, "signed_in": True} | fields)


def text(renderable: object) -> str:
    console = Console(width=200, record=True)
    console.print(renderable)
    return console.export_text()


def test_nothing_chosen_or_an_unknown_choice_says_what_to_run(monkeypatch, tmp_path):
    found, _ = service(monkeypatch, tmp_path, {"acme": Option(plain())}, Bindings(ai_access=None))
    listing = found.listing()
    assert (listing.note, listing.next) == (
        "No AI access is chosen, so nothing reaches AI.",
        ["hz ai access use claude-code"],
    )
    assert found.checks()[0].status is Status.UP
    with pytest.raises(HeinzelError, match="No AI access is chosen; name one to test"):
        found.test(None, {})  # type: ignore[arg-type]
    found, _ = service(monkeypatch, tmp_path, {"acme": Option(plain())}, Bindings(ai_access="gone"))
    listing = found.listing()
    assert listing.note == "No installed option is called 'gone'; install its plugin or pick another."
    assert listing.next == ["hz ai access use acme"]
    check = found.checks()[0]
    assert (check.status, check.fix) == (Status.DEGRADED, "hz ai access use acme")
    shown = text(render(listing))
    assert "acme" in shown
    assert "Next:\n  hz ai access use acme" in shown


def test_a_broken_status_lists_the_option_and_binds_no_runner(monkeypatch, tmp_path):
    found, editor = service(monkeypatch, tmp_path, {"acme": Option(RuntimeError("down"))}, Bindings(ai_access="acme"))
    listing = found.use("acme")
    assert listing.options[0].status == "error"
    assert listing.note is not None
    assert listing.note.startswith("acme's status couldn't be read, so no agent runner was bound")
    editor.write.assert_called_once_with("capabilities.ai_access", "acme", "user")


def test_an_option_without_a_runner_or_with_one_not_installed_says_so(monkeypatch, tmp_path):
    found, _ = service(monkeypatch, tmp_path, {"acme": Option(plain())}, Bindings(ai_access="acme"))
    assert found.use("acme").note == "acme has no agent of its own; the chat keeps its agent runner."
    found, _ = service(monkeypatch, tmp_path, {"acme": Option(plain(runner="acme-cli"))}, Bindings(ai_access="acme"))
    note = found.use("acme").note
    assert note is not None
    assert note.startswith("acme names the agent runner acme-cli, which no installed module provides.")


def test_an_environment_choice_wins_and_says_how_to_undo_it(monkeypatch, tmp_path):
    options = {"acme": Option(plain()), "other": Option(plain())}
    found, _ = service(monkeypatch, tmp_path, options, Bindings(ai_access="other"))
    listing = found.use("acme")
    assert listing.next == ["unset HZ_CAPABILITIES__AI_ACCESS"]


def test_a_plugins_models_pin_the_first_usable_one_and_say_where_coding_runs(monkeypatch, tmp_path):
    models = plain(core_engine=True, runner="engine", models=["down", "up"], unavailable_models=["down"])
    found, editor = service(
        monkeypatch, tmp_path, {"acme": Option(models)}, Bindings(ai_access="acme", agent_runner="claude-code")
    )
    note = found.use("acme").note
    editor.write.assert_any_call("ai.access.plugin_model", "up", "user")
    assert note is not None
    assert note.startswith("Chat, drafts and schedules use Acme; coding handoffs still run on the agent runner")
    found, editor = service(
        monkeypatch, tmp_path, {"acme": Option(models)}, Bindings(ai_access="acme", agent_runner=None)
    )
    assert found.use("acme").note is None
    assert setup_of("acme", plain(core_engine=True, installed=False)) == (None, "Acme offers no models")


def test_a_single_check_shows_what_it_answered_cost_and_fix():
    answered = AccessTestReport(name="acme", ok=True, detail="pong", model="big", cost_usd=0.01, duration_seconds=1.25)
    assert text(render_test(answered)).splitlines() == ["acme: answered (big) in 1.2s, $0.0100", "pong"]
    failed = AccessTestReport(name="acme", ok=False, detail="refused", fix="acme login")
    assert text(render_test(failed)).splitlines() == ["acme: failed in 0.0s", "refused", "Fix: acme login"]
