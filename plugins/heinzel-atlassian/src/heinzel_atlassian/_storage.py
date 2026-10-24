"""Confluence storage format (XHTML with `ac:` and `ri:` elements) to Markdown-flavoured plain text.

Best effort, standard library only: headings, paragraphs, line breaks, lists, task lists, links (including links to
other pages by title), tables as pipe rows, inline code and code macros as fenced blocks. Images, emoticons, user
mentions and macro parameters are dropped; the text of other macros is kept.

The output is bounded: nesting deeper than `MAX_INDENT` levels is indented no further, and a body whose text would
pass `max_output(len(storage))` characters is refused rather than converted.
"""

import re
from html.parser import HTMLParser
from typing import override

from heinzel import HeinzelError

_BLOCKS = frozenset({"p", "div", "blockquote", "section", "ac:layout-cell", "ac:rich-text-body", "hr"})
_DROPPED = frozenset({"script", "style", "ac:parameter", "ac:task-id", "ac:placeholder", "ac:emoticon"})
_RESETS_IMAGES = _BLOCKS | {"ul", "ol", "li", "table", "tr", "td", "th", "pre", "ac:task-list", "ac:task"}
"""Block tags: an image left open before one of them is over, so an unclosed `<ac:image>` can't hide the rest."""
_CODE_MACROS = frozenset({"code", "noformat"})
_SPACES = re.compile(r"[ \t\r\n\f\v\xa0]+")
_DOUBLE_SPACES = re.compile(r"(?<=\S) {2,}")
_FENCE = re.compile(r"\s*(`{3,})")
_BACKTICKS = re.compile(r"`+")
MAX_INDENT = 16
"""Nesting levels of lists and tasks that are indented; deeper ones stay at this depth."""
MIN_OUTPUT = 1_000_000
MAX_OUTPUT = 20_000_000


def max_output(size: int) -> int:
    """Most characters the text of a body of `size` characters may have: four times the body, 1 MB at least, 20 MB."""
    return min(max(4 * size, MIN_OUTPUT), MAX_OUTPUT)


def _fence(body: str) -> str:
    """A code fence longer than any run of backticks in `body`, three at least."""
    return "`" * max([3, *(len(run) + 1 for run in _BACKTICKS.findall(body))])


class _Converter(HTMLParser):
    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self._limit = limit
        self._written = 0  # every character ever written, rewrites included: an upper bound of the output
        self._sinks: list[list[str]] = [[]]
        self._lists: list[list[int]] = []  # per open list: [is_ordered, next number]
        self._task_lists = 0
        self._dropped = 0
        self._images = 0
        self._pre = 0
        self._pre_start: tuple[int, int] | None = None  # sink depth and sink length where the outermost <pre> opened
        self._macros: list[str] = []
        self._language = ""
        self._parameter = ""
        self._code: list[str] | None = None
        self._rows: list[list[list[str]]] = []  # per open table: its rows of cells
        self._header: list[bool] = []
        self._table_depth: list[tuple[int, int]] = []  # per open table: sink depth and open links when it opened
        self._task_done = False
        self._task_status: list[str] | None = None
        self._link: list[str] = []  # per open link: its href or page title
        self._link_text: list[tuple[int, int]] = []  # per open link: sink depth and sink length when it opened

    # -- output -----------------------------------------------------------------------------------------------------

    def _write(self, text: str) -> None:
        self._written += len(text)
        if self._written > self._limit:
            msg = (
                f"The page's text would pass {self._limit:,} characters; it is too large or too deeply nested to read."
            )
            raise HeinzelError(msg)
        self._sinks[-1].append(text)

    def _indent(self) -> str:
        """Two spaces per level of the list or task list being written, at most `MAX_INDENT` levels."""
        return "  " * min(len(self._lists) + self._task_lists - 1, MAX_INDENT)

    def _break(self, blank: bool = True) -> None:
        """End the line (and leave a blank one), unless the output already does."""
        tail = "".join(self._sinks[-1][-4:]).rstrip(" ")
        if not tail:
            return
        have = len(tail) - len(tail.rstrip("\n"))
        self._write("\n" * max(0, (2 if blank else 1) - have))

    def _at_line_start(self) -> bool:
        tail = "".join(self._sinks[-1][-4:]).rstrip(" ")
        return not tail or tail.endswith("\n")

    def text(self) -> str:
        lines: list[str] = []
        fence = ""  # the open code fence, if any
        for line in "".join(self._sinks[0]).split("\n"):
            found = _FENCE.match(line)
            if not fence and found:
                fence = found.group(1)
            elif fence and found and line.strip() == found.group(1) and len(found.group(1)) >= len(fence):
                fence = ""
                lines.append(line.rstrip())
                continue
            lines.append(line.rstrip() if fence else _DOUBLE_SPACES.sub(" ", line.rstrip()))
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"

    # -- parser callbacks -------------------------------------------------------------------------------------------

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: C901, PLR0912, PLR0915
        named = {key: value or "" for key, value in attrs}
        if tag in _RESETS_IMAGES or re.fullmatch(r"h[1-6]", tag):
            self._images = 0
        if tag == "ac:image":
            self._images += 1
            return
        if tag == "ac:parameter" and self._macros and self._macros[-1] in _CODE_MACROS:
            self._parameter = named.get("ac:name", "")
        if tag in _DROPPED:
            self._dropped += 1
            return
        if tag == "ac:structured-macro":
            self._macros.append(named.get("ac:name", ""))
            if self._macros[-1] in _CODE_MACROS:
                self._language, self._code = "", []
        elif tag == "ac:plain-text-body" and self._code is not None:
            pass
        elif tag in _BLOCKS or tag in {"ul", "ol", "table"}:
            self._break(blank=not (tag in {"ul", "ol"} and self._lists))
            if tag in {"ul", "ol"}:
                self._lists.append([tag == "ol", 1])
            elif tag == "table":
                self._rows.append([])
                self._header.append(False)
                self._table_depth.append((len(self._sinks), len(self._link)))
            elif tag == "hr":
                self._write("---")
                self._break()
        elif re.fullmatch(r"h[1-6]", tag):
            self._break()
            self._write("#" * int(tag[1]) + " ")
        elif tag == "br":
            self._break(blank=False)
        elif tag == "li":
            self._list_item()
        elif tag == "ac:task-list":
            self._task_lists += 1
        elif tag == "ac:task":
            self._task_done = False
        elif tag == "ac:task-status":
            self._task_status = []
        elif tag == "ac:task-body":
            self._break(blank=False)
            self._write(f"{self._indent()}- [{'x' if self._task_done else ' '}] ")
        elif tag == "tr" and self._rows:
            self._end_open_cell()
            self._rows[-1].append([])
        elif tag in {"td", "th"} and self._rows:
            self._end_open_cell()  # a cell left open ends where the next one starts
            self._header[-1] = self._header[-1] or (tag == "th" and len(self._rows[-1]) == 1)
            self._sinks.append([])
        elif tag == "pre":
            self._pre += 1
            if self._pre == 1:
                self._break()
                self._pre_start = (len(self._sinks), len(self._sinks[-1]))
        elif tag == "code" and not self._pre:
            self._write("`")
        elif tag in {"strong", "b"}:
            self._write("**")
        elif tag in {"em", "i"}:
            self._write("_")
        elif tag in {"a", "ac:link"}:
            self._link.append(named.get("href", ""))
            self._link_text.append((len(self._sinks), len(self._sinks[-1])))
        elif tag == "ri:page" and self._link:
            self._link[-1] = self._link[-1] or named.get("ri:content-title", "")
        elif tag == "ri:url" and self._link:
            self._link[-1] = self._link[-1] or named.get("ri:value", "")

    @override
    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DROPPED or tag == "ac:image":  # a self-closing <ac:image/> has no end tag to balance it
            return
        self.handle_starttag(tag, attrs)
        if tag not in {"br", "hr", "ri:page", "ri:url"}:
            self.handle_endtag(tag)

    @override
    def handle_endtag(self, tag: str) -> None:  # noqa: C901, PLR0912
        if tag == "ac:image":
            self._images = max(0, self._images - 1)
            return
        if tag in _DROPPED:
            self._dropped = max(0, self._dropped - 1)
            if tag == "ac:parameter":
                self._parameter = ""
            return
        if tag == "ac:structured-macro" and self._macros:
            if self._macros.pop() in _CODE_MACROS and self._code is not None:
                body = "".join(self._code).strip("\n")
                self._code = None
                self._break()
                fence = _fence(body)
                self._write(f"{fence}{self._language}\n{body}\n{fence}")
                self._break()
        elif tag in _BLOCKS or re.fullmatch(r"h[1-6]", tag):
            self._break()
        elif tag in {"ul", "ol"} and self._lists:
            self._lists.pop()
            self._break(blank=not self._lists)
        elif tag == "ac:task-list" and self._task_lists:
            self._task_lists -= 1
        elif tag == "ac:task-status" and self._task_status is not None:
            self._task_done = "".join(self._task_status).strip() == "complete"
            self._task_status = None
        elif tag in {"td", "th"} and self._rows:
            self._end_open_cell()
        elif tag == "table" and self._rows:
            self._close_table()
        elif tag == "pre" and self._pre:
            self._pre -= 1
            if not self._pre:
                self._close_pre()
        elif tag == "code" and not self._pre:
            self._write("`")
        elif tag in {"strong", "b"}:
            self._write("**")
        elif tag in {"em", "i"}:
            self._write("_")
        elif tag in {"a", "ac:link"} and self._link:
            self._close_link()

    @override
    def handle_data(self, data: str) -> None:
        if self._task_status is not None:
            self._task_status.append(data)
            return
        if self._parameter == "language" and self._macros:
            self._language = data.strip()
            return
        if self._dropped or self._images:
            return
        if self._code is not None:
            self._code.append(data)
        elif self._pre:
            self._write(data)
        else:
            text = _SPACES.sub(" ", data)
            if self._at_line_start():
                text = text.lstrip(" ")
            if text:
                self._write(text)

    @override
    def unknown_decl(self, data: str) -> None:
        # Code macro bodies are `<![CDATA[...]]>`, which HTMLParser hands here and otherwise drops.
        if data.startswith("CDATA["):
            content = data[len("CDATA[") :]
            if self._code is not None:
                self._code.append(content)
            elif not (self._dropped or self._images):
                self._write(content)

    # -- helpers ----------------------------------------------------------------------------------------------------

    def _list_item(self) -> None:
        self._break(blank=False)
        marker = "- "
        if self._lists and self._lists[-1][0]:
            marker = f"{self._lists[-1][1]}. "
            self._lists[-1][1] += 1
        self._write(self._indent() + marker)

    def _close_link(self) -> None:
        target = self._link.pop()
        depth, start = self._link_text.pop()
        if depth != len(self._sinks):  # opened in a table cell that is over: its text stays as it is
            return
        sink = self._sinks[-1]
        content = "".join(sink[start:])
        web = target.startswith(("https://", "http://", "mailto:"))
        if "\n" in content.strip():  # the link wraps blocks, a table say: keep them, then name the target
            if web:
                self._break(blank=False)
                self._write(f"<{target}>")
                self._break()
            return
        label = _SPACES.sub(" ", content).strip()
        del sink[start:]
        if label and target != label and web:
            self._write(f"[{label}]({target})")
        else:
            self._write(label or target)

    def _close_table(self) -> None:
        """Write the table, ending cells and dropping links left open inside it."""
        depth, links = self._table_depth.pop()
        while len(self._sinks) > depth:
            self._end_cell()
        del self._link[links:], self._link_text[links:]
        self._table(self._rows.pop(), self._header.pop())

    def _end_open_cell(self) -> None:
        if len(self._sinks) > self._table_depth[-1][0]:
            self._end_cell()

    def _end_cell(self) -> None:
        while self._link_text and self._link_text[-1][0] >= len(self._sinks):  # links can't outlive their cell
            del self._link[-1], self._link_text[-1]
        cell = _SPACES.sub(" ", "".join(self._sinks.pop()).replace("|", "\\|")).strip()
        if self._rows[-1]:
            self._rows[-1][-1].append(cell)

    def _close_pre(self) -> None:
        """Fence what the outermost <pre> wrote, with a fence longer than any backticks in it."""
        start, self._pre_start = self._pre_start, None
        if start is None or start[0] != len(self._sinks):  # opened in a table cell that is over
            return
        sink = self._sinks[-1]
        body = "".join(sink[start[1] :]).strip("\n")
        del sink[start[1] :]
        fence = _fence(body)
        self._write(f"{fence}\n{body}\n{fence}")
        self._break()

    def _table(self, rows: list[list[str]], header: bool) -> None:
        rows = [row for row in rows if row]
        if not rows:
            return
        width = max(len(row) for row in rows)
        self._break()
        for number, row in enumerate(rows):
            cells = row + [""] * (width - len(row))
            self._write("| " + " | ".join(cells) + " |\n")
            if number == 0 and header:
                self._write("|" + " --- |" * width + "\n")
        self._break()


def storage_to_text(storage: str) -> str:
    """The text of a page body in Confluence storage format, as Markdown-flavoured plain text.

    Raises:
        HeinzelError: If the text would pass `max_output(len(storage))` characters.
    """
    converter = _Converter(max_output(len(storage)))
    converter.feed(storage)
    converter.close()
    return converter.text()
