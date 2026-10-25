"""`system.ui` runs each contribution in isolation and builds what only the server may say (ADR 0056)."""

from typing import Annotated

import pytest
from pydantic import Field

from heinzel import (
    CardWidget,
    HeinzelError,
    ListEntry,
    ListWidget,
    MetricWidget,
    Module,
    Navigate,
    RunCommand,
    RunOperation,
    Status,
    StatusItem,
    WidgetAction,
)
from heinzel.kernel import user_config_dir
from heinzel.modules.system.ui import UiService
from heinzel.modules.system.ui import contributions as health

pytestmark = pytest.mark.unit


def acme() -> Module:  # noqa: C901 - one test module with every kind of source and contribution
    """A module with sources of every kind, good and bad, and contributions over them."""
    module = Module("acme", "Acme reviews.")

    @module.operation
    def report() -> dict[str, int]:
        """Count the open reviews."""
        return {"open": 3}

    @module.operation(read_only=False)
    def sync(dry_run: Annotated[bool, Field(description="Only show.")] = False) -> str:
        """Sync the reviews."""
        return "synced"

    @module.operation
    def secret() -> str:
        """Fail with a secret in the message."""
        msg = "token=hunter2 leaked"
        raise RuntimeError(msg)

    @module.operation
    def broken() -> str:
        """Fail the way adapters report problems."""
        msg = "Acme is down\u202e"
        raise HeinzelError(msg)

    @module.operation(heads={"cli"})
    def private() -> str:
        """Only for the terminal."""
        return "private"

    @module.operation
    def lookup(key: Annotated[str, Field(description="What to look up.")]) -> str:
        """Look something up."""
        return key

    @module.status(source="report", order=10)
    def reviews(result: dict[str, int]) -> StatusItem:
        return StatusItem(text=f"{result['open']} reviews", effect=RunOperation(operation="sync"))

    @module.status(source="secret")
    def leaky(result: str) -> StatusItem:
        return StatusItem(text=result)

    @module.status(source="broken", order=900, slots={"footer": True, "sidebar": False})
    def down(result: str) -> StatusItem:
        return StatusItem(text=result)

    @module.widget(source="report")
    def wrong_type(result: dict[str, int]) -> str:
        return "not a widget"

    @module.widget(source="sync")
    def writes(result: str) -> None:
        return None

    @module.widget(source="private", name="private-source")
    def private_source(result: str) -> None:
        return None

    @module.widget(source="lookup")
    def needs_argument(result: str) -> None:
        return None

    module.action(
        "sync-now",
        label="Sync reviews",
        icon="zap",
        effect=RunOperation(operation="sync", arguments={"dry_run": True}),
        source="report",
        badge=lambda result: result["open"],
    )
    module.action("gone", label="Gone", icon="zap", effect=RunOperation(operation="nothing"))
    return module


@pytest.fixture
def service(make_kernel) -> UiService:
    kernel = make_kernel(builtins=True, acme=acme())
    return kernel.resolve(UiService)


def test_items_are_sorted_by_order_and_each_failure_stays_its_own(service):
    entries = [e for e in service.status() if e.module == "acme"]
    assert [e.id for e in entries] == ["acme:reviews", "acme:leaky", "acme:down"]
    reviews, leaky, down = entries
    assert reviews.item is not None
    assert reviews.item.text == "3 reviews"
    assert reviews.problem is None
    assert (leaky.item, leaky.problem) == (None, "acme:leaky failed: RuntimeError")  # never the exception's text
    assert down.problem == "acme:down: Acme is down"  # a HeinzelError's message, cleaned
    assert (reviews.refresh, down.slots) == (60, {"footer": True, "sidebar": False})


def test_an_operation_effect_carries_the_call_the_server_built(service):
    (reviews,) = service.status("acme:reviews")
    assert reviews.item is not None
    effect = reviews.item.effect
    assert isinstance(effect, RunOperation)
    assert effect.call is not None
    assert effect.call.model_dump() == {
        "method": "POST",
        "path": "/acme/sync",
        "query": {},
        "body": None,
        "command": "hz acme sync",
        "summary": "Sync the reviews.",
        "read_only": False,
    }


def test_actions_get_their_badge_and_call_or_a_problem(service):
    gone, sync = [e for e in service.actions() if e.module == "acme"]  # same order: by id
    assert (sync.label, sync.icon, sync.badge, sync.slots) == ("Sync reviews", "zap", 3, {"header": True})
    assert isinstance(sync.effect, RunOperation)
    assert sync.effect.call is not None
    assert sync.effect.call.command == "hz acme sync --dry-run true"
    assert sync.effect.call.query == {"dry_run": True}
    assert (gone.effect, gone.problem) == (None, "module acme has no operation `nothing`")


def test_widgets_that_cant_work_say_why(service):
    problems = {e.id: e.problem for e in service.widgets() if e.module == "acme"}
    assert problems["acme:wrong-type"] is not None
    assert "returned something the app can't show" in problems["acme:wrong-type"]
    assert (
        problems["acme:writes"] == "acme:writes: its source `sync` writes; a contribution reads a read-only operation"
    )
    assert "isn't exposed on the web head" in (problems["acme:private-source"] or "")
    assert "takes a required argument" in (problems["acme:needs-argument"] or "")


def test_doctor_names_declarations_that_cant_work_without_running_them(service, make_kernel):
    checks = make_kernel(builtins=True, acme=acme()).call(health)
    degraded = {check.name: check.detail for check in checks if check.status is Status.DEGRADED}
    assert set(degraded) == {
        "contribution acme:writes",
        "contribution acme:private-source",
        "contribution acme:needs-argument",
        "contribution acme:gone",
    }


def test_doctor_is_up_when_every_contribution_can_work(make_kernel):
    checks = make_kernel(builtins=True).call(health)
    assert [check.status for check in checks] == [Status.UP]


def test_one_item_by_id_and_an_unknown_one_named(service):
    assert [e.id for e in service.status("acme:down")] == ["acme:down"]
    with pytest.raises(HeinzelError, match=r"No contribution 'acme:nope'; there are: .*acme:reviews"):
        service.status("acme:nope")


@pytest.mark.parametrize(
    ("command", "runs"),
    [
        ("hz acme report", True),
        ("hz acme sync", False),
        ("hz acme sync --dry-run true", False),
        ("hz acme private", True),  # the terminal is the CLI, which exposes it
        ("hz acme", False),
        ("hz nothing here", False),
        ("hz system daemon install", False),
        ("hz system daemon logs --lines 5", True),
    ],
)
def test_a_command_runs_as_typed_only_when_it_names_a_read_only_operation(service, command, runs):
    assert service.runs(command) is runs


def test_effects_inside_widgets_are_resolved_and_a_bad_one_fails_the_widget(service, make_kernel):
    module = Module("beta", "Beta.")

    @module.operation
    def data() -> int:
        """Return a number."""
        return 1

    @module.widget(source="data")
    def card(result: int) -> CardWidget:
        return CardWidget(
            title="Beta",
            actions=[
                WidgetAction(label="Open", effect=Navigate(path="/doctor")),
                WidgetAction(label="Data", effect=RunCommand(command="hz beta data")),
            ],
        )

    @module.widget(source="data")
    def listed(result: int) -> ListWidget:
        return ListWidget(title="Beta", items=[ListEntry(text="x", effect=RunOperation(operation="missing"))])

    @module.widget(source="data")
    def metric(result: int) -> MetricWidget:
        return MetricWidget(label="Beta", value=str(result))

    ui = make_kernel(builtins=True, beta=module).resolve(UiService)
    card_entry, listed_entry, metric_entry = [e for e in ui.widgets() if e.module == "beta"]
    assert isinstance(card_entry.widget, CardWidget)
    second = card_entry.widget.actions[1].effect
    assert isinstance(second, RunCommand)
    assert second.runs is True
    assert (listed_entry.widget, listed_entry.problem) == (None, "beta: module beta has no operation `missing`")
    assert isinstance(metric_entry.widget, MetricWidget)


def test_operation_arguments_must_fit_the_operation(make_kernel):
    module = Module("gamma", "Gamma.")

    @module.operation(read_only=False)
    def act(count: Annotated[int, Field(description="How many.", ge=1)]) -> int:
        """Act."""
        return count

    module.action("missing", label="x", icon="zap", effect=RunOperation(operation="act"))
    module.action("wrong", label="x", icon="zap", effect=RunOperation(operation="act", arguments={"count": 0}))
    ui = make_kernel(builtins=True, gamma=module).resolve(UiService)
    missing, wrong = [e for e in ui.actions() if e.module == "gamma"]
    assert missing.problem == "`act` needs count"
    assert (wrong.problem or "").startswith("`act` argument count: Input should be greater than or equal to 1")
    other = Module("delta", "Delta.")
    other.operation(act)
    other.action("extra", label="x", icon="zap", effect=RunOperation(operation="act", arguments={"count": 1, "x": 2}))
    (extra,) = [e for e in make_kernel(builtins=True, delta=other).resolve(UiService).actions() if e.module == "delta"]
    assert extra.problem == "`act` takes no argument x"


def test_show_and_hide_write_the_person_s_choice_per_slot(service):
    shown = {i.id: i.slots for i in service.show("acme:down", "sidebar", shown=True)}
    assert shown["acme:down"] == {"footer": True, "sidebar": True}
    hidden = {i.id: i.slots for i in service.show("acme:down", "footer", shown=False)}
    assert hidden["acme:down"] == {"footer": False, "sidebar": True}
    assert service.chosen()["acme:down"] == {"sidebar": True, "footer": False}
    (entry,) = service.status("acme:down")
    assert entry.slots == {"footer": False, "sidebar": True}
    single = {i.id: i.slots for i in service.show("acme:sync-now", None, shown=False)}  # its only slot
    assert single["acme:sync-now"] == {"header": False}
    info = next(i for i in service.listing() if i.id == "acme:sync-now")
    assert (info.kind, info.title, info.defaults) == ("action", "Sync reviews", {"header": True})


def test_show_refuses_an_unknown_id_or_slot(service):
    with pytest.raises(HeinzelError, match="No contribution 'acme:nope'"):
        service.show("acme:nope", None, shown=True)
    with pytest.raises(HeinzelError, match="shows in footer, sidebar; name one with --slot"):
        service.show("acme:down", None, shown=True)
    with pytest.raises(HeinzelError, match="shows in header"):
        service.show("acme:sync-now", "footer", shown=True)


def test_an_effect_that_crashes_is_its_entry_s_problem_only(service, monkeypatch):
    def crash(*args: object) -> None:
        msg = "secret detail"
        raise RuntimeError(msg)

    monkeypatch.setattr(UiService, "_call", crash)
    sync = next(e for e in service.actions() if e.id == "acme:sync-now")
    assert (sync.effect, sync.problem) == (None, "acme:sync-now failed: RuntimeError")
    reviews = next(e for e in service.status() if e.id == "acme:reviews")
    assert (reviews.item, reviews.problem) == (None, "acme failed: RuntimeError")
    assert next(e for e in service.status() if e.id == "acme:down").problem == "acme:down: Acme is down"


def test_a_toggle_writes_only_the_user_file_s_choices(service, monkeypatch):
    monkeypatch.setenv("HZ_SYSTEM_UI__CONTRIBUTIONS", '{"acme:reviews": {"footer": false}}')
    assert service.chosen()["acme:reviews"] == {"footer": False}
    service.show("acme:down", "sidebar", shown=True)
    monkeypatch.delenv("HZ_SYSTEM_UI__CONTRIBUTIONS")
    assert service.chosen() == {"acme:down": {"sidebar": True}}  # the variable's choice stayed out of the file


def test_a_broken_user_table_is_refused_not_overwritten(service):
    path = user_config_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[system.ui.contributions."acme:down"]\nsidebar = "maybe"\n', encoding="utf-8")
    with pytest.raises(HeinzelError, match="isn't valid; fix or remove it first"):
        service.show("acme:down", "footer", shown=False)
    assert "maybe" in path.read_text(encoding="utf-8")


def test_a_graphic_widget_is_data_with_a_size_and_a_dressed_avatar():
    from pydantic import ValidationError

    from heinzel import Avatar, GraphicWidget, MetricWidget

    graphic = GraphicWidget(label="Commute", value="12 min", avatar=Avatar(accessories=["scarf"]), size="wide")
    assert graphic.model_dump(mode="json")["avatar"] == {"accessories": ["scarf"]}
    assert MetricWidget(label="x", value="1").size is None
    with pytest.raises(ValidationError):
        GraphicWidget(label="Commute", value="12 min", size="huge")
    with pytest.raises(ValidationError):
        Avatar(accessories=["cape"])


def test_a_graphic_widget_renders_as_text():
    from heinzel import GraphicWidget
    from heinzel.modules.system.ui import render_widgets
    from heinzel.modules.system.ui._models import WidgetEntry

    entry = WidgetEntry(
        id="acme:w",
        module="acme",
        plugin=None,
        order=1,
        refresh=60,
        slots={"today": True},
        widget=GraphicWidget(label="Commute", value="12 min", caption="Light traffic"),
    )
    assert render_widgets([entry]) is not None
