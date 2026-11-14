"""A Heinzel's intelligence level and how code turns it into a model and an effort (ADR 0158).

The person picks a level (Auto, Low, Medium, High) and, optionally, a provider and an exact model in the Heinzel's
details. `resolve` maps that to one of the provider's offers: pure, deterministic, never asked of a model. Auto is
routed by code: in this release it runs as Medium by one fixed rule, and the turn records that.
"""

import re
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from heinzel.kernel import Effort, HeinzelError
from heinzel.kernel._ai_models import MODEL_ID_PATTERN, ModelOffer

Level = Literal["auto", "low", "medium", "high"]
"""What a person picks for a Heinzel: how hard it thinks, never a model name."""
Tier = Literal["low", "medium", "high"]
"""The level a turn runs as: Auto resolves to one of these by code."""
Rule = Literal["fixed", "auto-fixed-medium"]
"""How the level a turn ran as was decided: as picked, or Auto's fixed rule of this release."""
Source = Literal["exact", "table", "default", "provider-default"]
"""How the model was chosen: the exact model, the tier table, the provider's first offer, or the provider's own."""

PROVIDER_PATTERN = r"^[a-z][a-z0-9-]{0,63}$"
ProviderName = Annotated[str, StringConstraints(pattern=PROVIDER_PATTERN)]

AUTO_RUNS_AS: Tier = "medium"
"""Auto's fixed rule until the Auto router ships (ADR 0158, statement 4)."""

EFFORT_ORDER: tuple[Effort, ...] = ("low", "medium", "high", "xhigh", "max")
LEVEL_EFFORT: dict[Tier, Effort] = {"low": "low", "medium": "medium", "high": "high"}

TIER_WORDS: tuple[tuple[Tier, frozenset[str]], ...] = (
    ("low", frozenset({"haiku", "mini", "nano", "lite", "small", "tiny"})),
    ("high", frozenset({"opus", "pro", "ultra", "large"})),
    ("medium", frozenset({"sonnet", "flash", "medium"})),
)
"""The code-defined fallback table: words in a model's id or aliases and the tier they mean, Low checked first."""


class HeinzelIntelligence(BaseModel):
    """What a Heinzel's profile stores about its intelligence; the default is left out of the stored profile."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    level: Level = Field(default="auto", description="How hard this Heinzel thinks: auto, low, medium or high.")
    provider: ProviderName | None = Field(
        default=None, description="The AI access option it runs on; none for the configured AI access."
    )
    model: Annotated[str, StringConstraints(pattern=MODEL_ID_PATTERN)] | None = Field(
        default=None, description="An exact model id, an advanced choice that wins over the level; none for none."
    )


class TurnLevel(BaseModel):
    """What a turn recorded about its level: asked, ran as, by which rule, and how the model was chosen."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    asked: Level = Field(description="The level the Heinzel had when the turn started.")
    ran: Tier = Field(description="The level the turn ran as; Auto resolved by code.")
    rule: Rule = Field(description="How Auto was resolved, or `fixed` for a level picked as is.")
    source: Source = Field(description="How the model was chosen: exact, table, default or provider-default.")


@dataclass(frozen=True, slots=True)
class LevelChoice:
    """What `resolve` decided: the model and effort to run, and what the turn records."""

    model: str | None
    """The offer's id; none for a provider that lists no models, which then runs its own default."""
    effort: Effort | None
    """The effort to pass; none when the model takes none."""
    record: TurnLevel


def tier_of(offer: ModelOffer) -> Tier | None:
    """The tier the table gives `offer` by the words in its id and aliases, or none for no known word."""
    words = {word for name in (offer.id, *offer.aliases) for word in re.split(r"[^a-z0-9]+", name.lower()) if word}
    return next((tier for tier, known in TIER_WORDS if words & known), None)


def effort_for(offer: ModelOffer, tier: Tier) -> Effort | None:
    """The effort `tier` asks of `offer`: none without `thinking`, else the nearest it lists, the lower on a tie."""
    if offer.thinking is None:
        return None
    wanted = LEVEL_EFFORT[tier]
    if not offer.efforts or wanted in offer.efforts:
        return wanted
    target = EFFORT_ORDER.index(wanted)
    return min(offer.efforts, key=lambda effort: (abs(EFFORT_ORDER.index(effort) - target), EFFORT_ORDER.index(effort)))


def ran_as(level: Level) -> tuple[Tier, Rule]:
    """The level a turn runs as and the rule that decided it: Auto by code, never by a model."""
    if level == "auto":
        return AUTO_RUNS_AS, "auto-fixed-medium"
    return level, "fixed"


def resolve(
    provider: str,
    level: Level,
    offers: list[ModelOffer],
    *,
    exact: str | None = None,
    granted: frozenset[str] | None = None,
) -> LevelChoice:
    """The model and effort a turn of a Heinzel runs on `provider` at `level` (ADR 0158); pure and deterministic.

    `offers` is the provider's list, its default first; empty for a provider that lists none. `granted`, when set,
    is the models a grant covers: a level chooses only among them and never widens it.

    Raises:
        HeinzelError: If the exact model can't run, or the provider offers no model this level may run.
    """
    tier, rule = ran_as(level)
    if not offers:
        if exact is not None:
            msg = (
                f"Couldn't run this Heinzel on the exact model {exact}: the AI provider {provider} lists no models, "
                "so hz can't hold it to one; clear the exact model in the Heinzel's details"
            )
            raise HeinzelError(msg)
        return LevelChoice(None, None, TurnLevel(asked=level, ran=tier, rule=rule, source="provider-default"))
    if exact is not None:
        offer = _exact(provider, offers, exact, granted)
        return LevelChoice(
            offer.id, effort_for(offer, tier), TurnLevel(asked=level, ran=tier, rule=rule, source="exact")
        )
    candidates = [o for o in offers if o.tools and o.available and (granted is None or o.id in granted)]
    if not candidates:
        why = (
            "none of its models is covered by the grant"
            if granted is not None
            else "it offers no model that answers and calls tools"
        )
        msg = f"Couldn't pick a model for this Heinzel's level on the AI provider {provider}: {why}"
        raise HeinzelError(msg)
    matched = next((o for o in candidates if tier_of(o) == tier), None)
    source: Source = "table" if matched is not None else "default"
    offer = matched or candidates[0]
    return LevelChoice(offer.id, effort_for(offer, tier), TurnLevel(asked=level, ran=tier, rule=rule, source=source))


def _exact(provider: str, offers: list[ModelOffer], exact: str, granted: frozenset[str] | None) -> ModelOffer:
    """The exact model's offer, refused with the reason and the way out when it can't run.

    Raises:
        HeinzelError: If it isn't offered, is unavailable, calls no tools, or no grant covers it.
    """
    offer = next((o for o in offers if o.id == exact), None)
    way_out = "pick another or clear the exact model in the Heinzel's details"
    if offer is None:
        why = f"the AI provider {provider} doesn't offer it now"
    elif not offer.available:
        why = "it didn't answer when the provider last checked"
    elif not offer.tools:
        why = "it doesn't call tools, which chat needs"
    elif granted is not None and offer.id not in granted:
        why = "no model grant covers it, and a level never widens a grant"
    else:
        return offer
    msg = f"Couldn't run this Heinzel on the exact model {exact}: {why}; {way_out}"
    raise HeinzelError(msg)
