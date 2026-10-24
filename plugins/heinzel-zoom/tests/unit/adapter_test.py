from datetime import UTC, datetime, timedelta

import pytest
from heinzel_zoom import Zoom, ZoomSettings
from heinzel_zoom._adapter import MAX_SUMMARIES, SUMMARY_CHARS, WINDOW_CHARS

from heinzel import HeinzelError, Participant, Window
from heinzel.testing import assert_adapter

pytestmark = pytest.mark.unit

WINDOW = Window(since=datetime(2026, 10, 1, tzinfo=UTC), until=datetime(2026, 11, 1, tzinfo=UTC))
NEWS = Participant(name="News", email="news@paper.example")


def test_the_adapter_implements_the_meetings_protocol():
    assert_adapter(Zoom, "meetings")


def test_ended_lists_the_meetings_of_the_summaries_in_the_window_oldest_first(mailbox, emails):
    later = datetime(2026, 10, 22, 15, tzinfo=UTC)
    zoom = Zoom(
        ZoomSettings(),
        mailbox.holding(
            emails(
                "late",
                subject="Meeting assets for Retro are ready!",
                text="Quick recap\nIt went well.\n",
                received=later,
            ),
            emails("sync"),
            emails("news", subject="Weekly news", sender=NEWS),
            emails("ended", subject="Your meeting has ended"),
            emails("old", received=datetime(2026, 9, 30, tzinfo=UTC)),
        ),
    )
    sync, retro = zoom.ended(WINDOW)
    assert sync.id == "sync"
    assert sync.title == "Weekly sync"
    assert (sync.started_at, sync.ended_at) == (
        datetime(2026, 10, 20, 9, 1, tzinfo=UTC),
        datetime(2026, 10, 20, 9, 31, tzinfo=UTC),
    )
    assert sync.participants == [Participant(name="Mira Holm"), Participant(name="Jonas Berg")]
    assert sync.conference_id == "81234567890"
    assert sync.url == "https://us06web.zoom.us/meeting/summary/abc123"
    assert sync.has_transcript
    assert (retro.id, retro.title, retro.started_at, retro.conference_id) == ("late", "Retro", later, None)
    assert mailbox.queries == ["from:zoom.us"]
    assert sorted(mailbox.reads) == ["late", "sync"]  # only summaries are read, never other mail


def test_the_configured_query_is_used(mailbox, emails):
    zoom = Zoom(
        ZoomSettings(query="from:zoom.us label:Meetings"),
        mailbox.holding(emails("a"), emails("b", labels=("Meetings",))),
    )
    assert [m.id for m in zoom.ended(WINDOW)] == ["b"]
    assert mailbox.queries == ["from:zoom.us label:Meetings"]


def test_a_summary_without_sections_has_no_transcript(mailbox, emails):
    zoom = Zoom(ZoomSettings(), mailbox.holding(emails("a", text="Meeting ID: 812 3456 7890\n")))
    (meeting,) = zoom.ended(WINDOW)
    assert not meeting.has_transcript
    assert zoom.transcripts(WINDOW)[0].segments == []


def test_transcripts_are_the_summaries_one_segment_per_section_without_speakers(mailbox, emails):
    zoom = Zoom(ZoomSettings(), mailbox.holding(emails("sync")))
    (transcript,) = zoom.transcripts(WINDOW)
    assert transcript.meeting.id == "sync"
    assert [s.text.splitlines()[0] for s in transcript.segments] == [
        "Quick recap",
        "Next steps",
        "Release of the reading list",
        "Crash reports",
    ]
    assert {(s.speaker, s.offset_seconds) for s in transcript.segments} == {(None, 0)}
    assert "[address]" in transcript.segments[0].text
    assert "mira@example.com" not in transcript.model_dump_json()


def test_transcript_reads_one_summary_by_its_message_id(mailbox, emails):
    zoom = Zoom(ZoomSettings(), mailbox.holding(emails("sync"), emails("news", subject="Weekly news", sender=NEWS)))
    assert zoom.transcript("sync").meeting.title == "Weekly sync"
    with pytest.raises(HeinzelError, match="news is not a Zoom meeting summary"):
        zoom.transcript("news")


def test_an_empty_window_is_asked_and_a_backwards_one_fails(mailbox, emails):
    zoom = Zoom(ZoomSettings(), mailbox.holding(emails("sync")))
    at = WINDOW.since
    assert zoom.ended(Window(since=at, until=at)) == []
    with pytest.raises(HeinzelError, match="after it ends"):
        zoom.ended(Window(since=WINDOW.until, until=WINDOW.since))


def test_too_many_summaries_ask_for_a_shorter_window(mailbox, emails):
    many = [emails(f"m{i}", received=WINDOW.since + timedelta(minutes=i)) for i in range(MAX_SUMMARIES + 1)]
    with pytest.raises(HeinzelError, match=f"More than {MAX_SUMMARIES} Zoom summaries"):
        Zoom(ZoomSettings(), mailbox.holding(*many)).ended(WINDOW)
    assert mailbox.reads == []


def test_summaries_longer_than_the_window_budget_ask_for_a_shorter_window(mailbox, emails):
    long = "Quick recap\n" + "x" * (SUMMARY_CHARS - 20)
    count = WINDOW_CHARS // len(long) + 1
    many = [emails(f"m{i}", text=long, received=WINDOW.since + timedelta(hours=i)) for i in range(count)]
    with pytest.raises(HeinzelError, match=f"longer than {WINDOW_CHARS} characters together"):
        Zoom(ZoomSettings(), mailbox.holding(*many)).transcripts(WINDOW)


def test_a_message_too_long_to_be_a_summary_fails(mailbox, emails):
    zoom = Zoom(ZoomSettings(), mailbox.holding(emails("big", text="x" * (SUMMARY_CHARS + 1))))
    with pytest.raises(HeinzelError, match=f"longer than {SUMMARY_CHARS} characters, more than a Zoom summary"):
        zoom.transcript("big")


def test_a_message_that_is_no_summary_by_the_time_it_is_read_is_skipped(mailbox, emails):
    box = mailbox.holding(emails("sync"))
    renamed = emails("sync", subject="Weekly news")
    box.read = lambda message: renamed  # the subject changed between listing and reading
    assert Zoom(ZoomSettings(), box).ended(WINDOW) == []
