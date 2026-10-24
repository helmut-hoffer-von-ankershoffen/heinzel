"""`clean_text`: what adapters strip from every string they return (ADR 0028)."""

import pytest

from heinzel import clean_text

pytestmark = pytest.mark.unit


def test_strips_terminal_escapes_and_other_control_characters():
    assert clean_text("\x1b[31mred\x1b[0m \x9b2J\x07\x00done\x7f") == "[31mred[0m 2Jdone"


def test_strips_bidirectional_overrides_and_isolates():
    assert clean_text("pay \u202e001\u202c EUR \u2066now\u2069") == "pay 001 EUR now"


def test_keeps_newlines_tabs_and_every_other_character():
    text = "Agenda:\n\t1. Ärger über Umlaute 🎉\n\t2. 日本語"
    assert clean_text(text) == text


def test_an_empty_string_stays_empty():
    assert clean_text("") == ""
