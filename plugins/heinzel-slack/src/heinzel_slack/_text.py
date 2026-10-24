"""Slack's message markup to text people and agents read, and text to markup that pages nobody.

Slack writes mentions, channel links, broadcasts and links in angle brackets (`<@U0123>`, `<#C0123|general>`,
`<!channel>`, `<https://example.com|label>`) and escapes `&`, `<` and `>` everywhere else
(docs.slack.dev/messaging/formatting-message-text).
"""

import re
from collections.abc import Callable, Mapping

from heinzel import clean_text

MENTIONS = re.compile(r"<@([UW][A-Z0-9]{2,30})(?:\|[^<>]*)?>")
"""A user mention; group 1 is the user id."""
_CONTROL = re.compile(r"<([^<>\n]{1,4000})>")
_BROADCAST = re.compile(r"@(channel|here|everyone)\b", re.IGNORECASE)
_LINK = re.compile(r"\[([^\[\]\n]{1,500})\]\((https?://[^\s()<>|]{1,2000})\)")
_BOLD = re.compile(r"\*\*([^*\n]{1,2000})\*\*")
_STRIKE = re.compile(r"~~([^~\n]{1,2000})~~")
_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_BROADCASTS = {"channel": "@channel", "here": "@here", "everyone": "@everyone"}
WORD_JOINER = "⁠"
"""Invisible, and keeps Slack from reading `@channel` as a broadcast; `clean_text` removes it on the way back."""


def to_text(markup: str, names: Mapping[str, str]) -> str:
    """Slack's `markup` as plain text with Markdown links: mentions as `@name` from `names` by user id, cleaned.

    A user missing from `names` becomes `@someone`, never the raw id; a channel `#name` from its label.
    """

    def control(found: re.Match[str]) -> str:
        target, _, label = found.group(1).partition("|")
        return _control(target, label, names)

    text = _CONTROL.sub(control, markup)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")  # `&` last: `&amp;lt;` is `&lt;`
    return clean_text(text)


def _control(target: str, label: str, names: Mapping[str, str]) -> str:  # noqa: PLR0911 - one return per kind
    if target.startswith("@"):
        return f"@{names.get(target[1:]) or 'someone'}"
    if target.startswith("#"):
        return f"#{label or 'channel'}"
    if target.startswith("!subteam^"):
        return label or "@group"
    if target.startswith("!date^"):
        return label
    if target.startswith("!"):
        return _BROADCASTS.get(target[1:], label)
    if label:
        return f"[{label}]({target})"
    return target.removeprefix("mailto:")


def to_markup(text: str) -> str:
    """Plain text or Markdown as Slack markup that mentions and pages no one.

    `&`, `<` and `>` are escaped, so no `<!channel>`, `<!here>`, `<@U…>` or `<#C…>` survives as markup; a written
    `@channel`, `@here` or `@everyone` gets an invisible word joiner after the `@`. Markdown links to http(s) URLs,
    `**bold**`, `~~strike~~` and headings become their Slack forms; everything else stays as written.
    """
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped = _BROADCAST.sub(lambda found: f"@{WORD_JOINER}{found.group(1)}", escaped)
    converters: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
        (_LINK, lambda found: f"<{found.group(2)}|{found.group(1).replace('|', '/')}>"),
        (_BOLD, lambda found: f"*{found.group(1)}*"),
        (_STRIKE, lambda found: f"~{found.group(1)}~"),
        (_HEADING, lambda found: f"*{found.group(1)}*"),
    ]
    for pattern, convert in converters:
        escaped = pattern.sub(convert, escaped)
    return escaped
