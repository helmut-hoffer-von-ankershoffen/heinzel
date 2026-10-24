"""The mail capability's models and its query syntax (ADR 0028), in `heinzel.kernel._protocols`."""

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from heinzel import MailContent, MailMessage, MailQuery, Participant
from heinzel.kernel._protocols import ADDRESS_HEADERS

pytestmark = pytest.mark.unit

MESSAGE = MailMessage(
    id="m1",
    sender=Participant(name="Meeting tool", email="noreply@meet.example"),
    subject="Meeting summary for Weekly sync",
    received_at=datetime(2026, 10, 20, 10, tzinfo=UTC),
)


def test_content_headers_never_carry_addresses_and_are_lower_case():
    headers = {name.title(): "ada@example.com" for name in ADDRESS_HEADERS}
    headers |= {"Resent-From": "x@example.com", "Date": "Tue, 20 Oct 2026", "List-Id": "<team.example>"}
    content = MailContent(message=MESSAGE, text="Hello", headers=headers)
    assert content.headers == {"date": "Tue, 20 Oct 2026", "list-id": "<team.example>"}


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", MailQuery()),
        ("   ", MailQuery()),
        ("from:NoReply@Meet.Example", MailQuery(sender="noreply@meet.example")),
        (
            'from:meet.example subject:"Meeting summary" label:INBOX',
            MailQuery(sender="meet.example", subject="Meeting summary", label="INBOX"),
        ),
        ('label:"Team mail"  subject:digest', MailQuery(subject="digest", label="Team mail")),
    ],
)
def test_queries_parse(query, expected):
    assert MailQuery.parse(query) == expected


@pytest.mark.parametrize(
    ("query", "problem"),
    [
        ("summary", "must be terms like"),
        ("to:ada@example.com", "must be terms like"),
        ("FROM:x.example", "must be terms like"),
        ("from:", "must be terms like"),
        ('subject:"unclosed', "must be terms like"),
        ('subject:a"b', "must be terms like"),
        ("from:a.example from:b.example", "sets `from:` twice"),
        ('subject:"  "', "gives `subject:` no value"),
    ],
)
def test_malformed_queries_say_what_works(query, problem):
    with pytest.raises(ValueError, match=problem):
        MailQuery.parse(query)


@pytest.mark.parametrize(
    ("query", "sender", "subject", "labels", "expected"),
    [
        ("", None, "", [], True),
        ("from:noreply@meet.example", "NoReply@meet.example", "x", [], True),
        ("from:noreply@meet.example", "other@meet.example", "x", [], False),
        ("from:meet.example", "noreply@meet.example", "x", [], True),
        ("from:meet.example", "noreply@eu.meet.example", "x", [], True),
        ("from:meet.example", "noreply@evilmeet.example", "x", [], False),
        ("from:meet.example", None, "x", [], False),
        ('subject:"meeting SUMMARY"', None, "Meeting summary for Retro", [], True),
        ("subject:digest", None, "Meeting summary", [], False),
        ("label:inbox", None, "", ["INBOX", "Work"], True),
        ("label:archive", None, "", ["INBOX"], False),
    ],
)
def test_matches(query, sender, subject, labels, expected):
    assert MailQuery.parse(query).matches(sender, subject, labels) is expected


VALUE = st.text(alphabet=st.characters(exclude_characters='"', exclude_categories=["Cs"]), min_size=1).filter(str.strip)


@given(VALUE, VALUE)
def test_quoted_values_round_trip(subject, label):
    query = f'subject:"{subject}" label:"{label}"'
    assert MailQuery.parse(query) == MailQuery(subject=subject.strip(), label=label.strip())


@given(st.text(max_size=80))
def test_any_text_parses_or_raises_value_error_and_a_query_matches_its_own_terms(text):
    try:
        query = MailQuery.parse(text)
    except ValueError:
        return
    labels = [query.label] if query.label else []
    assert query.matches(query.sender, query.subject or "", labels)
