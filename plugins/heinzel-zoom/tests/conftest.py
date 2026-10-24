"""Shared fixtures: a mailbox in memory, made-up Zoom summary emails, and kernels that read through them. Offline.

Every meeting, person and address here is made up. heinzel.testing (a pytest plugin, loaded automatically) brings the
rest: every test is exactly one of unit, integration or e2e and gets its level's timeout, and every test has its own
HZ_CONFIG_HOME, HZ_DATA_DIR, HZ_CACHE_DIR, HZ_STATE_DIR, HZ_LOG_DIR, without the developer's HZ_* variables.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import ClassVar

import pytest

from heinzel import Config, Kernel, MailContent, MailMessage, MailQuery, Module, Participant, Settings, Window
from heinzel.kernel import ModuleRecord, Origin, discover

TEST_ORIGIN = Origin(distribution="heinzel-test", version="0", builtin=False)
ZOOM = Participant(name="Zoom", email="no-reply@zoom.us")
RECEIVED = datetime(2026, 10, 20, 9, 31, tzinfo=UTC)
WINDOW = Window(since=datetime(2026, 10, 1, tzinfo=UTC), until=datetime(2026, 11, 1, tzinfo=UTC))
SUMMARY = """\
Meeting summary for Weekly sync (10/20/2026)

Hi Mira,

Quick recap

The team reviewed the release of the reading list and agreed to ship on Thursday.
Questions go to mira@example.com.

Next steps

- Jonas Berg will update the release notes.
- Priya: check the crash reports before Thursday.

Summary

Release of the reading list
The team agreed the release is ready.
The rollout starts with ten percent of users.

Crash reports
Priya found two crashes on older phones.

Meeting ID: 812 3456 7890
Duration: 30 minutes
Participants: Mira Holm, Jonas Berg, priya@example.com

AI-generated content may be inaccurate or misleading. Always check for accuracy.
Edit, share, and view the full summary in Zoom: https://us06web.zoom.us/meeting/summary/abc123
"""


def email(  # noqa: PLR0913 - the fields tests vary
    id: str,  # noqa: A002 - as MailMessage names it
    *,
    subject: str = "Meeting summary for Weekly sync (10/20/2026)",
    text: str = SUMMARY,
    received: datetime = RECEIVED,
    sender: Participant = ZOOM,
    labels: tuple[str, ...] = ("INBOX",),
) -> MailContent:
    """One email in the mailbox."""
    message = MailMessage(
        id=id,
        sender=sender,
        recipients=[Participant(name="Mira Holm", email="mira@example.com")],
        subject=subject,
        received_at=received,
        labels=list(labels),
    )
    return MailContent(message=message, text=text, headers={"Date": "Tue, 20 Oct 2026 09:31:00 +0000"})


class Mailbox:
    """A mail adapter in memory: it filters with `MailQuery` as real ones must, and records what it was asked."""

    stored: ClassVar[dict[str, MailContent]] = {}
    queries: ClassVar[list[str]] = []
    reads: ClassVar[list[str]] = []

    def messages(self, query: str, window: Window) -> list[MailMessage]:
        self.queries.append(query)
        wanted = MailQuery.parse(query)
        return [
            c.message
            for c in self.stored.values()
            if window.since <= c.message.received_at < window.until and wanted.matches(c.message)
        ]

    def read(self, message: str) -> MailContent:
        self.reads.append(message)
        return self.stored[message]

    @classmethod
    def holding(cls, *contents: MailContent) -> "Mailbox":
        """The mailbox, emptied and then holding `contents`."""
        cls.stored.clear()
        cls.queries.clear()
        cls.reads.clear()
        cls.stored.update({c.message.id: c for c in contents})
        return cls()


@pytest.fixture
def mailbox() -> type[Mailbox]:
    """The in-memory mailbox, empty; `mailbox.holding(email("m1"), ...)` fills it."""
    Mailbox.holding()
    return Mailbox


@pytest.fixture
def emails() -> Callable[..., MailContent]:
    """Build an email; Zoom's summary of a made-up weekly sync unless told otherwise."""
    return email


@pytest.fixture
def boot() -> Callable[..., Kernel]:
    """Boot a kernel the way the `hz` command does: Heinzelmännchen's built-in modules plus every installed plugin."""

    def make(**settings: object) -> Kernel:
        records, errors = discover()
        return Kernel(Settings(**settings), records, errors)

    return make


@pytest.fixture
def zoom_kernel(mailbox) -> Callable[..., Kernel]:
    """A kernel with every installed plugin, the in-memory mailbox as the mail adapter `inbox`, extra test-only
    modules and the given configuration."""

    def make(config: Config | None = None, **modules: Module) -> Kernel:
        inbox = Module("inbox", "Test-only mail adapter.")
        inbox.provides("mail", modes={"read"}, hosts=["mail.invalid"])(mailbox)
        records, errors = discover()
        extra = {"inbox": inbox, **modules}
        records += [ModuleRecord(qualname=name, module=module, origin=TEST_ORIGIN) for name, module in extra.items()]
        return Kernel(Settings(), records, errors, config or Config.empty())

    return make
