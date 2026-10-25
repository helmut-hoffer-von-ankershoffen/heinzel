"""Contributions: status items, actions and widgets a module adds to the web app, as data (ADR 0056).

A contribution is a pure view over one of the module's own read-only operations: `system.ui` runs the operation the
way a head does (validation, injection, capability handles, `optional=True`) and hands its result to the view, which
returns one of the closed shapes below or None. The app renders the shapes itself; a plugin never ships code for it.
Every text is cleaned and length-limited, every link is https or an in-app path, and every effect is one of four kinds,
so what reaches the browser is data a strict model accepted.
"""

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self, get_args
from urllib.parse import urlsplit
from zoneinfo import available_timezones

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictFloat,
    StrictInt,
    model_validator,
)

from heinzel.kernel._lets import NAME_PATTERN, Let, host_of
from heinzel.kernel._text import clean_text

Tone = Literal["neutral", "ok", "warn", "bad"]
"""How a value reads: neutral, good (`ok`), needs attention (`warn`) or wrong (`bad`)."""
Icon = Literal[
    "activity",
    "alarm-clock",
    "bell",
    "bot",
    "calendar",
    "calendar-clock",
    "chart-line",
    "circle-alert",
    "circle-check",
    "clock",
    "cloud",
    "cloud-sun",
    "code",
    "file-text",
    "git-pull-request",
    "globe",
    "heart",
    "inbox",
    "info",
    "link",
    "list-checks",
    "mail",
    "message-square",
    "play",
    "rocket",
    "star",
    "terminal",
    "timer",
    "users",
    "video",
    "zap",
]
"""The icons a contribution may name; the web app maps each to its own icon, and only these."""
ICONS: frozenset[str] = frozenset(get_args(Icon))
Size = Literal["small", "medium", "wide"]
"""How much room a widget asks of the Today grid: one column, a roomier one, or two columns; the app may give less on a
narrow screen. Left out, each kind has its own default."""
Accessory = Literal["umbrella", "sunglasses", "scarf"]
"""What a Heinzelmännchen may wear or carry in a graphic widget; the web app draws each, and only these."""
Slot = Literal["footer", "sidebar", "header", "today"]
"""Where the web app shows a contribution: the status bar under the screen, the bottom of the navigation sidebar, the
header's buttons, or the Today screen."""
SLOTS: dict[str, tuple[Slot, ...]] = {"status": ("footer", "sidebar"), "action": ("header",), "widget": ("today",)}
"""The slots each kind of contribution may appear in."""
MAX_TEXT = 80
MAX_BODY = 280
MAX_VALUE = 16
MAX_BADGE = 4
MAX_DETAIL = 8
MAX_ACTIONS = 3
MAX_ITEM_ACTIONS = 4
MAX_ITEMS = 8
MAX_URL = 2048
MAX_PER_MODULE = {"status": 3, "action": 2, "widget": 4, "nav": 4}
"""How many contributions of each kind one module may offer."""
ORDER_MIN, ORDER_MAX, ORDER_DEFAULT = 0, 1000, 500
STATUS_REFRESH = (timedelta(seconds=15), timedelta(days=1))
"""The shortest and longest refresh of a status item or a badge."""
WIDGET_REFRESH = (timedelta(seconds=60), timedelta(days=1))
"""The shortest and longest refresh of a widget."""
_SEGMENT = r"[a-z0-9](?:-?[a-z0-9])*"
_QUERY = r"\?[a-z](?:-?[a-z0-9])*=[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?"
IN_APP_PATH = re.compile(rf"^/(?:{_SEGMENT}(?:/{_SEGMENT})*)?(?:{_QUERY})?$")
"""An in-app path: `/`, dash-separated lowercase segments, at most one `?name=value`; never `//`, `\\` or `..`."""
_UNSAFE = re.compile(r"[\s\\\"'<>`\x00-\x1f\x7f-\x9f]")
COMMAND = re.compile(r"^hz(?: +(?:[\w./:=@,+-]+|'(?:[^'\\\x00-\x1f\x7f-\x9f]|'\\'')*'))+$", re.ASCII)
"""An `hz` command the terminal panel runs as typed: plain words, or single-quoted arguments without control
characters, which a terminal would act on (Ctrl-C, Enter, escape sequences) even inside quotes."""
MAX_COMMAND = 300
MAX_ARGUMENT_DEPTH = 8
MAX_ARGUMENT_BYTES = 16_384
Scalar = (
    StrictBool
    | StrictInt
    | Annotated[StrictFloat, Field(allow_inf_nan=False)]
    | Annotated[str, Field(max_length=MAX_BODY)]
)


def _argument(value: JsonValue) -> JsonValue:
    """Bound nested fixed arguments before they reach a generic client or command preview.

    Raises:
        ValueError: If an argument exceeds the size/depth bounds or has a nonfinite JSON number.
    """

    def depth(item: JsonValue, level: int = 0) -> None:
        if level > MAX_ARGUMENT_DEPTH:
            msg = "Operation arguments may nest at most 8 levels"
            raise ValueError(msg)
        if isinstance(item, dict):
            for child in item.values():
                depth(child, level + 1)
        elif isinstance(item, list):
            for child in item:
                depth(child, level + 1)

    depth(value)
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":"), ensure_ascii=False).encode()
    except ValueError as exc:
        msg = "Operation arguments need finite JSON values"
        raise ValueError(msg) from exc
    if len(encoded) > MAX_ARGUMENT_BYTES:
        msg = "Operation arguments may contain at most 16 KiB of JSON per parameter"
        raise ValueError(msg)
    return value


JsonArgument = Annotated[JsonValue, AfterValidator(_argument)]


def _one_line(value: object) -> object:
    """A string cleaned for display on one line; anything else unchanged, for the model to refuse."""
    if isinstance(value, str):
        return " ".join(clean_text(value).split())
    return value


def _paragraph(value: object) -> object:
    """A string cleaned for display, keeping single newlines between lines."""
    if isinstance(value, str):
        return "\n".join(" ".join(line.split()) for line in clean_text(value).splitlines()).strip()
    return value


Text = Annotated[str, BeforeValidator(_one_line), Field(min_length=1, max_length=MAX_TEXT)]
"""One line of at most 80 characters, cleaned of control and invisible characters."""
Body = Annotated[str, BeforeValidator(_paragraph), Field(min_length=1, max_length=MAX_BODY)]
Value = Annotated[str, BeforeValidator(_one_line), Field(min_length=1, max_length=MAX_VALUE)]


def link_problem(value: str) -> str | None:
    """Why `value` is neither an https link nor an in-app path, or None."""
    if IN_APP_PATH.fullmatch(value):
        return None
    if value.startswith("/"):
        return f"{value!r} isn't an in-app path: `/` and lowercase, dash-separated segments, at most one `?name=value`"
    return _https_problem(value)


def _https_problem(value: str) -> str | None:
    if len(value) > MAX_URL or _UNSAFE.search(value):
        return f"{value[:60]!r}… isn't a link: too long, or with spaces, quotes, backslashes or control characters"
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return f"{value!r} isn't a link"
    if parts.scheme != "https":
        return f"{value!r} isn't an https link"
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return f"{value!r} carries user information; a link never holds credentials"
    host = parts.hostname or ""
    if not host or host_of(host) != host or port == 0:
        return f"{value!r} doesn't name a valid host"
    return None


def _link(value: str) -> str:
    if problem := link_problem(value):
        raise ValueError(problem)
    return value


def _url(value: str) -> str:
    if value.startswith("/"):
        msg = f"{value!r} is an in-app path; open it with `navigate`"
        raise ValueError(msg)
    if problem := link_problem(value):
        raise ValueError(problem)
    return value


def _path(value: str) -> str:
    if not IN_APP_PATH.fullmatch(value):
        raise ValueError(link_problem(value) or f"{value!r} isn't an in-app path")
    return value


def _zone(value: str) -> str:
    if value not in available_timezones():
        msg = f"{value!r} isn't an IANA time zone, e.g. Europe/Berlin"
        raise ValueError(msg)
    return value


def _command(value: str) -> str:
    if len(value) > MAX_COMMAND or not COMMAND.fullmatch(value):
        msg = f"{value[:60]!r} isn't an `hz` command of plain words or single-quoted arguments"
        raise ValueError(msg)
    return value


Link = Annotated[str, AfterValidator(_link)]
"""An https link without user information, or an in-app path such as `/doctor` or `/?settings=clock`."""


class _Data(BaseModel):
    """Frozen data that refuses unknown keys."""

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class Navigate(_Data):
    """Open a screen of the app."""

    kind: Literal["navigate"] = Field(default="navigate", description="Open a screen of the app.")
    path: Annotated[str, AfterValidator(_path)] = Field(description="An in-app path, e.g. `/doctor`.")


class OpenUrl(_Data):
    """Open an https link in a new tab."""

    kind: Literal["open"] = Field(default="open", description="Open an https link in a new tab.")
    url: Annotated[str, AfterValidator(_url)] = Field(description="An https link without user information.")


class OperationCall(_Data):
    """How the app runs an `operation` effect, built by `system.ui` from the operation itself, never by a plugin."""

    method: Literal["GET", "POST"] = Field(description="`GET` for a read-only operation, `POST` for one that writes.")
    path: str = Field(description="The operation's path under `/api`, e.g. `/clock`.")
    query: dict[str, Scalar] = Field(description="Validated query arguments; empty for private mutations.")
    body: dict[str, JsonArgument] | None = Field(
        default=None, description="Validated flat JSON arguments for a private mutation; never a query fallback."
    )
    command: str = Field(description="The same call as a person reads it, e.g. `hz clock`.")
    summary: str = Field(description="What the operation does: the first line of its docstring.")
    read_only: bool = Field(description="Whether it only reads; the app asks before running one that doesn't.")


class RunOperation(_Data):
    """Run an operation of the contributing module with fixed arguments; one that writes only after confirmation."""

    kind: Literal["operation"] = Field(default="operation", description="Run an operation of the module.")
    operation: str = Field(pattern=NAME_PATTERN.pattern, description="The operation's name in its module, e.g. `sync`.")
    arguments: dict[Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_]*$")], JsonArgument] = Field(
        default_factory=dict[str, JsonArgument],
        max_length=32,
        description="Fixed JSON arguments by parameter name; structured values require a private mutation.",
    )
    call: OperationCall | None = Field(
        default=None, description="Filled in by `system.ui`; whatever a plugin sets here is replaced."
    )


class RunCommand(_Data):
    """Open the terminal with an `hz` command."""

    kind: Literal["command"] = Field(default="command", description="Open the terminal with an `hz` command.")
    command: Annotated[str, AfterValidator(_command)] = Field(description="e.g. `hz clock`.")
    runs: bool = Field(
        default=False,
        description="Filled in by `system.ui`: whether it names a read-only operation, so the terminal may press "
        "Enter; otherwise the person does.",
    )


Effect = Annotated[Navigate | OpenUrl | RunOperation | RunCommand, Field(discriminator="kind")]
"""What a click does: exactly one of navigate, open, operation and command."""


class NavSettings(_Data):
    """The module-owned Settings sections a navigation entry groups under an existing heading."""

    sections: tuple[Annotated[str, Field(pattern=r"^[a-z](?:-?[a-z0-9])*(?:\.[a-z](?:-?[a-z0-9])*)*$")], ...] = Field(
        min_length=1, max_length=8, description="Qualified module prefixes owned by this provider."
    )
    group: Literal["preferences", "customize", "organisation", "computer"] = Field(
        default="preferences", description="The existing Settings heading under which the entry appears."
    )

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len(set(self.sections)) != len(self.sections):
            msg = "Navigation settings sections must be unique"
            raise ValueError(msg)
        return self


class _NavData(_Data):
    name: Annotated[str, Field(pattern=NAME_PATTERN.pattern, max_length=64)]
    title: Text
    icon: Icon
    order: Annotated[int, Field(ge=ORDER_MIN, le=ORDER_MAX, strict=True)] = ORDER_DEFAULT
    settings: NavSettings | None = None
    sidebar: Effect | None = None

    @model_validator(mode="after")
    def _target(self) -> Self:
        if self.settings is None and self.sidebar is None:
            msg = "A navigation entry needs Settings sections or a sidebar effect"
            raise ValueError(msg)
        return self


class WidgetAction(_Data):
    """A button of a widget."""

    label: Text = Field(description="What the button says.")
    icon: Icon | None = Field(default=None, description="An icon before the label.")
    effect: Effect = Field(description="What a click does.")


class LiveClock(_Data):
    """The time now in a zone, ticking in the app."""

    kind: Literal["clock"] = Field(default="clock", description="The time now in `zone`.")
    zone: Annotated[str, AfterValidator(_zone)] = Field(description="An IANA time zone, e.g. Europe/Berlin.")


class LiveTime(_Data):
    """A moment, shown as a local time in the person's locale."""

    kind: Literal["time"] = Field(default="time", description="`at` as a local time.")
    at: AwareDatetime = Field(description="The moment, with its offset.")


class LiveUntil(_Data):
    """A moment, shown relative to now: "in 12 min", "now", or its time when it isn't today."""

    kind: Literal["until"] = Field(default="until", description="`at` relative to now.")
    at: AwareDatetime = Field(description="The moment, with its offset.")


Live = Annotated[LiveClock | LiveTime | LiveUntil, Field(discriminator="kind")]


class DetailLine(_Data):
    """A line of an item's detail, shown on hover or focus."""

    text: Text = Field(description="One line, e.g. `New York 08:00, at work`.")
    tone: Tone = Field(default="neutral", description="`ok` shows it as good, `warn` and `bad` as needing attention.")


class StatusItem(_Data):
    """A short item of the status bar: text, an optional live time after it, detail on hover, a click."""

    text: Text = Field(description="What it says, one line, e.g. `Standup until`.")
    tone: Tone = Field(default="neutral", description="How it reads: neutral, ok, warn or bad.")
    icon: Icon | None = Field(default=None, description="An icon before the text.")
    live: Live | None = Field(default=None, description="A time the app renders after `text` and keeps current.")
    detail: list[DetailLine] = Field(
        default_factory=list[DetailLine], max_length=MAX_DETAIL, description="Up to eight lines shown on hover."
    )
    effect: Effect | None = Field(default=None, description="What a click does.")
    actions: list[WidgetAction] = Field(
        default_factory=list[WidgetAction],
        max_length=MAX_ITEM_ACTIONS,
        description="Up to four buttons shown with the detail, e.g. Install and Logs.",
    )


def _badge(value: object) -> object:
    if isinstance(value, bool):
        msg = "a badge is a count or a short text, not a boolean"
        raise ValueError(msg)  # noqa: TRY004 - pydantic reports ValueError
    if isinstance(value, int):
        if value < 0:
            msg = f"a badge counts from 0, not {value}"
            raise ValueError(msg)
        return value
    return _one_line(value)


Badge = Annotated[StrictInt | Annotated[str, Field(min_length=1, max_length=MAX_BADGE)], BeforeValidator(_badge)]
"""A count (0 shows none) or at most 4 characters."""


class CardWidget(_Data):
    """A card: a title, a short body and up to three buttons."""

    kind: Literal["card"] = Field(default="card", description="A card.")
    title: Text = Field(description="Its title, one line.")
    body: Body | None = Field(default=None, description="Up to 280 characters under the title.")
    tone: Tone = Field(default="neutral", description="How it reads: neutral, ok, warn or bad.")
    actions: list[WidgetAction] = Field(
        default_factory=list[WidgetAction], max_length=MAX_ACTIONS, description="Up to three buttons."
    )

    size: Size | None = Field(default=None, description="Room in the Today grid; each kind has a default.")


class ListEntry(_Data):
    """A line of a list widget."""

    text: Text = Field(description="The line, e.g. `Review the Acme roadmap`.")
    detail: Text | None = Field(default=None, description="A second, quieter line.")
    tone: Tone = Field(default="neutral", description="How it reads: neutral, ok, warn or bad.")
    effect: Effect | None = Field(default=None, description="What a click does.")


class ListWidget(_Data):
    """A titled list of up to eight lines."""

    kind: Literal["list"] = Field(default="list", description="A list.")
    title: Text = Field(description="Its title, one line.")
    items: list[ListEntry] = Field(max_length=MAX_ITEMS, description="Up to eight lines.")

    size: Size | None = Field(default=None, description="Room in the Today grid; each kind has a default.")


class MetricWidget(_Data):
    """One number with a label, and how it moved."""

    kind: Literal["metric"] = Field(default="metric", description="A metric.")
    label: Text = Field(description="What is measured, e.g. `Open reviews`.")
    value: Value = Field(description="The figure as shown, up to 16 characters, e.g. `12` or `2/3`.")
    delta: Value | None = Field(default=None, description="How it moved, as shown, e.g. `+3 this week`.")
    tone: Tone = Field(default="neutral", description="How it reads: neutral, ok, warn or bad.")

    size: Size | None = Field(default=None, description="Room in the Today grid; each kind has a default.")


class ProgressWidget(_Data):
    """How much of something is done."""

    kind: Literal["progress"] = Field(default="progress", description="Progress.")
    label: Text = Field(description="What is done, e.g. `Release checklist`.")
    done: int = Field(ge=0, description="How many are done, at most `total`.")
    total: int = Field(ge=1, description="How many there are.")
    tone: Tone = Field(default="neutral", description="How it reads: neutral, ok, warn or bad.")
    size: Size | None = Field(default=None, description="Room in the Today grid; each kind has a default.")

    @model_validator(mode="after")
    def _done_within_total(self) -> Self:
        if self.done > self.total:
            msg = f"done ({self.done}) can't exceed total ({self.total})"
            raise ValueError(msg)
        return self


class Avatar(_Data):
    """A Heinzelmännchen drawn in a widget, dressed by data only."""

    accessories: list[Accessory] = Field(
        default_factory=list[Accessory],
        max_length=3,
        description="What it wears or carries: umbrella, sunglasses, scarf.",
    )


class GraphicWidget(_Data):
    """A big value with a picture: an icon or a Heinzelmännchen, and a line under it."""

    kind: Literal["graphic"] = Field(default="graphic", description="A graphic.")
    label: Text = Field(description="What it shows, e.g. `Commute home`.")
    value: Value = Field(description="The big figure as shown, up to 16 characters, e.g. `12 min`.")
    caption: Text | None = Field(default=None, description="A line under the value, e.g. `Light traffic`.")
    icon: Icon | None = Field(default=None, description="An icon beside the value.")
    avatar: Avatar | None = Field(default=None, description="A Heinzelmännchen beside the value; wins over `icon`.")
    tone: Tone = Field(default="neutral", description="How it reads: neutral, ok, warn or bad.")
    size: Size | None = Field(default=None, description="Room in the Today grid; each kind has a default.")


Widget = Annotated[CardWidget | ListWidget | MetricWidget | ProgressWidget | GraphicWidget, Field(discriminator="kind")]
"""What a widget shows: a card, a list, a metric, progress or a graphic."""
View = Callable[[Any], Any]
"""A pure function of the source operation's result."""


def refresh_problem(refresh: timedelta, bounds: tuple[timedelta, timedelta]) -> str | None:
    """Why `refresh` is outside `bounds`, or None."""
    low, high = bounds
    if low <= refresh <= high:
        return None
    return f"refresh must be between {int(low.total_seconds())} s and {int(high.total_seconds())} s, not {refresh}"


@dataclass(frozen=True, kw_only=True, slots=True)
class ContributionLet(Let):
    """What a module adds to the web app (ADR 0056); consumed by `system.ui`. Its id is `<module>:<name>`."""

    order: int = ORDER_DEFAULT
    """Where it sits among others of its kind, 0 first; ties go by id."""
    refresh: timedelta = timedelta(seconds=60)
    slots: Mapping[Slot, bool] = field(default_factory=dict[Slot, bool])
    """Where it may appear, each shown or hidden by default; the person turns each on or off (`hz system ui show`)."""

    def _check(self, kind: str, bounds: tuple[timedelta, timedelta]) -> None:
        if not NAME_PATTERN.fullmatch(self.name):
            msg = f"The {kind} name {self.name!r} must match {NAME_PATTERN.pattern}"
            raise ValueError(msg)
        if not ORDER_MIN <= self.order <= ORDER_MAX:
            msg = f"The {kind} {self.name!r}: order must be {ORDER_MIN} to {ORDER_MAX}, not {self.order}"
            raise ValueError(msg)
        if problem := refresh_problem(self.refresh, bounds):
            msg = f"The {kind} {self.name!r}: {problem}"
            raise ValueError(msg)
        allowed = SLOTS[kind.split(maxsplit=1)[0]]
        slots: dict[Slot, bool] = dict(self.slots) or {allowed[0]: True}
        values: list[object] = list(slots.values())
        wrong: list[object] = [*sorted(set(slots) - set(allowed)), *(v for v in values if not isinstance(v, bool))]
        if wrong:
            msg = f"The {kind} {self.name!r}: slots are {', '.join(allowed)}, each true or false; not {wrong}"
            raise ValueError(msg)
        object.__setattr__(self, "slots", MappingProxyType(slots))


def _source(kind: str, name: str, source: str) -> None:
    if not NAME_PATTERN.fullmatch(source):
        msg = f"The {kind} {name!r}: its source {source!r} must be an operation name of the same module"
        raise ValueError(msg)


@dataclass(frozen=True, kw_only=True, slots=True)
class StatusLet(ContributionLet):
    """A status item: `view(result of source) -> StatusItem | None`, refreshed every `refresh`."""

    source: str
    """The read-only operation of the same module whose result `view` turns into the item."""
    view: View

    def __post_init__(self) -> None:
        """Refuse a name, order, refresh or source that can't work.

        Raises:
            ValueError: If one of them is invalid.
        """
        self._check("status item", STATUS_REFRESH)
        _source("status item", self.name, self.source)


@dataclass(frozen=True, kw_only=True, slots=True)
class WidgetLet(ContributionLet):
    """A widget: `view(result of source) -> Widget | None`, refreshed every `refresh`."""

    source: str
    view: View

    def __post_init__(self) -> None:
        """Refuse a name, order, refresh or source that can't work.

        Raises:
            ValueError: If one of them is invalid.
        """
        self._check("widget", WIDGET_REFRESH)
        _source("widget", self.name, self.source)


@dataclass(frozen=True, kw_only=True, slots=True)
class ActionLet(ContributionLet):
    """An action: a labelled icon button with one effect, and an optional badge viewed from `source`."""

    label: str
    """Its accessible name and tooltip."""
    icon: Icon
    effect: Navigate | OpenUrl | RunOperation | RunCommand
    source: str | None = None
    badge: View | None = None

    def __post_init__(self) -> None:
        """Check the label, icon and effect again, and that a badge comes with a source.

        Raises:
            ValueError: If the name, order, refresh, label, icon or effect is invalid, or only one of source and badge
                is given.
        """
        self._check("action", STATUS_REFRESH)
        checked = WidgetAction.model_validate({"label": self.label, "icon": self.icon, "effect": self.effect})
        object.__setattr__(self, "label", checked.label)
        object.__setattr__(self, "effect", checked.effect)
        if self.icon not in ICONS:
            msg = f"The action {self.name!r}: icon {self.icon!r} isn't one of {', '.join(sorted(ICONS))}"
            raise ValueError(msg)
        if (self.source is None) != (self.badge is None):
            msg = f"The action {self.name!r}: a badge needs a source to view, and a source a badge"
            raise ValueError(msg)
        if self.source is not None:
            _source("action", self.name, self.source)


@dataclass(frozen=True, kw_only=True, slots=True)
class NavLet(Let):
    """Static Settings and sidebar navigation offered by a module, consumed by `system.ui` (ADR 0072)."""

    title: str
    icon: Icon
    order: int = ORDER_DEFAULT
    settings: NavSettings | None = None
    sidebar: Effect | None = None

    def __post_init__(self) -> None:
        """Validate shape at declaration time; the consumer checks actual Settings ownership and effects.

        Raises:
            ValueError: If the name, display values, order or target is invalid.
        """
        checked = _NavData.model_validate({name: getattr(self, name) for name in _NavData.model_fields})
        for name in _NavData.model_fields:
            object.__setattr__(self, name, getattr(checked, name))
