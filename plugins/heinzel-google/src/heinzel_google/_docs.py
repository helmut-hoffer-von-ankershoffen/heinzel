"""A Google Docs document (the Docs API's JSON) to Markdown-flavoured plain text.

Best effort, standard library only: tabs, headings, paragraphs, soft line breaks, bold and italic, links, bulleted and
numbered lists with their nesting, tables as pipe rows (a table in a cell is flattened into the cell), person chips by
name (never their email) and rich links by title. Images, drawings, footnotes, equations and the table of contents
are dropped.

The output is bounded: every character ever written is counted against `max_output(size)` of the JSON it came from,
lists are indented at most `MAX_INDENT` levels, and tables in cells or tabs in tabs nested deeper than `MAX_DEPTH`
are refused, so neither recursion nor the copying of cell text into an enclosing cell can grow without bound.
"""

import re
from typing import Any, cast

from heinzel import HeinzelError

MAX_INDENT = 16
"""Nesting levels of lists that are indented; deeper ones stay at this depth."""
MAX_DEPTH = 8
"""Tables nested in table cells, or tabs nested in tabs, read; deeper is refused."""
MIN_OUTPUT = 1_000_000
MAX_OUTPUT = 20_000_000
_HEADINGS = {"TITLE": 1, **{f"HEADING_{n}": n for n in range(1, 7)}}
_UNORDERED = frozenset({"", "GLYPH_TYPE_UNSPECIFIED", "NONE"})
_SPACES = re.compile(r"[ \t]+")
_LINK = ("https://", "http://")
_SAFE_URL = re.compile(r"^https?://[^\s()<>\[\]]+$")
"""A link target that can't end the Markdown link it is put in, or start a line: no whitespace, brackets or parens.
`mailto:` links keep their text only: a document's people stay names, as chips do."""


def _url(value: object) -> str | None:
    url = str(value or "")
    return url if _SAFE_URL.fullmatch(url) else None


def _line(value: object) -> str:
    """One line of text: whitespace collapsed, so a title can't start a heading or a block of its own."""
    return " ".join(str(value or "").split())


type Json = dict[str, Any]


def max_output(size: int) -> int:
    """Most characters the text of a document of `size` bytes of JSON may have: four times, 1 MB at least, 20 MB."""
    return min(max(4 * size, MIN_OUTPUT), MAX_OUTPUT)


def _objects(value: object) -> list[Json]:
    """The objects in a JSON list; anything else is no objects."""
    if not isinstance(value, list):
        return []
    return [cast("Json", item) for item in cast("list[object]", value) if isinstance(item, dict)]


def _object(value: object) -> Json:
    return cast("Json", value) if isinstance(value, dict) else {}


class _Writer:
    """Counts every character written against a limit, across the whole document."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.written = 0

    def count(self, text: str) -> str:
        self.written += len(text)
        if self.written > self.limit:
            msg = f"The document's text would pass {self.limit:,} characters; it is too large to read."
            raise HeinzelError(msg)
        return text


class _Body:
    """Renders one body (a tab's, or a table cell's) with the lists it refers to."""

    def __init__(self, writer: _Writer, lists: Json, depth: int) -> None:
        self._writer = writer
        self._lists = lists
        self._depth = depth
        self._numbers: dict[str, list[int]] = {}  # per list: the last number per level

    def render(self, content: object) -> list[tuple[bool, str]]:
        """Blocks of text, one per paragraph or table, in order, each with whether it is a list item."""
        blocks: list[tuple[bool, str]] = []
        for element in _objects(content):
            if "paragraph" in element:
                paragraph = _object(element["paragraph"])
                block = self._paragraph(paragraph)
                item = bool(paragraph.get("bullet"))
            elif "table" in element:
                block, item = self._table(_object(element["table"])), False
            else:  # sectionBreak, tableOfContents: nothing to read
                continue
            if block is not None:
                blocks.append((item, block))
        return blocks

    def _paragraph(self, paragraph: Json) -> str | None:
        text = "".join(self._element(e) for e in _objects(paragraph.get("elements")))
        text = text.removesuffix("\n").replace("\x0b", "\n")  # a vertical tab is a soft line break in Docs
        lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
        text = "\n".join(lines).strip("\n")
        bullet = _object(paragraph.get("bullet"))
        if bullet:
            return self._writer.count(self._item(bullet, text))
        if not text:
            return None
        style = str(_object(paragraph.get("paragraphStyle")).get("namedStyleType", ""))
        if style in _HEADINGS:
            text = "#" * _HEADINGS[style] + " " + text.replace("\n", " ")
        return self._writer.count(text)

    def _item(self, bullet: Json, text: str) -> str:
        list_id = str(bullet.get("listId", ""))
        level = bullet.get("nestingLevel", 0)
        level = level if isinstance(level, int) and level >= 0 else 0
        levels = _objects(_object(_object(self._lists.get(list_id)).get("listProperties")).get("nestingLevels"))
        glyph = _object(levels[level]) if level < len(levels) else {}
        ordered = str(glyph.get("glyphType", "")) not in _UNORDERED and not glyph.get("glyphSymbol")
        level = min(level, MAX_INDENT)  # deeper levels are numbered and indented as the deepest one
        counters = self._numbers.setdefault(list_id, [])
        del counters[level + 1 :]  # a shallower item restarts the deeper levels' numbering
        counters += [0] * (level + 1 - len(counters))
        counters[level] += 1
        number = counters[level]
        marker = f"{number}." if ordered else "-"
        indent = "  " * level
        return f"{indent}{marker} " + text.replace("\n", "\n" + indent + "  ")

    def _element(self, element: Json) -> str:
        if "textRun" in element:
            return self._run(_object(element["textRun"]))
        if "person" in element:  # a person chip: the name, never the email address (ADR 0024)
            properties = _object(_object(element["person"]).get("personProperties"))
            return _line(properties.get("name")) or "someone"
        if "richLink" in element:
            properties = _object(_object(element["richLink"]).get("richLinkProperties"))
            title = _line(properties.get("title")).replace("[", "(").replace("]", ")")
            uri = _url(properties.get("uri"))
            if uri:
                return f"[{title}]({uri})" if title else uri
            return title
        if "horizontalRule" in element:
            return "\n---\n"
        return ""  # inline objects, footnote references, equations, auto text, page breaks

    @staticmethod
    def _run(run: Json) -> str:
        content = str(run.get("content", ""))
        core = content.strip()
        if not core:
            return content
        style = _object(run.get("textStyle"))
        url = _url(_object(style.get("link")).get("url")) or ""
        marked = core
        if style.get("bold") is True:
            marked = f"**{marked}**"
        if style.get("italic") is True:
            marked = f"_{marked}_"
        if url and url != core:
            marked = f"[{marked}]({url})"
        start = content.index(core[0])
        return content[:start] + marked + content[start + len(core) :]

    def _table(self, table: Json) -> str | None:
        if self._depth >= MAX_DEPTH:
            msg = f"The document nests tables more than {MAX_DEPTH} deep; it is too deeply nested to read."
            raise HeinzelError(msg)
        rows: list[list[str]] = []
        for row in _objects(table.get("tableRows")):
            cells: list[str] = []
            for cell in _objects(row.get("tableCells")):
                inner = _Body(self._writer, self._lists, self._depth + 1).render(cell.get("content"))
                text = " ".join(" ".join(block for _, block in inner).split())
                cells.append(self._writer.count(text.replace("|", "\\|")))
            if cells:
                rows.append(cells)
        if not rows:
            return None
        width = max(len(cells_of_row) for cells_of_row in rows)
        lines = []
        for number, cells_of_row in enumerate(rows):
            lines.append("| " + " | ".join(cells_of_row + [""] * (width - len(cells_of_row))) + " |")
            if number == 0:
                lines.append("|" + " --- |" * width)
        return self._writer.count("\n".join(lines))


def _tabs(document: Json) -> list[tuple[str, Json, int]]:
    """Every tab in reading order, with its title and depth; one untitled tab for a document read without tabs.

    Raises:
        HeinzelError: If tabs nest deeper than `MAX_DEPTH`.
    """
    if "tabs" not in document:
        return [("", document, 0)]
    found: list[tuple[str, Json, int]] = []
    pending = [(tab, 0) for tab in reversed(_objects(document["tabs"]))]
    while pending:
        tab, depth = pending.pop()
        if depth >= MAX_DEPTH:
            msg = f"The document nests tabs more than {MAX_DEPTH} deep; it is too deeply nested to read."
            raise HeinzelError(msg)
        title = _line(_object(tab.get("tabProperties")).get("title"))
        found.append((title, _object(tab.get("documentTab")), depth))
        pending += [(child, depth + 1) for child in reversed(_objects(tab.get("childTabs")))]
    return found


def document_to_text(document: Json, size: int) -> str:
    """The text of a Docs API document, as Markdown-flavoured plain text.

    Args:
        document: The answer of `documents.get`, with or without `includeTabsContent`.
        size: Bytes of JSON it was parsed from, which bounds the output (`max_output`).

    Raises:
        HeinzelError: If the text would pass `max_output(size)` characters, or tables or tabs nest too deeply.
    """
    writer = _Writer(max_output(size))
    tabs = _tabs(document)
    blocks: list[tuple[bool, str]] = []
    for title, tab, depth in tabs:
        if len(tabs) > 1:
            blocks.append((False, writer.count("#" * min(depth + 1, 6) + " " + (title or "Untitled tab"))))
        body = _Body(writer, _object(tab.get("lists")), 0)
        blocks += body.render(_object(tab.get("body")).get("content"))
    parts: list[str] = []
    for number, (item, block) in enumerate(blocks):
        if number:
            parts.append("\n" if item and blocks[number - 1][0] else "\n\n")  # a list's items stay together
        parts.append(block)
    return re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip("\n") + "\n"  # a first item keeps its indent
