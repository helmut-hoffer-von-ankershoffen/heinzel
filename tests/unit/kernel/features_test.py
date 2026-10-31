"""Feature flags keep big work dark until someone names it."""

import pytest

from heinzel.kernel import FEATURES, Settings, feature_enabled, unknown_features

pytestmark = pytest.mark.unit


def test_every_flag_is_off_by_default():
    assert not any(feature_enabled(Settings(), name) for name in FEATURES)


def test_a_named_flag_is_on_and_others_stay_off(monkeypatch):
    monkeypatch.setitem(FEATURES, "other", "Another one.")
    settings = Settings(features=frozenset({"durable_agents"}))
    assert feature_enabled(settings, "durable_agents")
    assert not feature_enabled(settings, "other")


def test_the_environment_takes_a_comma_separated_list(monkeypatch):
    monkeypatch.setenv("HZ_FEATURES", "durable_agents, typo")
    settings = Settings()
    assert feature_enabled(settings, "durable_agents")
    assert unknown_features(settings) == ["typo"]


def test_an_unregistered_name_in_code_is_a_bug():
    with pytest.raises(KeyError):
        feature_enabled(Settings(), "typo")
