"""What `hz system ui` lists: each contribution with its value or the problem that keeps it from showing (ADR 0056)."""

from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from heinzel import Icon, ModuleSettings, NavSettings, Slot, StatusItem, Widget
from heinzel.kernel import Badge, Effect

Kind = Literal["status", "action", "widget"]


class UiSettings(ModuleSettings):
    """Where each contribution shows: the `[system.ui]` table, written by `hz system ui show` and `hide`.

    User scope: a project `hz.toml` can't set it, because a repository someone else controls must not decide what a
    person's app shows.
    """

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"contributions"})

    contributions: dict[str, dict[Slot, bool]] = Field(
        default_factory=dict[str, dict[Slot, bool]],
        title="What the app shows",
        description="Which status items, actions and widgets show in which place of the app; anything you leave out "
        "keeps the place its module chose.",
        json_schema_extra={"x-expert": True},
    )


class Entry(BaseModel):
    """A contribution as the app sees it: where it comes from, where it sits, how often to ask again."""

    id: str = Field(description="`<module>:<name>`, what `--item` takes, e.g. `clock:primary-zone`.")
    module: str = Field(description="The qualified name of the contributing module, e.g. `clock`.")
    plugin: str | None = Field(description="The plugin it comes with, e.g. `heinzel-clock`; none for a built-in.")
    order: int = Field(description="Where it sits among its kind, 0 first; ties go by id.")
    refresh: int = Field(description="How often to ask again, in seconds.")
    slots: dict[Slot, bool] = Field(
        description="Each slot it may show in, and whether it does: what the app filters by."
    )
    problem: str | None = Field(
        default=None, description="Why it shows nothing: its source failed, or the declaration can't work."
    )


class StatusEntry(Entry):
    """A status item, or the problem that keeps it from showing."""

    item: StatusItem | None = Field(default=None, description="What to show; none when there is nothing to show.")


class ActionEntry(Entry):
    """An action: a labelled icon button with one effect and maybe a badge."""

    label: str = Field(description="Its accessible name and tooltip.")
    icon: Icon = Field(description="The icon the app shows.")
    effect: Effect | None = Field(default=None, description="What a click does; none when it can't work (`problem`).")
    badge: Badge | None = Field(default=None, description="A count (0 shows none) or up to four characters.")


class WidgetEntry(Entry):
    """A widget, or the problem that keeps it from showing."""

    widget: Widget | None = Field(default=None, description="What to show; none when there is nothing to show.")


class ContributionInfo(BaseModel):
    """A contribution as Settings lists it: where it may show and whether it does, without running it."""

    id: str = Field(description="`<module>:<name>`, e.g. `clock:primary-zone`.")
    module: str = Field(description="The qualified name of the contributing module, e.g. `clock`.")
    kind: Kind = Field(description="A status item, a header action or a widget.")
    title: str = Field(description="What people know it by: the action's label, or its name in words.")
    slots: dict[Slot, bool] = Field(description="Each slot it may show in, and whether it does now.")
    defaults: dict[Slot, bool] = Field(description="Each slot and whether it shows there unless the person decides.")


class NavEntry(BaseModel):
    """Checked navigation metadata from one active provider, without running an operation."""

    id: str = Field(
        description="Stable `<qualified-module>:<name>` identity; legacy Settings use reserved `:@settings`."
    )
    module: str = Field(description="The qualified module that declared this entry.")
    declared: bool = Field(description="True for an explicit NavLet; false for generic legacy Settings metadata.")
    plugin: str | None = Field(description="Its distribution, or none for a built-in provider.")
    title: str = Field(description="The provider's checked display title.")
    icon: Icon = Field(description="The provider's checked semantic icon.")
    order: int = Field(description="Display order; ties are sorted by stable id.")
    settings: NavSettings | None = Field(default=None, description="Owned Settings prefixes and their group.")
    sidebar: Effect | None = Field(
        default=None, description="The canonical sidebar effect; no new route is registered."
    )
    problem: str | None = Field(default=None, description="Why this declaration has no executable target.")
