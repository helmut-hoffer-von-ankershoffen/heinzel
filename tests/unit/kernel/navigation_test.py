"""Static navigation uses checked contribution vocabulary without a plugin-name map."""

import pytest
from pydantic import ValidationError

from heinzel import Module, Navigate, NavLet, NavSettings

pytestmark = pytest.mark.unit


def test_declaration_cleans_display_text_and_preserves_checked_targets():
    module = Module("harbor", "Harbor tools.")
    let = module.nav(
        title="  Harbor\u202e   tools ",
        icon="cloud-sun",
        settings=NavSettings(sections=("harbor",)),
        sidebar=Navigate(path="/?settings=harbor"),
    )
    assert let in module.lets
    assert (let.name, let.title, let.icon) == ("main", "Harbor tools", "cloud-sun")
    assert let.settings.sections == ("harbor",)
    assert let.sidebar.path == "/?settings=harbor"


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "../escape"},
        {"title": ""},
        {"title": "x" * 81},
        {"icon": "invented-icon"},
        {"order": -1},
        {"order": 1001},
        {"order": True},
        {"settings": None},
        {"sidebar": {"kind": "navigate", "path": "//evil.example"}},
    ],
)
def test_invalid_declaration_data_is_refused(changes):
    fields = {"name": "main", "title": "Harbor", "icon": "code", "settings": NavSettings(sections=("harbor",))}
    with pytest.raises(ValidationError):
        NavLet(**(fields | changes))


@pytest.mark.parametrize("sections", [(), ("harbor", "harbor"), ("harbor..other",), ("hz/*",), ("harbor",) * 9])
def test_sections_are_bounded_distinct_qualified_prefixes(sections):
    with pytest.raises(ValidationError):
        NavSettings(sections=sections)


def test_navigation_uses_existing_duplicate_and_per_provider_caps():
    module = Module("harbor", "Harbor tools.")
    for number in range(4):
        module.nav(f"entry-{number}", title="Harbor", icon="code", sidebar=Navigate(path="/"))
    with pytest.raises(ValueError, match="4 nav"):
        module.nav("extra", title="Harbor", icon="code", sidebar=Navigate(path="/"))
    other = Module("harbor", "Harbor tools.")
    other.nav(title="Harbor", icon="code", sidebar=Navigate(path="/"))
    with pytest.raises(ValueError, match="already has"):
        other.nav(title="Duplicate", icon="code", sidebar=Navigate(path="/"))
