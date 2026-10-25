"""The calendar capability's write models (ADR 0028), in `heinzel.kernel._protocols`: checked whatever the adapter."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from heinzel import EventChange, EventDraft, free_busy_people

pytestmark = pytest.mark.unit

TEN = datetime(2026, 10, 20, 10, tzinfo=UTC)
HOUR = timedelta(hours=1)


def test_a_draft_keeps_plain_addresses_lower_case_and_once():
    draft = EventDraft(title=" Plan ", start=TEN, end=TEN + HOUR, attendees=["Ada@Example.com", " ada@example.com"])
    assert (draft.title, draft.attendees) == ("Plan", ["ada@example.com"])


@pytest.mark.parametrize(
    ("fields", "problem"),
    [
        ({"title": ""}, "at least 1 character"),
        ({"title": "   "}, "needs a title"),
        ({"title": "a\nb"}, "must be one line"),
        ({"title": "x" * 301}, "more than 300 characters"),
        ({"location": "Room\r\n4"}, "must be one line"),
        ({"title": "a\u2028b"}, "must be one line"),
        ({"title": "a\x0bb"}, "must be one line"),
        ({"title": "a\x85b"}, "must be one line"),
        ({"description": "x" * 20_001}, "more than 20,000 characters"),
        ({"end": TEN}, "ends .* before it starts"),
        ({"attendees": ["Ada <ada@example.com>"]}, "is not an email address"),
        ({"attendees": ["ada@example.com\r\nBcc: zoe@example.com"]}, "is not an email address"),
        ({"attendees": [f"p{i}@example.com" for i in range(101)]}, "at most 100 addresses"),
    ],
)
def test_a_draft_refuses_what_could_mislead_or_overflow(fields, problem):
    with pytest.raises(ValidationError, match=problem):
        EventDraft(**{"title": "Plan", "start": TEN, "end": TEN + HOUR, **fields})


def test_a_change_must_change_something_and_fit():
    with pytest.raises(ValidationError, match="changes nothing"):
        EventChange(notify=False)
    with pytest.raises(ValidationError, match="would end"):
        EventChange(start=TEN, end=TEN - HOUR)
    with pytest.raises(ValidationError, match="can't be empty"):
        EventChange(title=" ")
    with pytest.raises(ValidationError, match="must be one line"):
        EventChange(location="a\nb")
    with pytest.raises(ValidationError, match="more than 20,000"):
        EventChange(description="x" * 20_001)
    assert EventChange(attendees=[]).attendees == []
    assert EventChange(attendees=["Zoe@Example.com"], description="").attendees == ["zoe@example.com"]


def test_free_busy_people_are_comma_separated_plain_addresses():
    assert free_busy_people(" Grace@Example.com, ,zoe@example.com,grace@example.com") == [
        "grace@example.com",
        "zoe@example.com",
    ]
    assert free_busy_people("") == []
    with pytest.raises(ValueError, match="is not an email address"):
        free_busy_people("grace")
    assert EventChange(location=None, title="x").location is None
