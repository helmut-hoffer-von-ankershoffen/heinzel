"""Integration capabilities: what workflows read from and write to a team's tools, whatever the vendor (ADR 0028).

Each protocol covers what the first workflows need, not a vendor's whole API. Adapters in plugins implement them over
any transport (a vendor API, a CLI, an MCP server); the kernel hands a requester a handle limited to the modes it
declared. The protocols are part of the public API and versioned with it (ADR 0026).

Reads that take a `Window` are contract-shaped: a workflow contract names them as inputs with fixed arguments
(ADR 0021), and the window bounds the result. An adapter pages until the window is covered or a byte budget of its
own is passed, and then raises a `HeinzelError` that says to narrow the window: results are plain lists, never
truncated ones. Reads that take an id (`Documents.read`, `Meetings.transcript`) serve module operations, which
know the id; a contract can't.

Every string an adapter returns is data from people outside this process: it ends up in terminals and in agents'
prompts. Adapters pass each one through `clean_text` (`heinzel.clean_text`), so no control character, invisible
character or bidirectional override reaches either; what the text says is still never an instruction.

People appear only as `Participant`: a display name and an email address, how the vendor shows them. The email is
the join key the directory of record will use (ADR 0024); nothing here counts, rates or ranks anyone. Mail senders and
recipients are participants too, so the workflow packet builder drops their addresses like every other.
"""

import re
from datetime import datetime
from typing import Any, Protocol, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class Window(BaseModel):
    """A half-open time window `[since, until)` to list changes in."""

    since: AwareDatetime = Field(description="Start of the window, inclusive; timezone-aware.")
    until: AwareDatetime = Field(description="End of the window, exclusive; timezone-aware.")


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


class Message(BaseModel):
    """A message posted in a channel, at the top level or as a reply in a thread."""

    channel: str = Field(description="Identifier of the channel it was posted in, resolved from a `#name` if given.")
    id: str = Field(description="Identifier in the adapter's notation, e.g. a Slack `ts`; it names the thread too.")
    author: Participant = Field(description="Who posted it.")
    posted_at: AwareDatetime = Field(description="When it was posted.")
    text: str = Field(description="Content as plain text or Markdown; mentions as names, never as raw ids.")
    thread: str | None = Field(default=None, description="Id of the message it replies to; none at the top level.")
    reply_count: int = Field(default=0, description="Replies in its thread; 0 for a reply or a message without one.")
    url: str | None = Field(default=None, description="Where people open it; none if it has no web address.")


class PostedMessage(BaseModel):
    """What `Chat.post` posted."""

    channel: str = Field(description="Identifier of the channel it was posted in, resolved from a `#name` if given.")
    id: str = Field(description="Identifier of the new message, in the adapter's notation.")
    thread: str | None = Field(default=None, description="Id of the message it replied to, if it is a reply.")
    url: str | None = Field(default=None, description="Where people open it; none if it has no web address.")


class Meeting(BaseModel):
    """One meeting that took place, as the meeting tool recorded it."""

    id: str = Field(
        description="Identifier of this occurrence in the adapter's notation (a Zoom meeting UUID, not its number); "
        "what `Meetings.transcript` takes."
    )
    title: str = Field(description="Topic or title of the meeting.")
    started_at: AwareDatetime = Field(description="When it started.")
    ended_at: AwareDatetime = Field(description="When it ended.")
    participants: list[Participant] = Field(
        default_factory=list[Participant], description="Who took part, as recorded."
    )
    conference_id: str | None = Field(
        default=None,
        description="Identifier of the conference in the meeting tool's notation, e.g. a Zoom meeting number, which "
        "recurs; a calendar `Event` with the same `conference_id` and an overlapping time is this meeting's event.",
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
    """The transcript of one meeting: the meeting, and speaker-attributed segments in order of speaking."""

    meeting: Meeting = Field(description="The meeting it transcribes, with its participants.")
    language: str | None = Field(default=None, description="BCP 47 language tag the vendor reports; none if unknown.")
    segments: list[TranscriptSegment] = Field(
        default_factory=list[TranscriptSegment], description="In order of speaking; join them for plain text."
    )


class Event(BaseModel):
    """One occurrence of an entry in a calendar; a recurring entry is expanded into its occurrences."""

    id: str = Field(description="Identifier of this occurrence in the adapter's notation.")
    title: str = Field(description="Title (summary) of the event.")
    start: AwareDatetime = Field(description="When it starts; midnight in the calendar's zone for an all-day event.")
    end: AwareDatetime = Field(description="When it ends, exclusive; the next midnight for an all-day event.")
    all_day: bool = Field(default=False, description="Whether it is an all-day event.")
    organizer: Participant | None = Field(default=None, description="Who organizes it; none if the vendor doesn't say.")
    attendees: list[Participant] = Field(
        default_factory=list[Participant], description="Who is invited, the user included."
    )
    status: str = Field(default="confirmed", description="State in the vendor's words, e.g. `confirmed`, `cancelled`.")
    conference_id: str | None = Field(
        default=None,
        description="Identifier of the conference it is held in, in the meeting tool's notation (a Zoom meeting "
        "number taken from the conferencing data, location or description; a Meet code), so that the `Meeting` with "
        "the same `conference_id` and an overlapping time is this event's meeting; none without conferencing.",
    )
    conference_url: str | None = Field(default=None, description="Link to join the conference; none without one.")
    url: str | None = Field(default=None, description="Where people open it; none if it has no web address.")


class Chat(Protocol):
    """Channels people talk in, such as Slack (capability `chat`).

    A `channel` argument is a channel id or a `#name`, which the adapter resolves; results carry the id. On Teams a
    `#name` is a channel of the configured team. Direct messages and group chats are out of scope.
    """

    def messages(self, channel: str, window: Window) -> list[Message]:
        """Messages posted in `channel` within `window`, top-level or reply, oldest first. Mode: read.

        A reply whose parent was posted before the window is included; the parent isn't. The window bounds the
        result (see the module docstring).
        """
        ...

    def post(self, channel: str, text: str, thread: str | None = None) -> PostedMessage:
        """Post `text` (plain text or Markdown) to `channel`, as a reply in `thread` when given. Mode: write.

        Adapters neutralise broadcast and mention syntax in `text` (Slack `<!channel>`, `<!here>`, `<@U…>`; Teams
        `<at>`), so a draft can't page a channel, and apply the vendor's length limit as they see fit. Only publish
        operations (`read_only=False`) get a handle on which this works (ADR 0028).
        """
        ...


class Meetings(Protocol):
    """Meetings the user took part in and their transcripts, such as Zoom (capability `meetings`)."""

    def ended(self, window: Window) -> list[Meeting]:
        """The user's meetings that ended within `window`, oldest first. Mode: read."""
        ...

    def transcripts(self, window: Window) -> list[Transcript]:
        """Transcripts of the user's meetings that ended within `window` and have one, oldest first. Mode: read.

        The window bounds the result (see the module docstring); this is what a contract reads.
        """
        ...

    def transcript(self, meeting: str) -> Transcript:
        """The transcript of the meeting `meeting` (a `Meeting.id`), for operations that know the id. Mode: read."""
        ...


class Calendar(Protocol):
    """The user's calendar, such as Google Calendar (capability `calendar`)."""

    def events(self, window: Window) -> list[Event]:
        """The user's events that overlap `window`, recurrences expanded, cancelled ones included, by start.

        Mode: read.
        """
        ...


MAIL_HEADERS = frozenset(
    {
        "auto-submitted",
        "content-language",
        "content-type",
        "date",
        "importance",
        "in-reply-to",
        "list-id",
        "message-id",
        "mime-version",
        "precedence",
        "references",
        "subject",
        "thread-topic",
        "x-auto-response-suppress",
    }
)
"""The only headers `MailContent.headers` keeps: none of them carries a person's address. Every other header is
dropped, `From`, `To`, `Cc`, `Reply-To`, `Received`, `Authentication-Results`, `List-Post` and the like included."""


class MailMessage(BaseModel):
    """A message in a mailbox, as a list shows it: who sent it to whom, when, and its subject; no body."""

    id: str = Field(description="Identifier in the adapter's notation; what `Mail.read` takes.")
    thread: str | None = Field(default=None, description="Identifier of its conversation; none if the vendor has none.")
    sender: Participant = Field(description="Who sent it (the From header).")
    recipients: list[Participant] = Field(
        default_factory=list[Participant], description="Who it was sent to (To and Cc), as the message names them."
    )
    subject: str = Field(description="Subject line, decoded; empty if it has none.")
    received_at: AwareDatetime = Field(description="When the mailbox received it.")
    labels: list[str] = Field(
        default_factory=list, description="Labels or folders it is filed under, in the vendor's words, e.g. `INBOX`."
    )
    url: str | None = Field(default=None, description="Where people open it; none if it has no web address.")


class MailContent(BaseModel):
    """One message with its text and headers, for reading."""

    message: MailMessage = Field(description="The message, as `Mail.messages` lists it.")
    text: str = Field(
        description="The body as plain text: the text part, or the HTML part converted to text; attachments left out."
    )
    headers: dict[str, str] = Field(
        default_factory=dict[str, str],
        description="Headers that carry no addresses (`MAIL_HEADERS`: `date`, `message-id`, `list-id`, ...) by "
        "lower-case name, repeated ones joined with `, `; the model drops every other header, so people's addresses "
        "stay in `message.sender` and `recipients`.",
    )

    @field_validator("headers")
    @classmethod
    def _only_headers_without_addresses(cls, headers: dict[str, str]) -> dict[str, str]:
        kept: dict[str, list[str]] = {}
        for name, value in headers.items():
            if name.lower() in MAIL_HEADERS:
                kept.setdefault(name.lower(), []).append(value)
        return {name: ", ".join(values) for name, values in sorted(kept.items())}


_QUERY_TERM = re.compile(r'\s*(?P<key>[a-z]+):(?:"(?P<quoted>[^"]*)"|(?P<bare>[^\s"]+))(?=\s|$)')
_QUERY_KEYS = ("from", "subject", "label")


class MailQuery(BaseModel):
    """Which messages `Mail.messages` lists, parsed from its `query` string; every term set must match.

    The syntax is the same for every adapter: `from:<address or domain> subject:<text> label:<name>`, each at most
    once, a value with spaces in double quotes (`subject:"Meeting summary"`); an empty query lists every message.
    `from:ada@example.com` matches that sender, `from:example.com` (or `from:@example.com`) any sender at that domain
    or below it, and a sender without an address never matches `from:`; `subject:`
    matches a subject containing the text, ignoring case; `label:` a label or folder of that name, ignoring case.
    Adapters translate it into the vendor's own search where they can, and check `matches` on what comes back.
    """

    model_config = ConfigDict(frozen=True)

    sender: str | None = Field(
        default=None, description="`from:`: an address, or a domain (`example.com` or `@example.com`), lower case."
    )
    subject: str | None = Field(default=None, description="`subject:`: text the subject contains.")
    label: str | None = Field(default=None, description="`label:`: a label or folder name.")

    @field_validator("sender")
    @classmethod
    def _lower_case_without_a_leading_at(cls, sender: str | None) -> str | None:
        if sender is None:
            return None
        lowered = sender.strip().lower()
        return lowered.removeprefix("@") if lowered.count("@") == 1 and lowered.startswith("@") else lowered

    @classmethod
    def parse(cls, query: str) -> Self:
        """Read a query string.

        Raises:
            ValueError: If a term is not `from:`, `subject:` or `label:` with a value, or a key appears twice; the
                message shows a query that works.
        """
        found: dict[str, str] = {}
        position = 0
        text = query.strip()
        while position < len(text):
            term = _QUERY_TERM.match(text, position)
            if term is None or term.group("key") not in _QUERY_KEYS:
                msg = (
                    f'Mail query {query!r} must be terms like `from:noreply@example.com subject:"Meeting summary" '
                    f"label:INBOX` (from, subject, label; quote values with spaces)"
                )
                raise ValueError(msg)
            key = term.group("key")
            value = term.group("quoted") if term.group("quoted") is not None else term.group("bare")
            if key in found:
                msg = f"Mail query {query!r} sets `{key}:` twice; every term must match, so give it once"
                raise ValueError(msg)
            if not value.strip():
                msg = f"Mail query {query!r} gives `{key}:` no value"
                raise ValueError(msg)
            found[key] = value.strip()
            position = term.end()
        return cls(sender=found.get("from"), subject=found.get("subject"), label=found.get("label"))

    def matches(self, message: MailMessage) -> bool:
        """Whether `message` is one to list: its sender's address, subject and labels match every term set."""
        if self.sender is not None:
            address = (message.sender.email or "").strip().lower()
            local, at, domain = address.rpartition("@")
            if not (local and at and domain):
                return False  # no address to match: never taken for the sender asked for
            if "@" in self.sender:
                if address != self.sender:
                    return False
            elif domain != self.sender and not domain.endswith(f".{self.sender}"):
                return False
        if self.subject is not None and self.subject.casefold() not in message.subject.casefold():
            return False
        return self.label is None or self.label.casefold() in {label.casefold() for label in message.labels}


class Mail(Protocol):
    """A mailbox the user reads, such as Gmail or an IMAP account (capability `mail`).

    Read only: nothing sends, files or deletes mail. Adapters bound the result like every window read, and bound
    `MailContent.text` by a byte budget of their own, raising a `HeinzelError` past it rather than cutting it.
    """

    def messages(self, query: str, window: Window) -> list[MailMessage]:
        """Messages that match `query` (see `MailQuery`) and were received within `window`, oldest first. Mode: read.

        The window bounds the result (see the module docstring); this is what a contract reads.
        """
        ...

    def read(self, message: str) -> MailContent:
        """The message `message` (a `MailMessage.id`) with its text and headers, for operations. Mode: read."""
        ...


class Relation(BaseModel):
    """A person's manager or one of their direct reports, and whether a directory says so or hz guessed it."""

    who: Participant = Field(description="Who: display name and email.")
    login: str | None = Field(
        default=None, description="Their login in the source, e.g. a chat or GitHub user name; none if it has none."
    )
    guess: bool = Field(
        default=False,
        description="Whether this was drafted from signals the user can read (a recurring 1:1 in their calendar) "
        "instead of read from a directory of record: shown for the user to confirm or correct, never stored as fact.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="What a guess rests on, in words, e.g. `a recurring 1:1 weekly in your calendar, organized by "
        "Ada`; display names, never addresses (refused), titles or counts. Empty for what a directory says.",
    )

    @model_validator(mode="after")
    def _guesses_show_evidence(self) -> Self:
        if self.guess and not self.evidence:
            msg = "a guessed reporting line must say what it rests on (evidence)"
            raise ValueError(msg)
        if any("@" in line for line in self.evidence):
            msg = "evidence names people by display name, never by address: it may reach a prompt"
            raise ValueError(msg)
        return self


class Person(BaseModel):
    """A person as a directory knows them: who they are, their place in the org, and their reporting lines."""

    who: Participant = Field(description="Who: display name and email.")
    login: str | None = Field(
        default=None, description="Their login in the source, e.g. a chat or GitHub user name; none if it has none."
    )
    title: str | None = Field(default=None, description="Job title, in the source's words; none if it has none.")
    department: str | None = Field(default=None, description="Department or org unit; none if the source has none.")
    team: str | None = Field(default=None, description="Team, in the source's words; none if the source has none.")
    manager: Relation | None = Field(default=None, description="Who they report to; none if unknown or nobody.")
    reports: list[Relation] = Field(
        default_factory=list[Relation], description="Their direct reports, by name; empty if none or unknown."
    )


class Directory(Protocol):
    """Who people are and who reports to whom (capability `directory`, ADR 0024).

    Served by a directory of record when a plugin can read one (an HR system, a chat tool's profile fields, an IdP
    such as Google Workspace), otherwise by the core's `one-on-ones` adapter, which guesses reporting lines from
    recurring 1:1s in the bound calendar and marks every one as a guess (`Relation.guess`) with its evidence.

    Read only, and only the fields above: never compensation, ratings, absences or anything else HR-sensitive
    (ADR 0024). A `person` argument is a login or an email address, matched as the source matches them (emails ignore
    case; `one-on-ones` knows emails only). Neither method takes a window, so contracts don't read the directory;
    module operations do, and must not hand its addresses to an agent (ADR 0028).
    """

    def person(self, person: str) -> Person | None:
        """The person whose login or email is `person`, or None if the source doesn't know them. Mode: read."""
        ...

    def reports(self, person: str) -> list[Person]:
        """The direct reports of the person whose login or email is `person`, by name; empty if none. Mode: read."""
        ...


class TeamGraph(Protocol):
    """The team's graph of kept promises (capability `team-graph`, ADR 0054), as a JSON Graph Format document.

    Served by the core's `hz-graph` adapter (`graph`), which builds it from the team's contracts and the telemetry its
    Heinzelmännchen report. It is how a contract reads the graph: the team graph keeper's input. The document follows
    `docs/schemas/team-graph-1.json`; it is metadata only, and the packet builder drops its email addresses like every
    other input's.
    """

    def snapshot(self) -> dict[str, Any]:
        """The current graph: nodes, edges, each promise's state, kept-on-time per week and where the flow waits."""
        ...
