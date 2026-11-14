"""Feature flags: big work merges dark and stays off until someone switches it on by name (ADR 0115).

A flag hides behaviour, not data: a state schema change follows the rule that schemas are only ever fixed forward.
Register a flag here when the work starts, and delete it with its checks when the work ships.
"""

from heinzel.kernel._settings import Settings

FEATURES: dict[str, str] = {
    "sidebar_browser": "Isolated desktop sidebar browsing while native containment is being qualified.",
    "connected_frontdoor": "Create a durable helper from its first prompt while the connected journey is completed.",
    "connected_channels": "Exact owner-reviewed channel delivery and host-verified human replies.",
    "captured_writes": "Exact human-reviewed conditional writes while provider integrations are being completed.",
    "durable_agents": "Durable agents that keep working between sessions, while they are being built.",
}
"""Every flag and what it switches on; everything is off unless named in the setting `features`."""


def feature_catalog() -> dict[str, str]:
    """Return the registered preview flags and their user-facing descriptions."""
    return FEATURES.copy()


def feature_enabled(settings: Settings, name: str) -> bool:
    """Whether the flag `name` is on.

    Raises:
        KeyError: If `name` is not registered, so a typo in code can't silently leave work dark forever.
    """
    if name not in FEATURES:
        raise KeyError(name)
    return name in settings.features


RETIRED_FEATURES = frozenset({"persistent_web", "one_chat"})
"""Flags whose work shipped without a gate: web availability, and the one kind of chat (ADR 0157)."""


def unknown_features(settings: Settings) -> list[str]:
    """Names in the setting that no flag has, to report; they switch nothing on."""
    # Retired flags whose work shipped: older selected profiles may still name them, so they are not reported.
    return sorted(settings.features - FEATURES.keys() - RETIRED_FEATURES)
