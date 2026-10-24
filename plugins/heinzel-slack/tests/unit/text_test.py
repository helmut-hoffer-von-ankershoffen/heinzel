import pytest
from heinzel_slack._text import WORD_JOINER, to_markup, to_text

pytestmark = pytest.mark.unit

NAMES = {"U0ADA": "Ada"}


@pytest.mark.parametrize(
    ("markup", "text"),
    [
        ("<@U0ADA> said hi", "@Ada said hi"),
        ("<@U0ADA|ada> and <@W0NOBODY>", "@Ada and @someone"),
        ("in <#C0123456789|widgets> and <#C0123456789>", "in #widgets and #channel"),
        ("<!channel> <!here> <!everyone>", "@channel @here @everyone"),
        ("<!subteam^S0123|@design> and <!subteam^S0123>", "@design and @group"),
        ("due <!date^1790812800^{date}|Oct 1>", "due Oct 1"),
        ("<!unknown|label> <!unknown>", "label "),
        ("see <https://acme.example/a?b=1&amp;c=2|the plan>", "see [the plan](https://acme.example/a?b=1&c=2)"),
        (
            "<https://acme.example> <mailto:ada@acme.example|Ada> <mailto:ada@acme.example>",
            "https://acme.example [Ada](mailto:ada@acme.example) ada@acme.example",
        ),
        ("1 &lt; 2 &amp;&amp; 3 &gt; 2, &amp;lt; stays", "1 < 2 && 3 > 2, &lt; stays"),
        ("bidi \u202egnp.exe\u202c tag \U000e0041 zero\u200bwidth\r\nline", "bidi gnp.exe tag  zerowidth\nline"),
    ],
)
def test_slack_markup_becomes_clean_text_with_names(markup, text):
    assert to_text(markup, NAMES) == text


def test_names_are_never_raw_ids_even_unknown_ones():
    assert "U0GRACE" not in to_text("<@U0GRACE>", {})


@pytest.mark.parametrize(
    ("text", "markup"),
    [
        ("<!channel> and <!here>", "&lt;!channel&gt; and &lt;!here&gt;"),
        ("<@U0ADA> <#C0123456789|x> <!subteam^S1>", "&lt;@U0ADA&gt; &lt;#C0123456789|x&gt; &lt;!subteam^S1&gt;"),
        ("@channel, @HERE and @everyone!", f"@{WORD_JOINER}channel, @{WORD_JOINER}HERE and @{WORD_JOINER}everyone!"),
        ("@channels and ada@here.example", f"@channels and ada@{WORD_JOINER}here.example"),
        ("a & b", "a &amp; b"),
        ("[plan](https://acme.example/p?a=1&b=2)", "<https://acme.example/p?a=1&amp;b=2|plan>"),
        ("[a|b](https://acme.example)", "<https://acme.example|a/b>"),
        ("[x](javascript:alert(1))", "[x](javascript:alert(1))"),
        ("[<!channel>](https://acme.example)", "<https://acme.example|&lt;!channel&gt;>"),
        ("**bold** and ~~gone~~", "*bold* and ~gone~"),
        ("# Weekly digest ##\n## Done\ntext #3", "*Weekly digest*\n*Done*\ntext #3"),
    ],
)
def test_text_becomes_markup_that_mentions_and_pages_no_one(text, markup):
    assert to_markup(text) == markup


def test_markup_round_trips_to_the_text_written():
    text = "<!channel> & @here: [plan](https://acme.example)"
    assert to_text(to_markup(text), {}) == "<!channel> & @here: [plan](https://acme.example)"
