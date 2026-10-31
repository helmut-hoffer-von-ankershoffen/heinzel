"""Feature flags: big work merges dark and stays off until someone switches it on by name (ADR 0114).

A flag hides behaviour, not data: a state schema change follows the rule that schemas are only ever fixed forward.
Register a flag here when the work starts, and delete it with its checks when the work ships.
"""

from heinzel.kernel._settings import Settings

FEATURES: dict[str, str] = {
    "durable_agents": "Durable agents that keep working between sessions, while they are being built.",
}
"""Every flag and what it switches on; everything is off unless named in the setting `features`."""


def feature_enabled(settings: Settings, name: str) -> bool:
    """Whether the flag `name` is on.

    Raises:
        KeyError: If `name` is not registered, so a typo in code can't silently leave work dark forever.
    """
    if name not in FEATURES:
        raise KeyError(name)
    return name in settings.features


def unknown_features(settings: Settings) -> list[str]:
    """Names in the setting that no flag has, to report; they switch nothing on."""
    return sorted(settings.features - FEATURES.keys())
