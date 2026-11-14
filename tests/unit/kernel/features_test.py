"""Feature flags keep big work dark until someone names it."""

import pytest

from heinzel.kernel import Settings, feature_enabled
from heinzel.kernel._features import FEATURES, unknown_features

pytestmark = pytest.mark.unit


def test_every_flag_is_off_by_default():
    assert not any(feature_enabled(Settings(), name) for name in FEATURES)


def test_a_named_flag_is_on_and_others_stay_off(monkeypatch):
    monkeypatch.setitem(FEATURES, "other", "Another one.")
    settings = Settings(features=frozenset({"connected_frontdoor"}))
    assert feature_enabled(settings, "connected_frontdoor")
    assert not feature_enabled(settings, "other")


def test_the_environment_takes_a_comma_separated_list(monkeypatch):
    monkeypatch.setenv("HZ_FEATURES", "connected_frontdoor, typo")
    settings = Settings()
    assert feature_enabled(settings, "connected_frontdoor")
    assert unknown_features(settings) == ["typo"]


def test_an_unregistered_name_in_code_is_a_bug():
    settings = Settings()
    with pytest.raises(KeyError):
        feature_enabled(settings, "typo")


@pytest.mark.parametrize("retired", ["persistent_web", "sidebar_browser", "durable_agents"])
def test_retired_preview_setting_is_harmless_without_hiding_other_unknown_flags(retired):
    settings = Settings(features={retired, "misspelled"})
    assert retired not in FEATURES
    assert unknown_features(settings) == ["misspelled"]
