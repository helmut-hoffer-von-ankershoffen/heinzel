"""Models of plugin setup: the bindings a company's playbook sets, the steps of a setup and its checklist."""

from typing import Literal

from pydantic import BaseModel, Field

BindingState = Literal["written", "kept", "skipped", "conflict", "removed"]
"""What became of one recommended binding: written now, kept as the person (or an earlier setup) set it, skipped
(its connector isn't installed, or binding was declined), a conflict between layers that binds nothing, or removed by
an undo."""
RowState = Literal["not-installed", "not-connected", "needs-attention", "connected"]
"""How one connector of a checklist stands, in the order a person meets them."""
RowActionKind = Literal["install", "sign-in", "bind", "settings", "check"]
StepState = Literal["done", "skipped", "needs-cli", "failed"]


class BindingResult(BaseModel):
    """One capability a playbook recommends a connector for, and what setup did about it."""

    capability: str = Field(description="The capability, e.g. `tracker`.")
    adapter: str | None = Field(description="The connector recommended; none for a conflict between layers.")
    state: BindingState = Field(description="What setup did: written, kept, skipped, conflict or removed.")
    reason: str = Field(default="", description="Why, in a few words, e.g. `yours: github` for a kept binding.")
    by: str = Field(description="The module whose playbook recommends it, e.g. `acme`.")


class SetupBind(BaseModel):
    """The bindings one setup wrote, kept or skipped; an undo lists those it removed."""

    bindings: list[BindingResult] = Field(description="Each recommended binding with its result, in playbook order.")
    message: str = Field(description="One line for a person: how many were set and kept.")


class RowAction(BaseModel):
    """The one thing to do next about a checklist row, as data a head turns into its own button."""

    kind: RowActionKind = Field(description="What it does: install, sign in, bind, open settings or check again.")
    connector: str = Field(description="The connector it is about, e.g. `github`.")
    capability: str | None = Field(default=None, description="For `bind`: the capability to serve.")
    command: str = Field(default="", description="The same action as a command line, for a terminal only.")


class ChecklistRow(BaseModel):
    """One connector a company's playbook needs, how it stands, and the one next action."""

    id: str = Field(description="Stable id of the row, `<module>:<connector>`.")
    connector: str = Field(description="The connector (adapter) name, e.g. `atlassian`.")
    capabilities: list[str] = Field(description="What the playbook has it serve, e.g. `tracker`, `documents`.")
    state: RowState = Field(description="not-installed, not-connected, needs-attention or connected.")
    line: str = Field(description="One status line, state first, at most 60 characters.")
    action: RowAction = Field(description="The one next action.")
    owner_only: bool = Field(
        description="Whether only the person can do it (a sign-in or a site of their own); setup does everything else."
    )


class Checklist(BaseModel):
    """What is still to do so a company's playbook is fully set up: one row per connector it names."""

    module: str | None = Field(description="The module whose playbook it is; none for every playbook installed.")
    rows: list[ChecklistRow] = Field(description="One row per connector, in the order the playbook names them.")
    done: bool = Field(description="Whether every row is connected.")


class SetupStep(BaseModel):
    """One step of `system plugin setup`, as it ended."""

    name: str = Field(description="The step: `bind`, `settings`, `templates` or `check`.")
    state: StepState = Field(description="done, skipped, needs-cli (the wizard `hz setup` asks it) or failed.")
    detail: str = Field(default="", description="What it did or why not.")
    command: str = Field(
        default="", description="For `needs-cli`: the command a terminal runs; never shown by a web head."
    )


class SetupResult(BaseModel):
    """What `system plugin setup` did: its steps, the bindings and the checklist after it."""

    module: str = Field(description="The module set up.")
    steps: list[SetupStep] = Field(description="Each step, in order.")
    bindings: list[BindingResult] = Field(description="Each recommended binding with its result.")
    checklist: Checklist = Field(description="What is still to do, mostly the person's own sign-ins.")
