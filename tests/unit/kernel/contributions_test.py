"""Contributions are data the kernel checks (ADR 0056): links, texts, shapes, effects, slots and caps per module."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import TypeAdapter, ValidationError

from heinzel import (
    ActionLet,
    CardWidget,
    DetailLine,
    ListEntry,
    ListWidget,
    LiveClock,
    LiveUntil,
    MetricWidget,
    Module,
    Navigate,
    OpenUrl,
    ProgressWidget,
    RunCommand,
    RunOperation,
    StatusItem,
    StatusLet,
    WidgetAction,
    WidgetLet,
)
from heinzel.kernel import Badge, link_problem

pytestmark = pytest.mark.unit


def view(result: object) -> None:
    """A view that shows nothing."""


@pytest.mark.parametrize(
    "link",
    [
        "/",
        "/doctor",
        "/contracts/weekly-report",
        "/?settings=clock",
        "https://acme.example/pulls",
        "https://acme.example:8443/a?b=c#d",
        "https://meet.acme.example/j/123?pwd=x",
    ],
)
def test_https_links_and_in_app_paths_pass(link):
    assert link_problem(link) is None


@pytest.mark.parametrize(
    ("link", "why"),
    [
        ("//evil.example", "in-app path"),
        ("/\\evil.example", "in-app path"),
        ("/a/../b", "in-app path"),
        ("/a//b", "in-app path"),
        ("/Doctor", "in-app path"),
        ("/a?x=1&y=2", "in-app path"),
        ("/a\n", "in-app path"),
        ("http://acme.example", "https"),
        ("javascript:alert(1)", "https"),
        ("https://user:secret@acme.example/", "user information"),
        ("https://token@acme.example/", "user information"),
        ("https://acme.example/a b", "spaces"),
        ("https://acme.example/\u0000", "control"),
        ('https://acme.example/"onload=x', "quotes"),
        ("https://acme.example\\@evil.example", "backslashes"),
        ("https://", "valid host"),
        ("https://exa_mple.com", "valid host"),
        ("https://acme.example:0/", "valid host"),
        ("https://acme.example:99999/", "isn't a link"),
        ("https://" + "a" * 3000 + ".example", "too long"),
    ],
)
def test_other_links_are_refused_with_the_reason(link, why):
    assert why in (link_problem(link) or "")


def test_navigate_takes_paths_and_open_takes_https_only():
    assert Navigate(path="/doctor").path == "/doctor"
    with pytest.raises(ValidationError, match="in-app path"):
        Navigate(path="https://acme.example")
    with pytest.raises(ValidationError, match="open it with `navigate`"):
        OpenUrl(url="/doctor")
    with pytest.raises(ValidationError, match="user information"):
        OpenUrl(url="https://a:b@acme.example")


@pytest.mark.parametrize(
    "command",
    ["hz clock", "hz system daemon logs --lines 20", "hz system config set clock.zones '[\"Asia/Tokyo\"]'"],
)
def test_commands_are_hz_with_plain_or_single_quoted_words(command):
    assert RunCommand(command=command).command == command
    assert RunCommand(command=command).runs is False  # only system.ui says it runs


@pytest.mark.parametrize(
    "command",
    [
        "hz",
        "rm -rf /",
        "hz clock; rm -rf /",
        "hz $(whoami)",
        "hz clock && ls",
        "hz `id`",
        "hz a\nb",
        "hz clock '\x03curl x|sh\r'",
        "hz clock '\x1b[A'",
        "hz clock 'a\nb'",
        "hz clock '\x9b'",
    ],
)
def test_other_commands_are_refused(command):
    with pytest.raises(ValidationError, match="isn't an `hz` command"):
        RunCommand(command=command)


def test_text_is_cleaned_to_one_line_and_limited():
    item = StatusItem(text="  Stand\u202eup\n until \u200b ")
    assert item.text == "Standup until"
    with pytest.raises(ValidationError, match="at most 80"):
        StatusItem(text="x" * 81)
    with pytest.raises(ValidationError, match="at least 1"):
        StatusItem(text="\u200b")
    card = CardWidget(title="Review", body="line one\n\n  line   two  ", icon="cloud-rain")
    assert card.body == "line one\n\nline two"
    assert card.icon == "cloud-rain"


def test_unknown_keys_icons_and_tones_are_refused():
    with pytest.raises(ValidationError, match="Extra inputs"):
        StatusItem.model_validate({"text": "x", "html": "<b>x</b>"})
    with pytest.raises(ValidationError, match="icon"):
        StatusItem.model_validate({"text": "x", "icon": "skull"})
    with pytest.raises(ValidationError, match="tone"):
        StatusItem.model_validate({"text": "x", "tone": "red"})


def test_a_model_built_without_checks_is_checked_again_inside_another():
    sneaky = OpenUrl.model_construct(kind="open", url="javascript:alert(1)")
    with pytest.raises(ValidationError, match="https"):
        StatusItem(text="x", effect=sneaky)


def test_live_times_need_a_real_zone_and_an_aware_moment():
    assert LiveClock(zone="Europe/Berlin").zone == "Europe/Berlin"
    with pytest.raises(ValidationError, match="IANA"):
        LiveClock(zone="Mars/Olympus")
    with pytest.raises(ValidationError, match="timezone"):
        LiveUntil(at=datetime(2026, 10, 24, 9, 0))  # noqa: DTZ001 - the naive moment under test
    assert LiveUntil(at=datetime(2026, 10, 24, 9, 0, tzinfo=UTC)).kind == "until"


def test_widget_shapes_have_limits():
    with pytest.raises(ValidationError, match="at most 8"):
        ListWidget(title="Reviews", items=[ListEntry(text=str(n)) for n in range(9)])
    with pytest.raises(ValidationError, match="at most 3"):
        CardWidget(title="x", actions=[WidgetAction(label="a", effect=Navigate(path="/"))] * 4)
    with pytest.raises(ValidationError, match="at most 64"):
        StatusItem(text="x", detail=[DetailLine(text="d")] * 65)
    with pytest.raises(ValidationError, match="at most 16"):
        MetricWidget(label="x", value="1" * 17)
    with pytest.raises(ValidationError, match="can't exceed total"):
        ProgressWidget(label="x", done=3, total=2)
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        ProgressWidget(label="x", done=0, total=0)


def test_operation_arguments_are_named_bounded_json_values():
    effect = RunOperation(operation="sync", arguments={"dry_run": True, "limit": 3, "who": "acme"})
    assert effect.call is None
    assert RunOperation(operation="sync", arguments={"nested": {"a": [1, None]}}).arguments == {
        "nested": {"a": [1, None]}
    }
    with pytest.raises(ValidationError):
        RunOperation(operation="sync", arguments={f"p{i}": i for i in range(33)})
    with pytest.raises(ValidationError):
        RunOperation(operation="sync", arguments={"Bad-Name": 1})
    with pytest.raises(ValidationError):
        RunOperation(operation="Sync")


def test_status_items_and_widgets_are_views_with_defaults():
    module = Module("acme", "Acme.")

    @module.status(source="report")
    def reviews(result: object) -> None:
        return None

    @module.widget(source="report", order=10)
    def _open_reviews(result: object) -> None:
        return None

    status, widget = module.lets
    assert isinstance(status, StatusLet)
    assert (status.name, status.source, status.order, status.refresh, dict(status.slots)) == (
        "reviews",
        "report",
        500,
        timedelta(seconds=60),
        {"footer": True},
    )
    assert isinstance(widget, WidgetLet)
    assert (widget.name, widget.order, widget.refresh, dict(widget.slots)) == (
        "open-reviews",
        10,
        timedelta(minutes=5),
        {"today": True},
    )


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"source": "Report"}, "operation name"),
        ({"source": "report", "name": "Bad"}, "must match"),
        ({"source": "report", "order": 1001}, "order must be"),
        ({"source": "report", "refresh": "1s"}, "refresh must be between 2 s"),
        ({"source": "report", "refresh": "2d"}, "refresh must be between"),
        ({"source": "report", "slots": {"today": True}}, "slots are footer, sidebar"),
        ({"source": "report", "slots": {"footer": "yes"}}, "each true or false"),
    ],
)
def test_bad_status_declarations_are_refused(options, message):
    with pytest.raises(ValueError, match=message):
        Module("acme", "Acme.").status(**options)(view)


def test_widgets_refresh_at_most_every_minute():
    with pytest.raises(ValueError, match="between 60 s"):
        Module("acme", "Acme.").widget(source="report", refresh="30s")(view)


def test_a_status_item_may_show_in_the_sidebar_too():
    module = Module("acme", "Acme.")
    module.status(source="report", slots={"footer": False, "sidebar": True})(view)
    (let,) = module.lets
    assert dict(let.slots) == {"footer": False, "sidebar": True}  # type: ignore[attr-defined]


def test_actions_check_their_data_and_need_a_source_with_a_badge():
    module = Module("acme", "Acme.")
    let = module.action("reviews", label=" Reviews\n", icon="inbox", effect=Navigate(path="/"))
    assert (let.label, let.source, let.badge, dict(let.slots)) == ("Reviews", None, None, {"header": True})
    with pytest.raises(ValueError, match="icon"):
        module.action("other", label="x", icon="skull", effect=Navigate(path="/"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="needs a source"):
        module.action("other", label="x", icon="inbox", effect=Navigate(path="/"), badge=len)
    with pytest.raises(ValueError, match="needs a source"):
        module.action("other", label="x", icon="inbox", effect=Navigate(path="/"), source="report")
    with pytest.raises(ValidationError, match="https"):
        ActionLet(
            name="sneaky", label="x", icon="inbox", effect=OpenUrl.model_construct(kind="open", url="http://x.example")
        )


def test_a_module_offers_a_few_of_each():
    module = Module("acme", "Acme.")
    for n in range(3):
        module.status(source="report", name=f"s{n}")(view)
    with pytest.raises(ValueError, match="offers 3 status contributions already"):
        module.status(source="report", name="s3")(view)
    for n in range(2):
        module.action(f"a{n}", label="x", icon="inbox", effect=Navigate(path="/"))
    with pytest.raises(ValueError, match="offers 2 action"):
        module.action("a2", label="x", icon="inbox", effect=Navigate(path="/"))
    for n in range(4):
        module.widget(source="report", name=f"w{n}")(view)
    with pytest.raises(ValueError, match="offers 4 widget"):
        module.widget(source="report", name="w4")(view)


def test_badges_are_counts_or_short_texts():
    badge = TypeAdapter(Badge)
    assert badge.validate_python(3) == 3
    assert badge.validate_python(" new ") == "new"
    for wrong in (True, -1, "12345", 1.5):
        with pytest.raises(ValidationError):
            badge.validate_python(wrong)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_operation_arguments_are_finite_json_scalars(value):
    with pytest.raises(ValidationError):
        RunOperation(operation="change", arguments={"limit": value})


@pytest.mark.parametrize("value", [float("nan"), {"nested": float("inf")}, [float("-inf")]])
def test_operation_arguments_refuse_nonfinite_nested_json(value):
    with pytest.raises(ValidationError, match="finite JSON"):
        RunOperation(operation="change", arguments={"value": value})


@pytest.mark.parametrize("value", ["x" * 16385, [[[[[[[[[[0]]]]]]]]]]])
def test_operation_arguments_refuse_unbounded_size_or_depth(value):
    with pytest.raises(ValidationError, match="at most"):
        RunOperation(operation="change", arguments={"value": value})
