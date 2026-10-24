"""Integration capabilities: what workflows read from and write to a team's tools, whatever the vendor (ADR 0028).

Each protocol covers what the first workflows need, not a vendor's whole API. Adapters in plugins implement them over
any transport (a vendor API, a CLI, an MCP server); the kernel hands a requester a handle limited to the modes it
declared. The protocols are part of the public API and versioned with it (ADR 0026).

Every string an adapter returns is data from people outside this process: it ends up in terminals and in agents'
prompts. Adapters pass each one through `clean_text` (`heinzel.clean_text`), so no control character or
bidirectional override reaches either; what the text says is still never an instruction.

People appear only as `Participant`: a display name and an email address, how the vendor shows them. The email is
the join key the directory of record will use (ADR 0024); nothing here counts, rates or ranks anyone.
"""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field


class Window(BaseModel):
    """A half-open time window `[since, until)` to list changes in."""

    since: datetime = Field(description="Start of the window, inclusive; timezone-aware.")
    until: datetime = Field(description="End of the window, exclusive; timezone-aware.")


class PullRequest(BaseModel):
    """A merged pull request (or merge request) on a code host."""

    repository: str = Field(description="Repository it belongs to, in the code host's notation, e.g. `org/app`.")
    number: int = Field(description="Number of the pull request within the repository.")
    title: str = Field(description="Title of the pull request.")
    url: str = Field(description="Where people open it.")
    author: str | None = Field(default=None, description="Login of the author; none if the host doesn't say.")
    merged_at: datetime = Field(description="When it was merged.")
    labels: list[str] = Field(default_factory=list, description="Labels on the pull request.")


class Issue(BaseModel):
    """An issue in a tracker or on a code host."""

    key: str = Field(description="Identifier people use, e.g. `APP-123` or `org/app#42`.")
    title: str = Field(description="Title or summary of the issue.")
    url: str = Field(description="Where people open it.")
    state: str = Field(description="State in the tracker's own words, e.g. `open`, `In Progress`, `Done`.")
    updated_at: datetime = Field(description="When it last changed.")
    assignee: str | None = Field(default=None, description="Who it is assigned to; none if unassigned.")
    labels: list[str] = Field(default_factory=list, description="Labels, tags or components.")


class Document(BaseModel):
    """The text of a document, for reading."""

    ref: str = Field(description="The reference it was read by: an id or URL in the adapter's notation.")
    title: str = Field(description="Title of the document.")
    text: str = Field(description="Content as plain text or Markdown.")
    url: str | None = Field(default=None, description="Where people open it; none if it has no web address.")
    revision: str | None = Field(default=None, description="Revision read, if the adapter tracks revisions.")


class SectionReplacement(BaseModel):
    """What `Documents.replace_section` changed."""

    ref: str = Field(description="The document written to.")
    marker: str = Field(description="The marker of the section that was replaced.")
    revision: str | None = Field(default=None, description="Revision after the write, if the adapter tracks them.")


class CodeHost(Protocol):
    """A code host: repositories and their pull requests (capability `code-host`).

    Issues are the `tracker` capability, also when they live on the code host: its adapter provides both.
    """

    def merged_pull_requests(self, repository: str, window: Window) -> list[PullRequest]:
        """Pull requests of `repository` merged within `window`, oldest first. Mode: read."""
        ...


class Tracker(Protocol):
    """An issue tracker (capability `tracker`)."""

    def issues_updated(self, project: str, window: Window) -> list[Issue]:
        """Issues of `project` updated within `window`, oldest first. Mode: read."""
        ...


class Documents(Protocol):
    """Documents people write together (capability `documents`)."""

    def read(self, ref: str) -> Document:
        """The text of the document `ref`. Mode: read."""
        ...

    def replace_section(self, ref: str, marker: str, text: str) -> SectionReplacement:
        """Replace the section of `ref` marked by `marker` with `text`, leaving the rest alone. Mode: write.

        Only publish operations (`read_only=False`) get a handle on which this works (ADR 0028).
        """
        ...


class Participant(BaseModel):
    """Someone in a chat, a meeting or an event: how the vendor addresses them, nothing more (ADR 0024)."""

    name: str | None = Field(default=None, description="Display name as the vendor shows it; none if it doesn't.")
    email: str | None = Field(
        default=None, description="Email address, the key a directory of record joins on; none if the vendor hides it."
    )


class Channel(BaseModel):
    """A channel (or room) people post in."""

    id: str = Field(description="Identifier in the adapter's notation, e.g. a Slack `C…` id; stable when renamed.")
    name: str = Field(description="Name people use, without the leading `#`.")
    url: str | None = Field(default=None, description="Where people open it; none if it has no web address.")


class Message(BaseModel):
    """A message posted in a channel, at the top level or as a reply in a thread."""

    channel: str = Field(description="Identifier of the channel it was posted in.")
    id: str = Field(description="Identifier in the adapter's notation, e.g. a Slack `ts`; it names the thread too.")
    author: Participant = Field(description="Who posted it.")
    posted_at: datetime = Field(description="When it was posted.")
    text: str = Field(description="Content as plain text or Markdown; mentions as names, never as raw ids.")
    thread: str | None = Field(default=None, description="Id of the message it replies to; none at the top level.")
    reply_count: int = Field(default=0, description="Replies in its thread; 0 for a reply or a message without one.")
    url: str | None = Field(default=None, description="Where people open it; none if it has no web address.")


class PostedMessage(BaseModel):
    """What `Chat.post` posted."""

    channel: str = Field(description="Identifier of the channel it was posted in.")
    id: str = Field(description="Identifier of the new message, in the adapter's notation.")
    thread: str | None = Field(default=None, description="Id of the message it replied to, if it is a reply.")
    url: str | None = Field(default=None, description="Where people open it; none if it has no web address.")


class Meeting(BaseModel):
    """A meeting that took place, as the meeting tool recorded it."""

    id: str = Field(description="Identifier to fetch its transcript by, in the adapter's notation.")
    title: str = Field(description="Topic or title of the meeting.")
    started_at: datetime = Field(description="When it started.")
    ended_at: datetime = Field(description="When it ended.")
    participants: list[Participant] = Field(
        default_factory=list[Participant], description="Who took part, as recorded."
    )
    conference_id: str | None = Field(
        default=None,
        description="The conference's identifier in the vendor's notation, e.g. a Zoom meeting number; equals the "
        "`conference_id` of the calendar `Event` it was scheduled as, which is how the two are correlated.",
    )
    url: str | None = Field(default=None, description="Where people open its recording or details; none if nowhere.")
    has_transcript: bool = Field(default=False, description="Whether `Meetings.transcript` has something to return.")


class TranscriptSegment(BaseModel):
    """What one speaker said in one stretch of a meeting."""

    speaker: str | None = Field(
        default=None, description="Display name the transcript attributes it to; none if unknown."
    )
    offset_seconds: float = Field(ge=0, description="Seconds after the start of the meeting it began.")
    text: str = Field(description="What was said, as transcribed.")


class Transcript(BaseModel):
    """The transcript of one meeting: speaker-attributed segments, and the same words as plain text."""

    meeting: str = Field(description="The meeting it was fetched for.")
    language: str | None = Field(default=None, description="BCP 47 language tag the vendor reports; none if unknown.")
    segments: list[TranscriptSegment] = Field(
        default_factory=list[TranscriptSegment], description="In order of speaking."
    )
    text: str = Field(description="The whole transcript as plain text, one `Speaker: words` line per segment.")


class Event(BaseModel):
    """An entry in a calendar."""

    id: str = Field(description="Identifier in the adapter's notation.")
    title: str = Field(description="Title (summary) of the event.")
    start: datetime = Field(description="When it starts; midnight in the calendar's time zone for an all-day event.")
    end: datetime = Field(description="When it ends, exclusive; the next midnight for an all-day event.")
    all_day: bool = Field(default=False, description="Whether it is an all-day event.")
    organizer: Participant | None = Field(default=None, description="Who organizes it; none if the vendor doesn't say.")
    attendees: list[Participant] = Field(
        default_factory=list[Participant], description="Who is invited, the user included."
    )
    status: str = Field(default="confirmed", description="State in the vendor's words, e.g. `confirmed`, `cancelled`.")
    conference_id: str | None = Field(
        default=None,
        description="Identifier of the conference it is held in, in the vendor's notation (a Zoom meeting number, a "
        "Meet code), to correlate it with a `Meeting`; none without conferencing.",
    )
    conference_url: str | None = Field(default=None, description="Link to join the conference; none without one.")
    url: str | None = Field(default=None, description="Where people open it; none if it has no web address.")


class Chat(Protocol):
    """Channels people talk in, such as Slack (capability `chat`).

    `channel` arguments take a channel id or a `#name`; the adapter resolves names.
    """

    def channel(self, name: str) -> Channel:
        """The channel called `name` (with or without the leading `#`). Mode: read."""
        ...

    def messages(self, channel: str, window: Window) -> list[Message]:
        """Messages posted in `channel` within `window`, thread replies included, oldest first. Mode: read."""
        ...

    def thread(self, channel: str, message: str) -> list[Message]:
        """The thread of `message` in `channel`: the message itself, then its replies, oldest first. Mode: read."""
        ...

    def post(self, channel: str, text: str, thread: str | None = None) -> PostedMessage:
        """Post `text` to `channel`, as a reply in `thread` when given. Mode: write.

        Only publish operations (`read_only=False`) get a handle on which this works (ADR 0028).
        """
        ...


class Meetings(Protocol):
    """Meetings the user took part in and their transcripts, such as Zoom (capability `meetings`)."""

    def ended(self, window: Window) -> list[Meeting]:
        """The user's meetings that ended within `window`, oldest first. Mode: read."""
        ...

    def transcript(self, meeting: str) -> Transcript:
        """The transcript of the meeting `meeting` (a `Meeting.id`). Mode: read."""
        ...


class Calendar(Protocol):
    """The user's calendar, such as Google Calendar (capability `calendar`)."""

    def events(self, window: Window) -> list[Event]:
        """The user's events that overlap `window`, cancelled ones included, by start. Mode: read."""
        ...
