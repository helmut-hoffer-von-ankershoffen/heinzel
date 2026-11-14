"""What the browser tools return. Everything a page says travels with the notice that it is untrusted data."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from heinzel.kernel._browser import WEBSITE_NOTICE


class BrowserTab(BaseModel):
    """One tab an agent opened. `notice` comes first, so a cut result still carries it."""

    model_config = ConfigDict(extra="ignore")

    notice: str = WEBSITE_NOTICE
    id: str
    url: str = ""
    title: str = ""
    status: str = ""
    paused: bool = False
    """True while the person has taken the tab over; calls on it wait for Hand back."""


class BrowserTabs(BaseModel):
    """The caller's own tabs; the person's tabs and other agents' tabs are never listed."""

    notice: str = WEBSITE_NOTICE
    available: bool
    """False without the desktop app: there is no built-in browser then."""
    tabs: list[BrowserTab] = []


class TabClosed(BaseModel):
    """A tab that is gone."""

    tab: str
    closed: Literal[True] = True


class PageContent(BaseModel):
    """What a page shows: its accessibility tree with `ref_N` handles, or its visible text."""

    notice: str = WEBSITE_NOTICE
    tab: str
    url: str
    title: str
    truncated: bool
    content: str


class InputDone(BaseModel):
    """An input that ran: a scroll, a click, typing or a key."""

    notice: str = WEBSITE_NOTICE
    tab: str
    url: str
    done: Literal[True] = True
    target: str = ""
    """What it hit, as the page names it, e.g. `button "Next"`."""
