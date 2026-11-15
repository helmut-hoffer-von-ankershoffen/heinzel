"""The daemon's side of an agent's browser call: caller, limits, the commit gate and the audit log (ADR 0162).

Every tool goes through `Browser.call`: it names the agent from the head's `BrowserCaller`, counts the call against
the agent's limits, runs it over the `BrowserBridge` and logs one `browser.call` event with the actor, tool, tab,
host, outcome and duration. Typed text and page content are never logged, only the length of what was typed.

An input (click, type, key) takes two steps: the desktop describes the target, `decide` judges it, and only then
the desktop acts, and only if the target still has the described fingerprint. A commit by a Heinzel is not run: it
leaves as `BrowserCommitError`, which the chat's tool policy proposes to the person. A commit by an outside agent waits
for the person's click on the card in that tab.
"""

import base64
import binascii
import ipaddress
import logging
import math
import re
import struct
import time
from collections import deque
from collections.abc import Callable, Mapping
from threading import Lock
from typing import Final
from urllib.parse import urlsplit

from pydantic import BaseModel, JsonValue, ValidationError

from heinzel.kernel._browser import (
    QUOTED_NOTICE,
    BrowserBridge,
    BrowserCaller,
    BrowserCommand,
    BrowserCommitError,
    BrowserError,
    PageImage,
    current_browser_caller,
)
from heinzel.kernel._logging import get_logger, log_event
from heinzel.kernel._text import clean_text
from heinzel.modules.ui.browser._gate import Input, Target, chord_key, decide
from heinzel.modules.ui.browser._models import BrowserTab, BrowserTabs, InputDone, PageContent, TabClosed

audit = get_logger("browser")

CALLS_A_MINUTE: Final = 60
NAVIGATIONS_A_MINUTE: Final = 10
WINDOW_SECONDS: Final = 60.0
CONFIRM_SECONDS: Final = 120
"""How long an outside agent's commit waits for the person's click on the card."""
LONGEST_EDGE: Final = 1568
NAME_LIMIT: Final = 60
ROLE_LIMIT: Final = 12
HOST_LIMIT: Final = 60
PREVIEW_LIMIT: Final = 40
"""Together a sentence and its notice stay inside the card's 300 characters without losing the host."""
SUMMARY_LIMIT: Final = 300
"""The desktop's card takes a sentence of 300 characters at most."""
URL_LIMIT: Final = 2048
_PNG: Final = b"\x89PNG\r\n\x1a\n"
_PRIVATE_SUFFIXES: Final = (
    ".local", ".localhost", ".internal", ".intranet", ".lan", ".home", ".corp", ".test", ".invalid", ".onion",
)  # fmt: skip
_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_NUMERIC_LABEL = re.compile(r"(0x[0-9a-f]*|\d+)")
PAGE_CHANGED: Final = "The page changed since this target was described; read it again."
WENT_ELSEWHERE: Final = (
    "Sent, not confirmed: the page went to {url}, which is not where the pressed link led. Read the page before you "
    "act on it."
)
SENT_NOT_CONFIRMED: Final = (
    "Sent, not confirmed: the target is a frame or a place hz cannot see into, so nothing says the click arrived. "
    "Read the page again before you act on it."
)


def public_host(url: str) -> str:
    """The host of a public HTTPS address an agent may open; the desktop checks again with its own policy.

    Raises:
        BrowserError: If the address is not HTTPS, carries credentials, or names a local or literal address.
    """
    refused = "The browser opens public https:// addresses only: no local names, literal addresses or credentials."
    if len(url) > URL_LIMIT or url != clean_text(url) or any(character.isspace() for character in url):
        raise BrowserError(refused, code="refused_url")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").rstrip(".").lower()
        _ = parts.port
    except ValueError:
        raise BrowserError(refused, code="refused_url") from None
    labels = host.split(".")
    if (
        parts.scheme != "https"
        or parts.username is not None
        or parts.password is not None
        or len(labels) < 2  # noqa: PLR2004 - a public name has a dot
        or not all(_LABEL.fullmatch(label) for label in labels)
        or _NUMERIC_LABEL.fullmatch(labels[-1])
        or host.endswith(_PRIVATE_SUFFIXES)
        or _is_address(host)
    ):
        raise BrowserError(refused, code="refused_url")
    return host


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def host_of(url: object) -> str | None:
    """The host of an address the desktop reported, for the audit log; never the path or the query."""
    if not isinstance(url, str):
        return None
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


class Limits:
    """Per agent: 60 calls and 10 navigations a minute; beyond that a call is refused with the wait."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Count against `clock`."""
        self._clock = clock
        self._lock = Lock()
        self._calls: dict[str, deque[float]] = {}
        self._navigations: dict[str, deque[float]] = {}

    def count(self, actor: str, *, navigation: bool) -> None:
        """Count one call, and one navigation if it is one.

        Raises:
            BrowserError: If the agent is over a limit; the message says how long to wait.
        """
        now = self._clock()
        with self._lock:
            calls = self._window(self._calls, actor, now)
            navigations = self._window(self._navigations, actor, now)
            if len(calls) >= CALLS_A_MINUTE:
                raise BrowserError(self._wait("browser calls", CALLS_A_MINUTE, calls[0], now), code="rate")
            if navigation and len(navigations) >= NAVIGATIONS_A_MINUTE:
                raise BrowserError(self._wait("navigations", NAVIGATIONS_A_MINUTE, navigations[0], now), code="rate")
            calls.append(now)
            if navigation:
                navigations.append(now)

    def follow(self, actor: str) -> None:
        """Count a navigation that is part of a call already counted: a click that follows a link.

        Raises:
            BrowserError: If the agent is over its navigations; the message says how long to wait.
        """
        now = self._clock()
        with self._lock:
            navigations = self._window(self._navigations, actor, now)
            if len(navigations) >= NAVIGATIONS_A_MINUTE:
                raise BrowserError(self._wait("navigations", NAVIGATIONS_A_MINUTE, navigations[0], now), code="rate")
            navigations.append(now)

    @staticmethod
    def _window(windows: dict[str, deque[float]], actor: str, now: float) -> deque[float]:
        window = windows.setdefault(actor, deque())
        while window and now - window[0] >= WINDOW_SECONDS:
            window.popleft()
        return window

    @staticmethod
    def _wait(what: str, limit: int, oldest: float, now: float) -> str:
        seconds = max(1, math.ceil(WINDOW_SECONDS - (now - oldest)))
        return f"Too many {what}: {limit} a minute at most. Wait {seconds} seconds, then go on."


def follows_link(action: Input, target: Target, keys: str) -> bool:
    """Whether the input opens a link: a click on one, or Enter on a focused one."""
    link = target.role.casefold() == "link" or target.tag.casefold() == "a"
    return link and (action == "click" or (action == "key" and chord_key(keys) in {"enter", "return", "numpadenter"}))


_DOUBLE_QUOTES: Final = '"\u201c\u201d\u201e\u201f\u00ab\u00bb\u2039\u203a\uff02\u301d\u301e\u301f'
"""Every mark a reader takes for a quotation mark: straight, curly, low, angle, full-width and CJK ones."""
_QUOTES = str.maketrans(dict.fromkeys(_DOUBLE_QUOTES, "'"))
_BRACKETS = str.maketrans({"(": "[", ")": "]"})


def quoted(text: str, limit: int) -> str:
    """Page or agent text made safe to stand inside the card's quotes: one line, cut, and with no double quote.

    A name such as `Cancel" on hz.example "Keep` could otherwise close the quote and add words of its own to the
    sentence the person reads; every kind of double quote becomes a single one.
    """
    line = " ".join(clean_text(text).split()).translate(_QUOTES)
    return line if len(line) <= limit else line[: limit - 1] + "…"


def label(target: Target) -> str:
    """A target as one short phrase for the person and the agent: `button "Send"`; page text, cleaned and cut."""
    name = quoted(target.name, NAME_LIMIT)
    kind = quoted(target.role or target.tag, ROLE_LIMIT).replace("…", "") or "element"
    return f'{kind} "{name}"' if name else kind


def summary(action: Input, target: Target, tab: str, *, text: str, keys: str) -> str:
    """The sentence on the confirmation card: what would happen, where, and how typed text starts."""
    sentence = _sentence(action, target, tab, text=text, keys=keys)
    return f"{sentence[: SUMMARY_LIMIT - len(QUOTED_NOTICE) - 2]}. {QUOTED_NOTICE}"


def _sentence(action: Input, target: Target, tab: str, *, text: str, keys: str) -> str:
    if not target.resolved:
        return f"Click at a point hz cannot identify in browser tab {tab}"
    where = f"on {quoted(target.host, HOST_LIMIT) or 'this page'} (browser tab {tab})"
    if action == "click":
        return f"Click {label(target)} {where}"
    if action == "type":
        start = quoted(text, PREVIEW_LIMIT).translate(_BRACKETS)  # it stands in brackets: it cannot close them
        return f"Type {len(text)} characters (starting: {start}) into {label(target)} {where}"
    return f"Press {keys} in {label(target)} {where}"


def png_size(data: str) -> tuple[int, int]:
    """Width and height of a base64 PNG, read from its header.

    Raises:
        BrowserError: If it is no PNG or its longest edge is over the limit.
    """
    try:
        head = base64.b64decode(data[:64], validate=True)
    except (binascii.Error, ValueError):
        head = b""
    if len(head) < 24 or not head.startswith(_PNG) or head[12:16] != b"IHDR":  # noqa: PLR2004 - signature and IHDR
        msg = "The desktop app sent no usable picture of the tab."
        raise BrowserError(msg, code="failed")
    width, height = struct.unpack(">II", head[16:24])
    if not 0 < max(width, height) <= LONGEST_EDGE or min(width, height) <= 0:
        msg = f"The picture of the tab is larger than {LONGEST_EDGE} pixels; nothing was returned."
        raise BrowserError(msg, code="failed")
    return width, height


class _Described(BaseModel):
    target: Target
    fingerprint: str


class _Page(BaseModel):
    url: str = ""
    title: str = ""
    content: str = ""


class _Sent(BaseModel):
    url: str = ""
    confirmed: bool = True
    """False for a click into a frame or at a place the desktop cannot see into: sent, and nothing says it arrived."""
    navigated: bool = False
    """True when the page was another one before the click could be seen; with `confirmed` false, not the link's own."""


class _Picture(BaseModel):
    url: str = ""
    data: str


class Browser:
    """An agent's calls into the built-in browser, each one limited, gated and logged."""

    def __init__(self, bridge: BrowserBridge, clock: Callable[[], float] = time.monotonic) -> None:
        """Call the desktop over `bridge`; `clock` times limits and durations."""
        self._bridge = bridge
        self._clock = clock
        self._limits = Limits(clock)

    def call[T](
        self,
        tool: str,
        tab: str | None,
        work: Callable[[BrowserCaller, dict[str, object]], T],
        *,
        navigation: bool = False,
        typed: int | None = None,
    ) -> T:
        """Run one tool call for the current agent and log it once, whatever happens.

        `work` may set `host` in the fields it is given. The log never holds typed text or page content.

        Raises:
            BrowserError: If there is no caller or no desktop, a limit is reached, or the desktop refuses.
            BrowserCommitError: If a Heinzel's input is a commit; it was not run.
        """
        started = self._clock()
        fields: dict[str, object] = {"host": None}
        actor: str | None = None
        outcome = "refused"
        try:
            caller = current_browser_caller()
            actor = caller.actor
            if not caller.approved:  # a commit the person confirmed is theirs: it never waits on the agent's pace
                self._limits.count(actor, navigation=navigation)
            result = work(caller, fields)
            outcome = "unconfirmed" if fields.get("unconfirmed") else "ran"
        except BrowserCommitError:
            outcome = "proposed"
            raise
        except BrowserError as exc:
            outcome = "paused" if exc.code == "paused" else "refused"
            fields["reason"] = exc.code
            raise
        except ValidationError:
            fields["reason"] = "invalid_answer"
            msg = "The desktop app answered in a way hz does not understand; update the app."
            raise BrowserError(msg, code="failed") from None
        finally:
            log_event(
                audit,
                "browser.call",
                level=logging.INFO if outcome in {"ran", "proposed", "unconfirmed"} else logging.WARNING,
                actor=actor,
                tool=tool,
                tab=tab,
                host=fields.get("host"),
                outcome=outcome,
                reason=fields.get("reason"),
                typed_length=typed,
                duration_ms=round((self._clock() - started) * 1000),
            )
        return result

    def _send(
        self, caller: BrowserCaller, action: str, tab: str | None, arguments: Mapping[str, JsonValue] | None = None
    ) -> dict[str, JsonValue]:
        sent = {name: value for name, value in (arguments or {}).items() if value is not None}
        command = BrowserCommand(actor=caller.actor, kind=caller.kind, action=action, tab=tab, arguments=sent)
        return self._bridge.request(command)

    def tabs(self) -> BrowserTabs:
        """The caller's tabs, or `available: false` without the desktop app."""

        def work(caller: BrowserCaller, _: dict[str, object]) -> BrowserTabs:
            if not self._bridge.available:
                return BrowserTabs(available=False)
            return BrowserTabs.model_validate({"available": True, "tabs": self._send(caller, "tabs", None)["tabs"]})

        return self.call("ui_browser_tabs", None, work)

    def open(self, url: str | None) -> BrowserTab:
        """Open a new tab of the caller's, at `url` if given."""

        def work(caller: BrowserCaller, _: dict[str, object]) -> BrowserTab:
            return BrowserTab.model_validate(self._send(caller, "open", None, {"url": url}))

        return self._checked_address("ui_browser_open", None, url, work)

    def navigate(self, tab: str, url: str | None, to: str | None) -> BrowserTab:
        """Go to `url`, or back, forward or reload."""

        def work(caller: BrowserCaller, fields: dict[str, object]) -> BrowserTab:
            if (url is None) == (to is None):
                msg = "Give either `url` or `to`, not both."
                raise BrowserError(msg, code="invalid")
            answer = BrowserTab.model_validate(self._send(caller, "navigate", tab, {"url": url, "to": to}))
            fields["host"] = host_of(answer.url) or fields["host"]
            return answer

        return self._checked_address("ui_browser_navigate", tab, url, work)

    def _checked_address[T](
        self,
        tool: str,
        tab: str | None,
        url: str | None,
        work: Callable[[BrowserCaller, dict[str, object]], T],
    ) -> T:
        """Run a call that opens an address; a refused address is logged like any other refused call."""

        def checked(caller: BrowserCaller, fields: dict[str, object]) -> T:
            if url is not None:
                fields["host"] = host_of(url)  # logged also when the address is refused
                fields["host"] = public_host(url)
            return work(caller, fields)

        return self.call(tool, tab, checked, navigation=url is not None or tool == "ui_browser_navigate")

    def close(self, tab: str) -> TabClosed:
        """Close one of the caller's tabs."""

        def work(caller: BrowserCaller, _: dict[str, object]) -> TabClosed:
            self._send(caller, "close", tab)
            return TabClosed(tab=tab)

        return self.call("ui_browser_close", tab, work)

    def page(self, tool: str, action: str, tab: str, max_chars: int, arguments: Mapping[str, JsonValue]) -> PageContent:
        """Read a tab: `read` gives the accessibility tree with refs, `text` the visible text."""

        def work(caller: BrowserCaller, fields: dict[str, object]) -> PageContent:
            page = _Page.model_validate(self._send(caller, action, tab, {**arguments, "max_chars": max_chars}))
            fields["host"] = host_of(page.url)
            content = clean_text(page.content)
            return PageContent(
                tab=tab,
                url=clean_text(page.url)[:URL_LIMIT],
                title=clean_text(page.title)[:200],
                truncated=len(content) > max_chars,
                content=content[:max_chars],
            )

        return self.call(tool, tab, work)

    def screenshot(self, tab: str, scale: float) -> PageImage:
        """A PNG of the tab only."""

        def work(caller: BrowserCaller, fields: dict[str, object]) -> PageImage:
            picture = _Picture.model_validate(self._send(caller, "screenshot", tab, {"scale": scale}))
            fields["host"] = host_of(picture.url)
            width, height = png_size(picture.data)
            return PageImage(
                tab=tab, url=clean_text(picture.url)[:URL_LIMIT], width=width, height=height, data=picture.data
            )

        return self.call("ui_browser_screenshot", tab, work)

    def scroll(self, tab: str, arguments: Mapping[str, JsonValue]) -> InputDone:
        """Scroll the page or one element; it changes what is shown, not what the site stores."""

        def work(caller: BrowserCaller, fields: dict[str, object]) -> InputDone:
            page = _Page.model_validate(self._send(caller, "scroll", tab, arguments))
            fields["host"] = host_of(page.url)
            return InputDone(tab=tab, url=clean_text(page.url)[:URL_LIMIT])

        return self.call("ui_browser_scroll", tab, work)

    def input(  # noqa: PLR0913 - one input: where, what, and the fingerprint a proposal pinned
        self,
        action: Input,
        tab: str,
        where: Mapping[str, JsonValue],
        what: Mapping[str, JsonValue],
        expected: str | None,
        *,
        text: str = "",
        keys: str = "",
    ) -> InputDone:
        """Describe the target, run the gate, then act on exactly that target.

        Raises:
            BrowserCommitError: If a Heinzel's input is a commit; it was not run and must be proposed.
            BrowserError: If the target is on the never-list, changed, or the person did not confirm.
        """

        def work(caller: BrowserCaller, fields: dict[str, object]) -> InputDone:
            described = _Described.model_validate(self._send(caller, "describe", tab, {"action": action, **where}))
            target, fingerprint = described.target, described.fingerprint
            fields["host"] = target.host or None
            verdict = decide(action, target, text=text, keys=keys)
            if verdict.outcome == "never":
                msg = f"Not done: {verdict.reason}."
                raise BrowserError(msg, code="never")
            if not fingerprint or (expected is not None and expected != fingerprint):
                raise BrowserError(PAGE_CHANGED, code="changed")
            if caller.approved:
                if expected is None:  # only a proposal made here carries the fingerprint the person saw
                    raise BrowserError(PAGE_CHANGED, code="changed")
            elif verdict.outcome == "commit":
                sentence = summary(action, target, tab, text=text, keys=keys)
                if caller.kind == "heinzel":
                    raise BrowserCommitError(fingerprint, sentence)
                self._ask_the_person(caller, tab, sentence, fingerprint)
            if follows_link(action, target, keys) and not caller.approved:
                self._limits.follow(caller.actor)  # walking links is navigating, at the same 10 a minute
            sent = _Sent.model_validate(self._send(caller, action, tab, {**where, **what, "expect": fingerprint}))
            url = clean_text(sent.url)[:URL_LIMIT]
            if not sent.confirmed:  # never told as done: the agent reads the page before it builds on this
                fields["unconfirmed"] = True
                note = WENT_ELSEWHERE.format(url=url) if sent.navigated else SENT_NOT_CONFIRMED
                return InputDone(
                    tab=tab, url=url, target=label(target), done=False, navigated=sent.navigated, note=note
                )
            return InputDone(tab=tab, url=url, target=label(target), navigated=sent.navigated)

        return self.call(f"ui_browser_{action}", tab, work, typed=len(text) if action == "type" else None)

    def _ask_the_person(self, caller: BrowserCaller, tab: str, sentence: str, fingerprint: str) -> None:
        """An outside agent's commit: the card on that tab, and the call waits for the person's click.

        Raises:
            BrowserError: If the person declines or does not answer in time.
        """
        try:
            answer = self._send(
                caller, "confirm", tab, {"summary": sentence, "fingerprint": fingerprint, "seconds": CONFIRM_SECONDS}
            )
        except BrowserError as exc:
            if exc.code != "timeout":
                raise
            answer = {}
        if answer.get("approved") is not True:
            msg = f"Not run: the person did not confirm it in the browser. ({sentence})"
            raise BrowserError(msg, code="refused")
