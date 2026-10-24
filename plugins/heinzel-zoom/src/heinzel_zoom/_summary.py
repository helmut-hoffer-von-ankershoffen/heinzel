"""Reading one Zoom meeting-summary email: the meeting it is about and the sections of the summary.

Zoom's AI Companion mails a summary after a meeting: a subject such as `Meeting summary for Weekly sync (10/20/2026)`
or `Meeting assets for Weekly sync are ready!`, and a body of headed sections (`Quick recap`, `Next steps`,
`Summary` with a heading per topic), followed by the meeting ID and a footer. The wording varies between Zoom's
versions and accounts, so the parser is lenient: it recognises the subject, takes what it finds, ignores what it
doesn't know, and never fails on the body. A message that isn't a summary is not one (`None`), never an error.

Every string passes `clean_text`; display names never hold an email address (`_text.name`), and an address in the
summary's text becomes `[address]`.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from heinzel import clean_text
from heinzel_zoom._text import ADDRESS, name, without_addresses

_FORWARDED = r"^(?:re:\s*|fwd?:\s*)*"
_SUBJECTS = (
    re.compile(
        _FORWARDED + r"meeting\s+summary\s+for\s+(?P<title>.+?)(?:\s+\((?P<date>[\d/.\-]+)\))?\s*$", re.IGNORECASE
    ),
    re.compile(_FORWARDED + r"meeting\s+summary\s*[:\-]\s*(?P<title>.+?)\s*$", re.IGNORECASE),
    re.compile(_FORWARDED + r"meeting\s+assets\s+for\s+(?P<title>.+?)\s+(?:are|is)\s+ready!?\s*$", re.IGNORECASE),
)
_MEETING_ID = re.compile(r"^\s*meeting\s+id\s*[:#]?\s*(?P<id>\d(?:[\d \-]{7,18})\d)\s*$", re.IGNORECASE)
_DURATION = re.compile(
    r"^\s*duration\s*:\s*(?:(?P<hours>\d{1,2})\s*h(?:ours?|rs?)?\s*)?(?:(?P<minutes>\d{1,4})\s*m(?:in(?:ute)?s?)?)?\s*$",
    re.IGNORECASE,
)
_PEOPLE = re.compile(r"^\s*(?:participants|attendees)\s*:\s*(?P<people>.+)$", re.IGNORECASE)
_LINK = re.compile(r"https://(?:[a-z0-9-]+\.)*zoom\.us/[^\s<>()\"']*", re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[-*\u2022\u25cf\u25aa]|\d{1,3}[.)])\s+")
HEADINGS = ("quick recap", "next steps", "summary")
"""Section headings Zoom writes, lower case; other headings inside `Summary` are topics."""
_FOOTER = (
    "ai-generated content may be inaccurate",
    "please rate the accuracy",
    "edit, share, and view",
    "share this summary",
    "unsubscribe",
    "zoom video communications",
    "you are receiving this",
    "this email was sent",
)
MAX_LINE = 2_000
"""Characters of one line kept; Zoom's longest paragraphs are well below it."""


@dataclass(frozen=True, slots=True)
class Section:
    """One part of the summary: its heading and its lines, in order."""

    heading: str
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        """The heading and the lines, one per line."""
        return "\n".join((self.heading, *self.lines))


@dataclass(frozen=True, slots=True)
class Summary:
    """What one summary email says about its meeting."""

    title: str
    conference_id: str | None = None
    duration: timedelta | None = None
    people: tuple[str, ...] = ()
    url: str | None = None
    sections: tuple[Section, ...] = field(default_factory=tuple)


def title_of(subject: str) -> str | None:
    """The meeting's title if `subject` is a Zoom summary's subject, else None."""
    cleaned = without_addresses(" ".join(clean_text(subject).split()))
    for pattern in _SUBJECTS:
        if (found := pattern.match(cleaned)) and (title := found.group("title").strip(" -:")):
            return title
    return None


def _footer(line: str) -> bool:
    lowered = line.casefold()
    return any(mark in lowered for mark in _FOOTER)


@dataclass(slots=True)
class _Reader:
    """What has been read of a summary's body so far, line by line."""

    title: str
    conference_id: str | None = None
    duration: timedelta | None = None
    people: tuple[str, ...] = ()
    url: str | None = None
    sections: list[Section] = field(default_factory=list[Section])
    heading: str | None = None
    lines: list[str] = field(default_factory=list[str])

    def read(self, raw: str) -> None:
        line = " ".join(raw.split())[:MAX_LINE]
        if self.url is None and (link := _LINK.search(line)):
            self.url = link.group().rstrip(".,;")
        line = without_addresses(line)
        if not line or _footer(line) or _LINK.fullmatch(line) or self._fact(line) or title_of(line) == self.title:
            return  # empty, boilerplate, a fact about the meeting, or the subject repeated as the first line
        if _heading(line, self.heading):
            self._close()
            self.heading = line.rstrip(":")
        elif self.heading is not None:
            self.lines.append(_BULLET.sub("- ", line))

    def _fact(self, line: str) -> bool:
        """Take the meeting ID, the duration or the participants from `line`; whether it was one of them."""
        if found := _MEETING_ID.match(line):
            self.conference_id = re.sub(r"\D", "", found.group("id"))
        elif (found := _DURATION.match(line)) and (found.group("hours") or found.group("minutes")):
            self.duration = timedelta(hours=int(found.group("hours") or 0), minutes=int(found.group("minutes") or 0))
        elif found := _PEOPLE.match(line):
            parts = re.split(r"[,;]", found.group("people"))
            self.people = tuple(p for part in parts if (p := name(part)) and p != ADDRESS)
        else:
            return False
        return True

    def _close(self) -> None:
        if self.heading is not None and self.lines:
            self.sections.append(Section(self.heading, tuple(self.lines)))
        self.lines = []

    def summary(self) -> Summary:
        self._close()
        return Summary(self.title, self.conference_id, self.duration, self.people, self.url, tuple(self.sections))


def parse(subject: str, text: str) -> Summary | None:
    """The summary in an email with `subject` and plain-text body `text`, or None if it isn't a Zoom summary."""
    title = title_of(subject)
    if title is None:
        return None
    reader = _Reader(title)
    for raw in clean_text(text).splitlines():
        reader.read(raw)
    return reader.summary()


def _heading(line: str, current: str | None) -> bool:
    """Whether `line` starts a section: one of Zoom's headings, or a short title-like line inside `Summary`."""
    bare = line.rstrip(":").strip()
    if bare.casefold() in HEADINGS:
        return True
    in_summary = current is not None and (current.casefold() == "summary" or current.casefold() not in HEADINGS)
    return (
        in_summary
        and len(bare) <= 80  # noqa: PLR2004 - a topic heading is short
        and not _BULLET.match(line)
        and not bare.endswith((".", "!", "?", ","))
        and bare[:1].isupper()
    )


def meeting_times(received: datetime, duration: timedelta | None) -> tuple[datetime, datetime]:
    """When the meeting started and ended: Zoom mails the summary when it ends, so it ended when the mail arrived."""
    return received - (duration or timedelta(0)), received
