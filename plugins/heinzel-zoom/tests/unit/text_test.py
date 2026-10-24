import pytest
from heinzel_zoom._text import ADDRESS, name, without_addresses

pytestmark = pytest.mark.unit


def test_names_are_cleaned_and_never_an_email():
    assert name("  Mira \u202e Holm\t") == "Mira Holm"
    assert name("mira@example.com") is None
    assert name("Mira (mira@example.com)") is None
    assert name("") is None
    assert name(None) is None
    assert name(42) is None


def test_addresses_in_text_are_replaced_and_sentences_keep_their_end():
    assert without_addresses("Write to mira@example.com. Or jo@x.example!") == f"Write to {ADDRESS}. Or {ADDRESS}!"
    assert without_addresses("no address @ all") == "no address @ all"
