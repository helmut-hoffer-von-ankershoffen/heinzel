"""Feature flags: big work merges dark and stays off until someone switches it on by name (ADR 0115).

A flag hides behaviour, not data: a state schema change follows the rule that schemas are only ever fixed forward.
Register a flag here when the work starts, and delete it with its checks when the work ships.
"""

from heinzel.kernel._settings import Settings

FEATURES: dict[str, str] = {
    "connected_channels": "Exact owner-reviewed channel delivery and host-verified human replies.",
    "captured_writes": "Exact human-reviewed conditional writes while provider integrations are being completed.",
}
"""Every flag and what it switches on; everything is off unless named in the setting `features`."""


RETIRED_FEATURES = frozenset({"persistent_web", "sidebar_browser", "durable_agents", "one_chat", "connected_frontdoor"})
"""Flags that shipped or were removed (one_chat: the one kind of chat, ADR 0157); a setting still naming one is ignored,
not reported."""


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


def unknown_features(settings: Settings) -> list[str]:
    """Names in the setting that no flag has, to report; they switch nothing on."""
    # Shipped work no longer has a gate; older selected profiles may still name these retired flags.
    return sorted(settings.features - FEATURES.keys() - RETIRED_FEATURES)
