from datetime import UTC, datetime, timedelta

import pytest
from heinzel_zoom._summary import MAX_LINE, Section, Summary, meeting_times, parse, title_of
from heinzel_zoom._text import _EMAIL
from hypothesis import given, settings
from hypothesis import strategies as st

from heinzel import clean_text

pytestmark = pytest.mark.unit

SUBJECT = "Meeting summary for Weekly sync (10/20/2026)"


def test_a_summary_yields_its_meeting_and_sections(emails):
    content = emails("m1")
    assert parse(content.message.subject, content.text) == Summary(
        title="Weekly sync",
        conference_id="81234567890",
        duration=timedelta(minutes=30),
        people=("Mira Holm", "Jonas Berg"),
        url="https://us06web.zoom.us/meeting/summary/abc123",
        sections=(
            Section(
                "Quick recap",
                (
                    "The team reviewed the release of the reading list and agreed to ship on Thursday.",
                    "Questions go to [address].",
                ),
            ),
            Section(
                "Next steps",
                ("- Jonas Berg will update the release notes.", "- Priya: check the crash reports before Thursday."),
            ),
            Section(
                "Release of the reading list",
                ("The team agreed the release is ready.", "The rollout starts with ten percent of users."),
            ),
            Section("Crash reports", ("Priya found two crashes on older phones.",)),
        ),
    )


@pytest.mark.parametrize(
    ("subject", "title"),
    [
        (SUBJECT, "Weekly sync"),
        ("Meeting summary for Weekly sync", "Weekly sync"),
        ("meeting SUMMARY for  Retro (1.10.2026)", "Retro"),
        ("Meeting Summary: Planning", "Planning"),
        ("Meeting summary - Planning", "Planning"),
        ("Meeting assets for Board prep are ready!", "Board prep"),
        ("Meeting assets for Board prep is ready", "Board prep"),
        ("Fwd: Re: Meeting summary for 1:1 Mira / Jonas", "1:1 Mira / Jonas"),
        ("Meeting summary for \u202eReview\u200b", "Review"),
        ("Your meeting has ended", None),
        ("Cloud recording - Weekly sync is now available", None),
        ("Meeting summary for", None),
        ("Meeting summary for (10/20/2026)", "(10/20/2026)"),
        ("Meeting summary for Call with ada@example.com", "Call with [address]"),
        ("", None),
    ],
)
def test_subjects_tell_summaries_apart(subject, title):
    assert title_of(subject) == title


def test_a_message_that_is_no_summary_is_none():
    assert parse("Weekly newsletter", "Quick recap\nSomething") is None


def test_a_minimal_summary_has_its_title_and_no_sections():
    assert parse(SUBJECT, "") == Summary(title="Weekly sync")
    assert parse(SUBJECT, "Some text before any heading.\n") == Summary(title="Weekly sync")


def test_durations_in_hours_and_minutes():
    assert parse(SUBJECT, "Duration: 1 hour 15 minutes").duration == timedelta(hours=1, minutes=15)
    assert parse(SUBJECT, "Duration: 2h").duration == timedelta(hours=2)
    assert parse(SUBJECT, "Duration: 45 min").duration == timedelta(minutes=45)
    assert parse(SUBJECT, "Duration:").duration is None


def test_meeting_ids_with_dashes_or_spaces_and_bullets_of_every_kind():
    found = parse(SUBJECT, "Meeting ID: 812-3456-7890\nNext steps\n\u2022 one\n1. two\n* three\n")
    assert found.conference_id == "81234567890"
    assert found.sections == (Section("Next steps", ("- one", "- two", "- three")),)


def test_headings_with_a_colon_and_topics_only_inside_summary():
    found = parse(SUBJECT, "Quick recap:\nShort line\nSummary\nTopic one\nDetail.\nAnother topic\nMore.\n")
    assert [s.heading for s in found.sections] == ["Quick recap", "Topic one", "Another topic"]
    assert found.sections[0].lines == ("Short line",)


def test_a_summary_heading_without_topics_keeps_its_lines():
    found = parse(SUBJECT, "Summary\nthe team talked.\n- a point\n")
    assert found.sections == (Section("Summary", ("the team talked.", "- a point")),)


def test_control_characters_are_removed_and_long_lines_cut():
    found = parse(SUBJECT, "Quick recap\nship \x1b[31mit\u202e now" + "x" * (MAX_LINE * 2) + "\n")
    (line,) = found.sections[0].lines
    assert line.startswith("ship [31mit now")
    assert len(line) == MAX_LINE


def test_people_given_as_addresses_are_dropped_and_links_outside_zoom_ignored():
    found = parse(
        SUBJECT, "Attendees: ada@example.com; Mira Holm\nSee https://evil.example/x and https://zoom.us/s/1.\n"
    )
    assert found.people == ("Mira Holm",)
    assert found.url == "https://zoom.us/s/1"


def test_meeting_times_end_when_the_summary_arrived():
    at = datetime(2026, 10, 20, 10, tzinfo=UTC)
    assert meeting_times(at, timedelta(minutes=30)) == (at - timedelta(minutes=30), at)
    assert meeting_times(at, None) == (at, at)


# Properties: a summary rendered from made-up sections parses back; any body parses without failing or leaking.

WORDS = st.lists(
    st.text(alphabet=st.characters(categories=["L", "N"]), min_size=1, max_size=10), min_size=1, max_size=6
)
TOPIC = WORDS.map(" ".join).map(str.capitalize)
SENTENCE = WORDS.map(lambda words: " ".join(words).lower() + ".")


@settings(max_examples=60, deadline=None)
@given(
    st.lists(SENTENCE, min_size=1, max_size=5),
    st.lists(SENTENCE, min_size=1, max_size=5),
    st.lists(st.tuples(TOPIC, st.lists(SENTENCE, min_size=1, max_size=4)), max_size=5),
)
def test_a_rendered_summary_parses_back_to_its_sections(recap, steps, topics):
    topics = [
        (t, s) for t, s in topics if t.casefold() not in {"quick recap", "next steps", "summary"} and t[:1].isupper()
    ]
    body = ["Quick recap", *recap, "", "Next steps", *(f"- {s}" for s in steps), "", "Summary"]
    for topic, sentences in topics:
        body += [topic, *sentences, ""]
    found = parse(SUBJECT, "\n".join(body))
    assert found is not None
    expected = [
        Section("Quick recap", tuple(recap)),
        Section("Next steps", tuple(f"- {s}" for s in steps)),
        *(Section(t, tuple(s)) for t, s in topics),
    ]
    assert list(found.sections) == expected


@settings(max_examples=100, deadline=None)
@given(st.text(max_size=400), st.text(max_size=60))
def test_any_body_parses_and_nothing_unsafe_comes_out(body, title):
    found = parse(f"Meeting summary for {title}x", body)
    assert found is not None
    texts = [found.title, *found.people, *(s.text for s in found.sections)]
    for text in texts:
        assert clean_text(text) == text
        assert _EMAIL.search(text) is None
    assert found.conference_id is None or found.conference_id.isdigit()
    assert found.url is None or found.url.startswith("https://")
