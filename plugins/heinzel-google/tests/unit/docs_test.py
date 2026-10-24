"""The Docs JSON to text converter: what it keeps, what it drops, and that it stays bounded."""

import json
import time
from typing import Any

import pytest
from heinzel_google._docs import MAX_DEPTH, MAX_INDENT, MIN_OUTPUT, document_to_text, max_output
from hypothesis import given, settings
from hypothesis import strategies as st

from heinzel import HeinzelError

pytestmark = pytest.mark.unit


def run(content: str, **style: object) -> dict[str, Any]:
    return {"textRun": {"content": content, "textStyle": style}}


def paragraph(*runs: object, style: str = "NORMAL_TEXT", bullet: dict[str, Any] | None = None) -> dict[str, Any]:
    """A paragraph of text runs (plain strings become runs); Docs ends every paragraph with a newline."""
    elements = [run(r) if isinstance(r, str) else r for r in runs]
    if elements and "textRun" in elements[-1]:
        elements[-1]["textRun"]["content"] += "\n"
    result: dict[str, Any] = {"paragraph": {"elements": elements, "paragraphStyle": {"namedStyleType": style}}}
    if bullet is not None:
        result["paragraph"]["bullet"] = bullet
    return result


def table(*rows: list[list[object]]) -> dict[str, Any]:
    return {"table": {"tableRows": [{"tableCells": [{"content": cell} for cell in row]} for row in rows]}}


def text(*content: object, lists: dict[str, object] | None = None) -> str:
    document = {"title": "T", "body": {"content": list(content)}, "lists": lists or {}}
    return document_to_text(document, len(json.dumps(document)))


ORDERED = {"listProperties": {"nestingLevels": [{"glyphType": "DECIMAL"}, {"glyphType": "ALPHA"}]}}
BULLETS = {"listProperties": {"nestingLevels": [{"glyphSymbol": "●"}, {"glyphSymbol": "○"}]}}


def test_headings_paragraphs_and_soft_breaks():
    assert (
        text(
            paragraph("Plan", style="TITLE"),
            paragraph("Goal", style="HEADING_2"),
            paragraph("Ship  it\x0bsoon"),
            paragraph(""),
            {"sectionBreak": {}},
            {"tableOfContents": {"content": [paragraph("Goal")]}},
        )
        == "# Plan\n\n## Goal\n\nShip it\nsoon\n"
    )


def test_bold_italic_and_links_keep_surrounding_spaces():
    assert (
        text(
            paragraph(
                run("Read "),
                run("this", bold=True, italic=True),
                run(" and "),
                run("that", link={"url": "https://example.com/a"}),
                run(" or "),
                run("there", link={"headingId": "h.1"}),
                run(" js", link={"url": "javascript:alert(1)"}),
                run(" "),
                run("https://example.com/b", link={"url": "https://example.com/b"}),
            )
        )
        == "Read _**this**_ and [that](https://example.com/a) or there js https://example.com/b\n"
    )


def test_people_by_name_never_email_and_rich_links_by_title():
    assert (
        text(
            paragraph(
                {"person": {"personProperties": {"name": "Ada Example", "email": "ada@example.com"}}},
                run(" and "),
                {"person": {"personProperties": {"email": "grace@example.com"}}},
                run(" see "),
                {"richLink": {"richLinkProperties": {"title": "Roadmap", "uri": "https://docs.google.com/x"}}},
                run(" "),
                {"richLink": {"richLinkProperties": {"uri": "https://docs.google.com/y"}}},
                run(" "),
                {"richLink": {"richLinkProperties": {"title": "Odd", "uri": "file:///etc"}}},
                {"inlineObjectElement": {"inlineObjectId": "kix.1"}},
                {"horizontalRule": {}},
                run("after"),
            )
        )
        == "Ada Example and someone see [Roadmap](https://docs.google.com/x) https://docs.google.com/y Odd\n"
        "---\nafter\n"
    )


def test_numbered_and_bulleted_lists_nest_and_restart():
    numbered = {"listId": "n", "nestingLevel": 0}
    sub = {"listId": "n", "nestingLevel": 1}
    assert (
        text(
            paragraph("one", bullet=numbered),
            paragraph("a", bullet=sub),
            paragraph("b", bullet=sub),
            paragraph("two", bullet=numbered),
            paragraph("a again", bullet=sub),
            paragraph("dot", bullet={"listId": "b"}),
            paragraph("unknown list", bullet={"listId": "missing", "nestingLevel": "x"}),
            paragraph("after"),
            lists={"n": ORDERED, "b": BULLETS},
        )
        == "1. one\n  1. a\n  2. b\n2. two\n  1. a again\n- dot\n- unknown list\n\nafter\n"
    )


def test_list_indentation_is_capped():
    deep = {"listId": "b", "nestingLevel": 10_000_000}
    line = text(paragraph("deep", bullet=deep), lists={"b": BULLETS})
    assert line == "  " * MAX_INDENT + "- deep\n"


def test_multi_line_items_stay_inside_the_item():
    assert text(paragraph("first\x0bsecond", bullet={"listId": "b"}), lists={"b": BULLETS}) == "- first\n  second\n"


def test_tables_become_pipe_rows_and_nested_tables_flatten():
    inner = table([[paragraph("x")], [paragraph("y")]])
    assert (
        text(
            table(
                [[paragraph("Name")], [paragraph("Notes")]],
                [[paragraph("a|b")], [paragraph("line 1"), paragraph("line 2"), inner]],
                [[paragraph("short")]],
            ),
            table(),
        )
        == "| Name | Notes |\n| --- | --- |\n"
        "| a\\|b | line 1 line 2 \\| x \\| y \\| \\| --- \\| --- \\| |\n| short |  |\n"
    )


def test_tabs_are_read_in_order_under_their_titles():
    def tab(title: str, words: str, children: list[object] | None = None) -> dict[str, object]:
        return {
            "tabProperties": {"title": title},
            "documentTab": {"body": {"content": [paragraph(words)]}},
            "childTabs": children or [],
        }

    document = {"tabs": [tab("One", "first", [tab("Child", "nested")]), tab("", "second")]}
    assert document_to_text(document, 100) == "# One\n\nfirst\n\n## Child\n\nnested\n\n# Untitled tab\n\nsecond\n"
    single = {"tabs": [tab("Only", "alone")]}
    assert document_to_text(single, 100) == "alone\n"


def test_nothing_readable_is_an_empty_line():
    assert document_to_text({}, 2) == "\n"
    assert document_to_text({"body": {"content": "nonsense"}, "tabs": None}, 10) == "\n"


def nested_tables(depth: int) -> dict[str, object]:
    element: dict[str, object] = paragraph("core")
    for _ in range(depth):
        element = table([[element]])
    return element


def test_tables_nested_to_the_limit_are_read():
    assert "core" in text(nested_tables(MAX_DEPTH))


def test_tables_nested_deeper_are_refused_quickly():
    started = time.monotonic()
    with pytest.raises(HeinzelError, match="nests tables more than"):
        text(nested_tables(5_000))
    assert time.monotonic() - started < 1


def test_tabs_nested_deeper_are_refused():
    tab: dict[str, object] = {"tabProperties": {"title": "x"}, "documentTab": {}}
    for _ in range(MAX_DEPTH + 1):
        tab = {"tabProperties": {"title": "x"}, "documentTab": {}, "childTabs": [tab]}
    with pytest.raises(HeinzelError, match="nests tabs more than"):
        document_to_text({"tabs": [tab]}, 100)


def test_output_is_bounded_by_the_size_of_the_json():
    assert max_output(10) == MIN_OUTPUT
    assert max_output(10**9) == 20_000_000
    huge = {"body": {"content": [paragraph("x" * (MIN_OUTPUT + 1))]}}
    with pytest.raises(HeinzelError, match="would pass"):
        document_to_text(huge, 10)


# A property: whatever shape the JSON has, the converter returns text or a HeinzelError, within its bound.

leaves = st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=20))
values = st.recursive(
    leaves,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(
            st.sampled_from(
                [
                    "content",
                    "paragraph",
                    "elements",
                    "textRun",
                    "table",
                    "tableRows",
                    "tableCells",
                    "bullet",
                    "listId",
                    "nestingLevel",
                    "paragraphStyle",
                    "namedStyleType",
                    "textStyle",
                    "link",
                    "url",
                    "bold",
                    "person",
                    "personProperties",
                    "name",
                    "richLink",
                    "richLinkProperties",
                    "title",
                    "uri",
                    "tabs",
                    "childTabs",
                    "documentTab",
                    "body",
                    "lists",
                    "tabProperties",
                    "horizontalRule",
                ]
            ),
            children,
            max_size=5,
        ),
    ),
    max_leaves=25,
)


@settings(max_examples=40, deadline=None)
@given(st.dictionaries(st.sampled_from(["body", "tabs", "lists", "title"]), values, max_size=4))
def test_any_json_converts_or_is_refused_within_the_bound(document):
    size = len(json.dumps(document))
    try:
        result = document_to_text(document, size)
    except HeinzelError:
        return
    assert isinstance(result, str)
    assert result.endswith("\n")
    assert len(result) <= max_output(size) + 1


def test_rows_without_cells_are_skipped():
    assert text(table([[paragraph("solo")]], [])) == "| solo |\n| --- |\n"


def test_a_document_author_cannot_forge_structure_through_titles_or_links():
    document = {
        "tabs": [
            {"tabProperties": {"title": "A\n\n# injected\r\nx"}, "documentTab": {"body": {"content": []}}},
            {
                "tabProperties": {"title": "B"},
                "documentTab": {
                    "body": {
                        "content": [
                            paragraph(
                                run("click", link={"url": "https://a.example/x) and\n\n# Heading"}),
                                run(" "),
                                {
                                    "richLink": {
                                        "richLinkProperties": {
                                            "title": "t](https://evil)\n# h",
                                            "uri": "https://a.example/y",
                                        }
                                    }
                                },
                                run(" "),
                                run("mail me", link={"url": "mailto:ada@example.com"}),
                                run(" "),
                                {"person": {"personProperties": {"name": "Ada\n\n# Injected"}}},
                            )
                        ]
                    }
                },
            },
        ]
    }
    text = document_to_text(document, 1000)
    assert (
        text == "# A # injected x\n\n# B\n\nclick [t)(https://evil) # h](https://a.example/y) mail me Ada # Injected\n"
    )
    assert "ada@example.com" not in text
