"""Models of the layer module: the installed layers and the stack a person is in (ADR 0040)."""

from pydantic import BaseModel, Field, JsonValue


class LayerView(BaseModel):
    """An installed layer: where it sits and what it brings."""

    name: str = Field(description="The layer's module name, e.g. acme-payments-checkout.")
    label: str = Field(description="Where it sits, derived from its position: company, org, site or team.")
    parent: str | None = Field(description="The layer above it; none for the company.")
    site: str | None = Field(description="The site layer it is at; none if it names none.")
    plugin: str = Field(description="The plugin it comes with, e.g. heinzel-acme-payments-checkout.")
    module: str = Field(description="The module that declares it.")
    adapters: list[str] = Field(description="The adapters its plugin brings, `<adapter>:<capability>`.")
    roles: list[str] = Field(description="The roles it adds to the core vocabulary.")
    playbook: str | None = Field(description="What its playbook is for; none without one.")
    hint: str | None = Field(description="How its name would follow its path from the company; none when it does.")


class LayerProblemView(BaseModel):
    """Something wrong with an installed layer, and what fixes it."""

    layer: str = Field(description="The layer it is about; empty when it is about none in particular.")
    problem: str = Field(description="What kind, e.g. missing-parent, cycle, declared-twice or not-installed.")
    detail: str = Field(description="What is wrong.")
    fix: str = Field(description="What fixes it, as a command where one does.")


class LayerList(BaseModel):
    """`hz system layer list`: every installed layer, and what is wrong with the tree."""

    layers: list[LayerView] = Field(description="Every installed layer that passed the checks, by name.")
    problems: list[LayerProblemView] = Field(description="Layers left out or incomplete, each with its fix.")
    next: list[str] = Field(description="What to run next.")


class RecommendationView(BaseModel):
    """A setting the stack recommends; applied only when you accept it."""

    key: str = Field(description="The setting, `<section>.<key>`.")
    value: JsonValue = Field(description="The value offered: the most specific layer's.")
    layer: str = Field(description="The layer whose value is offered.")
    alternatives: dict[str, JsonValue] = Field(
        description="What more general layers recommend instead, by layer, most specific first."
    )
    command: str = Field(description="The command that applies it, if you agree.")


class ConflictView(BaseModel):
    """An org and the site recommend different values and your team is silent: nothing is offered."""

    key: str = Field(description="The setting they disagree on.")
    values: dict[str, JsonValue] = Field(description="Each layer's value, general to specific.")
    fix: str = Field(description="How it gets decided.")


class StackView(BaseModel):
    """`hz system layer show`: the layers you are in, general to specific, and what they bring together."""

    layer: str | None = Field(description="Your layer, e.g. your team's; none when nothing says which.")
    source: str = Field(description="Where your layer came from, e.g. your team.")
    stack: str = Field(description="The stack in one line: company → org + site → team.")
    layers: list[LayerView] = Field(description="The layers, general to specific.")
    roles: list[str] = Field(description="The roles the layers add, general to specific.")
    recommendations: list[RecommendationView] = Field(
        description="The settings the layers' playbooks recommend, most specific first; none is applied by itself."
    )
    conflicts: list[ConflictView] = Field(
        description="Keys an org and the site disagree on, left to you and your team."
    )
    problems: list[LayerProblemView] = Field(description="Why the stack is empty or incomplete, with fixes.")
    next: list[str] = Field(description="What to run next.")
