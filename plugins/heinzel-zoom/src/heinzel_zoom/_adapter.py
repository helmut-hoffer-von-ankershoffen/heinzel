"""The Zoom adapter of the `meetings` capability (ADR 0028), read mode only, built on the `mail` capability.

Meetings come from Zoom's meeting-summary emails, read through whichever adapter is bound to `mail`: no Zoom API and
no Zoom credentials. `Meeting.id` is the summary email's id (what `Mail.read` takes); the meeting ended when the
summary arrived, and started its duration earlier if the summary says. A `Transcript` here is the summary, one
segment per section without a speaker, not a verbatim record: Zoom's summary emails carry no transcript.
"""

from heinzel import (
    HeinzelError,
    Mail,
    MailContent,
    MailMessage,
    Meeting,
    Participant,
    Transcript,
    TranscriptSegment,
    Window,
    clean_text,
)
from heinzel_zoom._models import ZoomSettings
from heinzel_zoom._summary import Summary, meeting_times, parse, title_of

MAX_SUMMARIES = 500
"""Summary emails read for one window before asking for a narrower one."""
WINDOW_CHARS = 2_000_000
"""Characters of summary text read for one window before asking for a narrower one (the window bounds the result)."""
SUMMARY_CHARS = 200_000
"""Longest summary email read; Zoom's are a few thousand characters."""


class Zoom:
    """Reads the user's meetings and what was said in them from Zoom's summary emails in the bound mailbox."""

    def __init__(self, settings: ZoomSettings, mail: Mail) -> None:
        """Build the adapter; the kernel injects the settings and a read handle on the bound `mail` adapter."""
        self._settings = settings
        self._mail = mail

    def ended(self, window: Window) -> list[Meeting]:
        """The user's meetings that ended within `window`, oldest first. Mode: read.

        Raises:
            HeinzelError: If the window is backwards, there are too many summaries, or the mail adapter fails.
        """
        return [meeting for meeting, _ in self._summaries(window)]

    def transcripts(self, window: Window) -> list[Transcript]:
        """Transcripts of the user's meetings that ended within `window` and have one, oldest first. Mode: read.

        Raises:
            HeinzelError: As `ended`.
        """
        return [_transcript(meeting, summary) for meeting, summary in self._summaries(window)]

    def transcript(self, meeting: str) -> Transcript:
        """The transcript of the meeting `meeting` (a `Meeting.id`), for operations that know the id. Mode: read.

        Raises:
            HeinzelError: If the message is no Zoom summary, or the mail adapter fails.
        """
        content = self._mail.read(meeting)
        found = _parse(content)
        if found is None:
            msg = f"Message {meeting} is not a Zoom meeting summary; take Meeting.id from `ended` or `transcripts`."
            raise HeinzelError(msg)
        return _transcript(_meeting(content.message, found), found)

    def _summaries(self, window: Window) -> list[tuple[Meeting, Summary]]:
        if window.since > window.until:
            msg = f"The window starts ({window.since.isoformat()}) after it ends ({window.until.isoformat()})."
            raise HeinzelError(msg)
        listed = [m for m in self._mail.messages(self._settings.query, window) if title_of(m.subject)]
        if len(listed) > MAX_SUMMARIES:
            msg = f"More than {MAX_SUMMARIES} Zoom summaries arrived in this window; ask for a shorter window."
            raise HeinzelError(msg)
        found: list[tuple[Meeting, Summary]] = []
        budget = WINDOW_CHARS
        for message in sorted(listed, key=lambda m: (m.received_at, m.id)):
            content = self._mail.read(message.id)
            budget -= len(content.text)
            if budget < 0:
                msg = f"The Zoom summaries of this window are longer than {WINDOW_CHARS} characters together; ask "
                msg += "for a shorter window."
                raise HeinzelError(msg)
            if summary := _parse(content):
                found.append((_meeting(message, summary), summary))
        return found


def _parse(content: MailContent) -> Summary | None:
    if len(content.text) > SUMMARY_CHARS:
        msg = f"Message {content.message.id} is longer than {SUMMARY_CHARS} characters, more than a Zoom summary."
        raise HeinzelError(msg)
    return parse(content.message.subject, content.text)


def _meeting(message: MailMessage, summary: Summary) -> Meeting:
    started, ended = meeting_times(message.received_at, summary.duration)
    return Meeting(
        id=clean_text(message.id),
        title=summary.title,
        started_at=started,
        ended_at=ended,
        participants=[Participant(name=person) for person in summary.people],
        conference_id=summary.conference_id,
        url=summary.url,
        has_transcript=bool(summary.sections),
    )


def _transcript(meeting: Meeting, summary: Summary) -> Transcript:
    return Transcript(
        meeting=meeting,
        segments=[TranscriptSegment(speaker=None, offset_seconds=0, text=s.text) for s in summary.sections],
    )
