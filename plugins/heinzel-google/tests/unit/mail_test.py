"""The Gmail adapter, against the fake `gws`."""

import base64
import json
from datetime import UTC, datetime

import pytest
from heinzel_google import LOGIN_WITH_MAIL, Gmail, Process, _mail
from heinzel_google._mail import gmail_search, html_to_text

from heinzel import HeinzelError, MailQuery, Window

pytestmark = pytest.mark.unit

LIST = ("gmail", "users", "messages", "list")
GET = ("gmail", "users", "messages", "get")
LABELS = ("gmail", "users", "labels", "list")
SINCE = datetime(2026, 10, 19, tzinfo=UTC)
UNTIL = datetime(2026, 10, 26, tzinfo=UTC)
WEEK = Window(since=SINCE, until=UNTIL)


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def message(message_id: str, when: datetime, *, sender: str = "Zoe Example <zoe@example.com>", **extra: object):
    headers = [
        {"name": "From", "value": sender},
        {"name": "To", "value": "ada@example.com, =?utf-8?q?Gr=C3=A4ce?= <grace@example.com>"},
        {"name": "Cc", "value": "team@example.com"},
        {"name": "Subject", "value": "Meeting summary: =?utf-8?b?UGxhbg==?="},
        {"name": "Reply-To", "value": "hidden@example.com"},
        {"name": "List-Id", "value": "<team.example.com>"},
    ]
    return {
        "id": message_id,
        "threadId": "t" + message_id,
        "labelIds": ["INBOX", "Label_1"],
        "internalDate": str(int(when.timestamp() * 1000)),
        "payload": {"headers": headers, **extra},
    }


@pytest.fixture
def gmail(session, fake_gws):
    fake_gws.json(LABELS, {"labels": [{"id": "INBOX", "name": "INBOX"}, {"id": "Label_1", "name": "Summaries"}]})
    return Gmail(session)


def test_the_search_carries_the_query_and_a_widened_window():
    query = MailQuery.parse('from:zoe@example.com subject:"Meeting summary" label:Summaries')
    assert gmail_search(query, WEEK) == (
        f"after:{int(SINCE.timestamp()) - 1} before:{int(UNTIL.timestamp()) + 1} "
        'from:"zoe@example.com" subject:"Meeting summary" label:"Summaries"'
    )
    assert gmail_search(MailQuery(subject='a "b\\c'), WEEK).endswith('subject:"a  b c"')


def test_messages_in_the_window_that_match_oldest_first(gmail, fake_gws):
    fake_gws.json(
        LIST, {"messages": [{"id": "m2"}, {"id": "m1"}, {"id": "bad id"}, {"id": "m3"}], "nextPageToken": "p"}
    )
    fake_gws.json(LIST, {"messages": [{"id": "m4"}, {"id": "m1"}]}, page="p")
    fake_gws.on(
        GET,
        Process(0, json.dumps(message("m2", datetime(2026, 10, 21, tzinfo=UTC))).encode()),
        Process(0, json.dumps(message("m1", datetime(2026, 10, 20, tzinfo=UTC))).encode()),
        Process(0, json.dumps(message("m3", datetime(2026, 10, 27, tzinfo=UTC))).encode()),  # after the window
        Process(0, json.dumps(message("m4", datetime(2026, 10, 22, tzinfo=UTC), sender="x@other.example")).encode()),
    )
    found = gmail.messages('from:example.com subject:"meeting summary"', WEEK)
    assert [m.id for m in found] == ["m1", "m2"]
    first = found[0].model_dump()
    assert first["sender"] == {"name": "Zoe Example", "email": "zoe@example.com"}
    assert first["recipients"] == [
        {"name": None, "email": "ada@example.com"},
        {"name": "Gräce", "email": "grace@example.com"},
        {"name": None, "email": "team@example.com"},
    ]
    assert (first["subject"], first["labels"], first["thread"]) == (
        "Meeting summary: Plan",
        ["INBOX", "Summaries"],
        "tm1",
    )
    assert first["url"] == "https://mail.google.com/mail/u/0/#all/m1"
    list_run = next(r for r in fake_gws.runs if r.command == LIST)
    assert list_run.params["userId"] == "me"
    assert 'subject:"meeting summary"' in list_run.params["q"]
    get_run = next(r for r in fake_gws.runs if r.command == GET)
    assert get_run.params == {
        "userId": "me",
        "id": "m2",
        "format": "metadata",
        "metadataHeaders": ["From", "To", "Cc", "Subject"],
    }


def test_too_many_messages_ask_for_a_narrower_window(gmail, fake_gws, monkeypatch):
    monkeypatch.setattr(_mail, "MAX_MESSAGES", 1)
    fake_gws.json(LIST, {"messages": [{"id": "a"}, {"id": "b"}]})
    with pytest.raises(HeinzelError, match="more than 1 matching messages"):
        gmail.messages("", WEEK)


def test_malformed_queries_windows_and_ids(gmail, fake_gws):
    with pytest.raises(HeinzelError, match="must be terms like"):
        gmail.messages("to:someone", WEEK)
    with pytest.raises(HeinzelError, match="after it ends"):
        gmail.messages("", Window(since=UNTIL, until=SINCE))
    assert gmail.messages("", Window(since=SINCE, until=SINCE)) == []
    with pytest.raises(HeinzelError, match="is not a Gmail message id"):
        gmail.read("../etc")
    assert fake_gws.runs == []


def test_messages_without_an_id_or_time_are_skipped(gmail, fake_gws):
    fake_gws.json(LIST, {"messages": [{"id": "m1"}, {"id": "m2"}]})
    broken = message("m2", SINCE)
    broken["internalDate"] = "soon"
    fake_gws.on(GET, Process(0, json.dumps({"id": "bad id"}).encode()), Process(0, json.dumps(broken).encode()))
    assert gmail.messages("", WEEK) == []


def test_read_takes_the_text_part_and_drops_address_headers(gmail, fake_gws):
    full = message(
        "m1",
        datetime(2026, 10, 20, tzinfo=UTC),
        mimeType="multipart/mixed",
        parts=[
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": b64("Decisions:\r\n- ship\u202e it\r\n")}},
                    {"mimeType": "text/html", "body": {"data": b64("<p>ignored</p>")}},
                ],
            },
            {"mimeType": "text/plain", "filename": "notes.txt", "body": {"attachmentId": "x"}},
        ],
    )
    fake_gws.json(GET, full)
    content = gmail.read("m1")
    assert content.text == "Decisions:\n- ship it\n"
    assert content.headers == {"subject": "Meeting summary: Plan", "list-id": "<team.example.com>"}
    assert content.message.id == "m1"
    assert next(r for r in fake_gws.runs if r.command == GET).params["format"] == "full"


def test_read_falls_back_to_html_as_text(gmail, fake_gws):
    html = (
        "<html><head><style>p{}</style></head><body><h1>Summary</h1><p>Ship &amp; tell</p><script>x()</script></body>"
    )
    fake_gws.json(GET, message("m1", SINCE, mimeType="text/html", body={"data": b64(html)}))
    assert gmail.read("m1").text == "Summary\n\nShip & tell"


def test_read_without_a_body_or_with_broken_data(gmail, fake_gws):
    fake_gws.json(GET, message("m1", SINCE, mimeType="text/plain", body={"data": "!!!"}))
    assert gmail.read("m1").text == ""
    fake_gws.json(GET, {"id": "m1"})
    with pytest.raises(HeinzelError, match="lacks its id or time"):
        gmail.read("m1")


def test_a_too_long_message_is_refused_not_cut(gmail, fake_gws, monkeypatch):
    monkeypatch.setattr(_mail, "MAX_TEXT", 5)
    fake_gws.json(GET, message("m1", SINCE, mimeType="text/plain", body={"data": b64("too long")}))
    with pytest.raises(HeinzelError, match="passes 5 characters"):
        gmail.read("m1")
    fake_gws.json(GET, message("m1", SINCE, mimeType="text/html", body={"data": b64("<p>" + "x" * 30 + "</p>")}))
    with pytest.raises(HeinzelError, match="HTML passes 20 characters"):
        gmail.read("m1")


def test_parts_are_bounded_and_walked_without_recursion(gmail, fake_gws, monkeypatch):
    monkeypatch.setattr(_mail, "MAX_PARTS", 3)
    deep: dict[str, object] = {"mimeType": "text/plain", "body": {"data": b64("deep")}}
    for _ in range(2000):
        deep = {"mimeType": "multipart/mixed", "parts": [deep]}
    fake_gws.json(GET, message("m1", SINCE, **deep))
    assert gmail.read("m1").text == ""


def test_a_malformed_encoded_word_stays_as_it_is(gmail, fake_gws):
    fake_gws.json(GET, message("m1", SINCE, sender="=?bogus-charset?q?Zoe?= <zoe@example.com>"))
    assert gmail.read("m1").message.sender.model_dump() == {
        "name": "=?bogus-charset?q?Zoe?=",
        "email": "zoe@example.com",
    }


def test_not_signed_in_for_gmail_names_the_login_with_its_scope(gmail, fake_gws):
    fake_gws.on(GET, Process(2, json.dumps({"error": {"code": 401, "reason": "authError"}}).encode()))
    with pytest.raises(HeinzelError) as caught:
        gmail.read("m1")
    assert str(caught.value).endswith(LOGIN_WITH_MAIL)


def test_a_message_without_sender_or_body_and_with_repeated_headers(gmail, fake_gws):
    bare = message("m1", SINCE, mimeType="multipart/mixed", parts=[{"mimeType": "text/plain", "body": {"size": 0}}])
    headers = bare["payload"]["headers"]
    bare["payload"]["headers"] = [h for h in headers if h["name"] != "From"] + [{"name": "Subject", "value": "second"}]
    fake_gws.json(GET, bare)
    content = gmail.read("m1")
    assert content.message.sender.model_dump() == {"name": None, "email": None}
    assert (content.message.subject, content.text) == ("Meeting summary: Plan", "")


def test_html_to_text_tidies_whitespace():
    assert html_to_text("<div>a  <b>b</b></div><br><br><br><br><p>c</p>") == "a b\n\nc"
