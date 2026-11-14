"""A Heinzel's intelligence level and how code turns it into a model and an effort (ADR 0158).

The person picks a level (Auto, Low, Medium, High) and, optionally, a provider and an exact model on the Heinzel's
page. The provider defines the levels: it marks which of its models is Low, Medium and High. `resolve` picks the
model marked for the level, or the nearest level the provider marks: pure, deterministic, never asked of a model.
Auto is computed by code among the provider's levels; in this release it runs as Medium (or the nearest marked).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from heinzel.kernel import Effort, HeinzelError
from heinzel.kernel._ai_models import MODEL_ID_PATTERN, ModelOffer

Level = Literal["auto", "low", "medium", "high"]
"""What a person picks for a Heinzel: how hard it thinks, never a model name."""
Tier = Literal["low", "medium", "high"]
"""A level a provider marks a model for; Auto resolves to one of these by code."""
Rule = Literal["fixed", "auto-fixed-medium"]
"""How the level asked for was decided: as picked, or Auto's fixed rule of this release."""
Source = Literal["exact", "declared", "nearest", "provider-default"]
"""How the model was chosen: the exact model, the provider's model for the level, its model for the nearest level it
marks, or the provider's own default when it marks none."""

PROVIDER_PATTERN = r"^[a-z][a-z0-9-]{0,63}$"
ProviderName = Annotated[str, StringConstraints(pattern=PROVIDER_PATTERN)]

AUTO_RUNS_AS: Tier = "medium"
"""Auto's fixed rule until the Auto router ships (ADR 0158, statement 4)."""

TIERS: tuple[Tier, ...] = ("low", "medium", "high")
EFFORT_ORDER: tuple[Effort, ...] = ("low", "medium", "high", "xhigh", "max")
LEVEL_EFFORT: dict[Tier, Effort] = {"low": "low", "medium": "medium", "high": "high"}


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
    ran: Tier = Field(
        description="The level the turn ran as: Auto resolved by code, the nearest one the provider marks."
    )
    rule: Rule = Field(description="How Auto was resolved, or `fixed` for a level picked as is.")
    source: Source = Field(description="How the model was chosen: exact, declared, nearest or provider-default.")


@dataclass(frozen=True, slots=True)
class LevelChoice:
    """What `resolve` decided: the model and effort to run, and what the turn records."""

    model: str | None
    """The model id; none when the provider marks no level, so it runs its own default."""
    effort: Effort | None
    """The effort to pass; none when the model takes none."""
    record: TurnLevel


def effort_for(offer: ModelOffer, tier: Tier) -> Effort | None:
    """The effort `tier` asks of `offer`: none without `thinking`, else the nearest it lists, the lower on a tie."""
    if offer.thinking is None:
        return None
    wanted = LEVEL_EFFORT[tier]
    if not offer.efforts or wanted in offer.efforts:
        return wanted
    target = EFFORT_ORDER.index(wanted)
    return min(offer.efforts, key=lambda effort: (abs(EFFORT_ORDER.index(effort) - target), EFFORT_ORDER.index(effort)))


def declared_levels(offers: list[ModelOffer]) -> dict[Tier, str]:
    """The model a provider marks for each level: the first offer marked with it, in the provider's order."""
    declared: dict[Tier, str] = {}
    for offer in offers:
        marked = getattr(offer, "level", None)
        if marked in TIERS:
            declared.setdefault(marked, offer.id)
    return declared


def asked_as(level: Level) -> tuple[Tier, Rule]:
    """The level a turn asks the provider for, and the rule that decided it: Auto by code, never by a model."""
    if level == "auto":
        return AUTO_RUNS_AS, "auto-fixed-medium"
    return level, "fixed"


def nearest(tier: Tier, marked: set[Tier]) -> Tier:
    """`tier` when marked, else the nearest marked level, the lower on a tie so a level never spends more."""
    target = TIERS.index(tier)
    return min(marked, key=lambda other: (abs(TIERS.index(other) - target), TIERS.index(other)))


def resolve(  # noqa: PLR0913 - the provider's declaration and the Heinzel's choice, all pure inputs
    provider: str,
    level: Level,
    declared: Mapping[Tier, str],
    offers: list[ModelOffer],
    *,
    exact: str | None = None,
    granted: frozenset[str] | None = None,
) -> LevelChoice:
    """The model and effort a turn of a Heinzel runs on `provider` at `level` (ADR 0158); pure and deterministic.

    `declared` is the model the provider marks for each level; `offers` its list, empty when it lists none. A level
    the provider doesn't mark runs on the nearest one it does. `granted`, when set, is the models a grant covers: a
    level chooses only among them and never widens it.

    Raises:
        HeinzelError: If the exact model can't run, or the provider marks no model this Heinzel may run.
    """
    tier, rule = asked_as(level)
    if exact is not None:
        offer = _exact(provider, offers, exact, granted)
        return LevelChoice(
            offer.id, effort_for(offer, tier), TurnLevel(asked=level, ran=tier, rule=rule, source="exact")
        )
    by_id = {offer.id: offer for offer in offers}
    runnable = {
        marked: model
        for marked, model in declared.items()
        if (not offers or (model in by_id and by_id[model].tools and by_id[model].available))
        and (granted is None or model in granted)
    }
    if not runnable:
        if declared or granted is not None:
            why = (
                "none of the models it marks for a level is covered by the grant"
                if granted is not None
                else ("none of the models it marks for a level answers and calls tools now")
            )
            msg = f"Couldn't pick a model for this Heinzel's level on the AI provider {provider}: {why}"
            raise HeinzelError(msg)
        return LevelChoice(None, None, TurnLevel(asked=level, ran=tier, rule=rule, source="provider-default"))
    ran = nearest(tier, set(runnable))
    model = runnable[ran]
    effort = effort_for(by_id[model], ran) if model in by_id else LEVEL_EFFORT[ran]
    source: Source = "declared" if ran == tier else "nearest"
    return LevelChoice(model, effort, TurnLevel(asked=level, ran=ran, rule=rule, source=source))


def _exact(provider: str, offers: list[ModelOffer], exact: str, granted: frozenset[str] | None) -> ModelOffer:
    """The exact model's offer, refused with the reason and the way out when it can't run.

    Raises:
        HeinzelError: If the provider lists no models, or it isn't offered, is unavailable, calls no tools, or no
            grant covers it.
    """
    offer = next((o for o in offers if o.id == exact), None)
    way_out = "pick another or clear the exact model on the Heinzel's page"
    if not offers:
        why = f"the AI provider {provider} lists no models, so hz can't hold it to one"
    elif offer is None:
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
