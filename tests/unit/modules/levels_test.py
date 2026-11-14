"""The intelligence level resolver: a level and a provider's offers become a model and an effort (ADR 0158)."""

import pytest

from heinzel import HeinzelError, ModelOffer
from heinzel.modules.heinzel._levels import (
    AUTO_RUNS_AS,
    TIER_WORDS,
    HeinzelIntelligence,
    effort_for,
    resolve,
    tier_of,
)

pytestmark = pytest.mark.unit

TIERED = [
    ModelOffer(id="acme-sonnet-4", thinking="adaptive"),
    ModelOffer(id="acme-haiku-4", thinking="adaptive", efforts=["low", "medium"]),
    ModelOffer(id="acme-opus-4", thinking="budget", efforts=["low", "medium", "high", "max"]),
]


@pytest.mark.parametrize(
    ("level", "model", "effort", "ran", "rule"),
    [
        ("low", "acme-haiku-4", "low", "low", "fixed"),
        ("medium", "acme-sonnet-4", "medium", "medium", "fixed"),
        ("high", "acme-opus-4", "high", "high", "fixed"),
        ("auto", "acme-sonnet-4", "medium", "medium", "auto-fixed-medium"),
    ],
)
def test_every_level_maps_to_its_tier_and_effort(level, model, effort, ran, rule):
    choice = resolve("acme", level, TIERED)
    assert (choice.model, choice.effort) == (model, effort)
    assert (choice.record.asked, choice.record.ran, choice.record.rule) == (level, ran, rule)
    assert choice.record.source == "table"


def test_auto_runs_as_medium_by_a_fixed_rule_and_records_it():
    assert AUTO_RUNS_AS == "medium"
    assert resolve("acme", "auto", TIERED) == resolve("acme", "auto", TIERED)


@pytest.mark.parametrize("level", ["auto", "low", "medium", "high"])
def test_offers_without_hints_fall_back_to_the_providers_default(level):
    offers = [ModelOffer(id="house-model"), ModelOffer(id="other-model")]
    choice = resolve("acme", level, offers)
    assert (choice.model, choice.effort, choice.record.source) == ("house-model", None, "default")


def test_a_missing_tier_falls_back_to_the_default_with_the_levels_effort():
    offers = [ModelOffer(id="acme-sonnet-4", thinking="adaptive")]
    choice = resolve("acme", "high", offers)
    assert (choice.model, choice.effort, choice.record.source, choice.record.ran) == (
        "acme-sonnet-4",
        "high",
        "default",
        "high",
    )


def test_unavailable_and_toolless_offers_are_never_picked():
    offers = [
        ModelOffer(id="fast-mini", available=False),
        ModelOffer(id="chat-mini", tools=False),
        ModelOffer(id="house"),
    ]
    assert resolve("acme", "low", offers).model == "house"


def test_no_usable_offer_is_refused_naming_the_action():
    with pytest.raises(HeinzelError, match="Couldn't pick a model for this Heinzel's level on the AI provider acme"):
        resolve("acme", "medium", [ModelOffer(id="down", available=False)])


@pytest.mark.parametrize("level", ["auto", "low", "medium", "high"])
def test_a_provider_without_offers_runs_its_own_default(level):
    choice = resolve("claude-code", level, [])
    assert (choice.model, choice.effort, choice.record.source) == (None, None, "provider-default")
    assert choice.record.asked == level


def test_an_exact_model_on_a_provider_without_offers_is_refused():
    with pytest.raises(HeinzelError, match=r"lists no models.*clear the exact model in the Heinzel's details"):
        resolve("claude-code", "low", [], exact="claude-x")


@pytest.mark.parametrize("level", ["auto", "low", "medium", "high"])
def test_the_exact_model_wins_over_every_level(level):
    choice = resolve("acme", level, TIERED, exact="acme-opus-4")
    assert choice.model == "acme-opus-4"
    assert choice.record.source == "exact"
    assert choice.effort == {"auto": "medium", "low": "low", "medium": "medium", "high": "high"}[level]


@pytest.mark.parametrize(
    ("offers", "why"),
    [
        ([ModelOffer(id="x", available=False)], "didn't answer"),
        ([ModelOffer(id="x", tools=False)], "doesn't call tools"),
        ([ModelOffer(id="y")], "doesn't offer it now"),
    ],
)
def test_an_exact_model_that_cant_run_is_refused(offers, why):
    with pytest.raises(HeinzelError, match=rf"Couldn't run this Heinzel on the exact model x: .*{why}"):
        resolve("acme", "medium", offers, exact="x")


def test_a_level_never_widens_a_grant():
    granted = frozenset({"acme-sonnet-4"})
    choice = resolve("acme", "high", TIERED, granted=granted)
    assert (choice.model, choice.record.source) == ("acme-sonnet-4", "default")
    with pytest.raises(HeinzelError, match="no model grant covers it, and a level never widens a grant"):
        resolve("acme", "high", TIERED, exact="acme-opus-4", granted=granted)
    with pytest.raises(HeinzelError, match="none of its models is covered by the grant"):
        resolve("acme", "low", TIERED, granted=frozenset({"elsewhere"}))


@pytest.mark.parametrize(
    ("efforts", "thinking", "tier", "effort"),
    [
        ([], None, "high", None),
        ([], "adaptive", "high", "high"),
        (["low", "max"], "budget", "medium", "low"),
        (["medium", "max"], "budget", "high", "medium"),
        (["xhigh"], "adaptive", "low", "xhigh"),
        (["low", "medium", "high"], "adaptive", "low", "low"),
    ],
)
def test_effort_is_the_nearest_the_offer_takes_the_lower_on_a_tie(efforts, thinking, tier, effort):
    assert effort_for(ModelOffer(id="m", efforts=efforts, thinking=thinking), tier) == effort


@pytest.mark.parametrize(
    ("model_id", "aliases", "tier"),
    [
        ("claude-haiku-4-5", [], "low"),
        ("gpt-5-mini", [], "low"),
        ("gpt-5-nano", [], "low"),
        ("gemini-2.5-flash-lite", [], "low"),
        ("mistral-small", [], "low"),
        ("tiny-llama", [], "low"),
        ("claude-opus-4-1", [], "high"),
        ("o3-pro", [], "high"),
        ("model-ultra", [], "high"),
        ("mistral-large", [], "high"),
        ("claude-sonnet-4-5", [], "medium"),
        ("gemini-2.5-flash", [], "medium"),
        ("house-medium", [], "medium"),
        ("house-model", [], None),
        ("house-model", ["team/opus-proxy"], "high"),
        ("Acme-MINI:8b", [], "low"),
    ],
)
def test_the_tier_table_row_by_row(model_id, aliases, tier):
    assert tier_of(ModelOffer(id=model_id, aliases=aliases)) == tier


def test_low_words_are_checked_first():
    assert TIER_WORDS[0][0] == "low"
    assert tier_of(ModelOffer(id="pro-mini")) == "low"


def test_the_stored_intelligence_defaults_to_auto_with_no_provider_or_model():
    assert HeinzelIntelligence().model_dump() == {"level": "auto", "provider": None, "model": None}
    with pytest.raises(ValueError, match="level"):
        HeinzelIntelligence.model_validate({"level": "turbo"})
    with pytest.raises(ValueError, match="provider"):
        HeinzelIntelligence.model_validate({"provider": "Not A Name"})
