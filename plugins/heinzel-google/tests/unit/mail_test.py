"""The Gmail adapter, against the fake `gws`."""

import base64
import json
from datetime import UTC, datetime
from email import message_from_bytes, policy

import pytest
from heinzel_google import LOGIN_WITH_MAIL, Gmail, Process, _mail
from heinzel_google._mail import gmail_search, html_to_text

from heinzel import HeinzelError, MailDraft, MailQuery, Window

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
    fake_gws.json(("gmail", "users", "getProfile"), {"emailAddress": "ada@example.com"})
    return Gmail(session)


def test_the_search_carries_the_query_and_a_widened_window():
    query = MailQuery.parse('from:zoe@example.com subject:"Meeting summary" label:Summaries')
    assert gmail_search(query, WEEK) == (
        f'after:{int(SINCE.timestamp()) - 1} before:{int(UNTIL.timestamp()) + 1} from:"zoe@example.com"'
    )  # Gmail's subject: matches whole words and label: names differ: both are checked otherwise
    assert gmail_search(MailQuery(sender='a"b\\c.example'), WEEK).endswith('from:"a b c.example"')


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
    assert first["url"] == "https://mail.google.com/mail/#all/m1"
    list_run = next(r for r in fake_gws.runs if r.command == LIST)
    assert list_run.params["userId"] == "me"
    assert list_run.params["q"].endswith('from:"example.com"')
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
    monkeypatch.setattr(_mail, "MAX_HTML", 20)
    fake_gws.json(GET, message("m1", SINCE, mimeType="text/html", body={"data": b64("<p>" + "x" * 30 + "</p>")}))
    with pytest.raises(HeinzelError, match="HTML passes 20 characters"):
        gmail.read("m1")


def test_parts_are_bounded_and_walked_without_recursion(gmail, fake_gws, monkeypatch):
    monkeypatch.setattr(_mail, "MAX_PARTS", 3)
    deep: dict[str, object] = {"mimeType": "text/plain", "body": {"data": b64("deep")}}
    for _ in range(2000):
        deep = {"mimeType": "multipart/mixed", "parts": [deep]}
    fake_gws.json(GET, message("m1", SINCE, **deep))
    with pytest.raises(HeinzelError, match="more than 3 MIME parts"):
        gmail.read("m1")


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


def test_an_encoded_display_name_cannot_pose_as_the_sender(gmail, fake_gws):
    forged = message("m1", SINCE, sender="=?utf-8?q?ceo=40corp.example=2C?= <attacker@evil.example>")
    fake_gws.json(GET, forged)
    assert gmail.read("m1").message.sender.model_dump() == {"name": None, "email": "attacker@evil.example"}
    comma = message("m1", SINCE, sender="=?utf-8?q?M=C3=BCller=2C_Hans?= <hans@example.com>")
    fake_gws.json(GET, comma)
    assert gmail.read("m1").message.sender.model_dump() == {"name": "Müller, Hans", "email": "hans@example.com"}
    bare = message("m1", SINCE, sender="not an address")
    fake_gws.json(GET, bare)
    assert gmail.read("m1").message.sender.email is None


def test_a_body_is_read_in_its_own_charset(gmail, fake_gws):
    latin = base64.urlsafe_b64encode("Grüße".encode("latin-1")).decode()
    part = {"mimeType": "text/plain", "headers": [{"name": "Content-Type", "value": "text/plain; charset=ISO-8859-1"}]}
    fake_gws.json(GET, message("m1", SINCE, **part, body={"data": latin}))
    assert gmail.read("m1").text == "Grüße"
    odd = {"mimeType": "text/plain", "headers": [{"name": "Content-Type", "value": "text/plain; charset=x-unknown"}]}
    fake_gws.json(GET, message("m1", SINCE, **odd, body={"data": b64("plain")}))
    assert gmail.read("m1").text == "plain"


def test_html_with_an_open_head_still_shows_its_body_and_scripts_never_show():
    assert html_to_text("<head><meta charset=utf-8><body>visible text") == "visible text"
    assert html_to_text("<script>steal()</script><style>p{}</style>shown") == "shown"
    # No regex fallback over the raw HTML: an unclosed title hides the rest, and nothing is re-scanned.
    assert "still here" not in html_to_text("<title>unclosed<p>still here")


class Counting(str):
    """A string that counts the searches made in it."""

    __slots__ = ()

    searches = 0

    def find(self, *args: object) -> int:  # type: ignore[override]
        Counting.searches += 1
        return super().find(*args)  # type: ignore[arg-type]


@pytest.mark.parametrize("unit", ["<a", "<", "<p>", "x<b>", "<!--", "<script>", "<a href='"])
def test_html_to_text_is_one_linear_scan(unit):
    """The work grows with the input, not its square: count the searches, not the clock."""
    for size in (1_000, 4_000):
        Counting.searches = 0
        html_to_text(Counting(unit * size))
        assert Counting.searches <= 2 * size + 2


def test_html_to_text_keeps_text_and_breaks_and_decodes_entities_once():
    html = "<html><body><h1>Title</h1>a &amp;lt; b<br/>c<!-- hidden --><template>t</template><p>d</p>e < f"
    assert html_to_text(html) == "Title\na &lt; b\nc\nd\ne"
    assert html_to_text("<script>x</script >after") == "after"
    assert html_to_text("<script/>hidden</script>shown") == "shown"  # `/>` doesn't close a script in HTML
    assert html_to_text("<style>p{}<body>still style</style>shown") == "shown"  # nor does <body>
    assert html_to_text("text <unterminated") == "text"


@pytest.mark.parametrize("charset", ["zlib", "base64", "hex", "rot13", "idna", "unicode_escape", "bogus"])
def test_charsets_that_are_no_text_encoding_fall_back_to_utf8(gmail, fake_gws, charset):
    part = {"mimeType": "text/plain", "headers": [{"name": "Content-Type", "value": f"text/plain; charset={charset}"}]}
    fake_gws.json(GET, message("m1", SINCE, **part, body={"data": b64("Gr\u00fc\u00dfe \\u0041")}))
    assert gmail.read("m1").text == "Gr\u00fc\u00dfe \\u0041"


def test_long_header_values_are_cut_before_decoding(gmail, fake_gws):
    long = message("m1", SINCE)
    long["payload"]["headers"].append({"name": "References", "value": "<x@y> " * 10_000})
    fake_gws.json(GET, long)
    assert len(gmail.read("m1").headers["references"]) <= _mail.MAX_HEADER


def test_spam_and_trash_are_never_read(gmail):
    for label in ("spam", "TRASH"):
        with pytest.raises(HeinzelError, match="Spam or Trash is not read"):
            gmail.messages(f"label:{label}", WEEK)


def test_reading_a_window_has_a_deadline(gmail, fake_gws, monkeypatch):
    monkeypatch.setattr(_mail, "DEADLINE", -1.0)
    fake_gws.json(LIST, {"messages": [{"id": "m1"}]})
    with pytest.raises(HeinzelError, match="takes more than -1 seconds"):
        gmail.messages("", WEEK)


def test_subject_parts_and_labels_are_matched_here(gmail, fake_gws):
    fake_gws.json(LIST, {"messages": [{"id": "m1"}]})
    fake_gws.json(GET, message("m1", datetime(2026, 10, 20, tzinfo=UTC)))
    assert [m.id for m in gmail.messages("subject:meet label:summaries", WEEK)] == ["m1"]
    run = next(r for r in fake_gws.runs if r.command == LIST)
    assert "subject:" not in run.params["q"]
    assert run.params["labelIds"] == ["Label_1"]
    assert gmail.messages("label:nowhere", WEEK) == []


def test_labels_are_read_once_and_repeated_headers_joined(gmail, fake_gws):
    full = message("m1", SINCE, mimeType="text/plain", body={"data": b64("x")})
    full["payload"]["headers"] += [
        {"name": "References", "value": "<a@x>"},
        {"name": "References", "value": "<b@x>"},
        {"value": "nameless"},
    ]
    fake_gws.json(GET, full)
    gmail.read("m1")
    content = gmail.read("m1")
    assert content.headers["references"] == "<a@x>, <b@x>"
    assert [r.command for r in fake_gws.runs].count(LABELS) == 1


@pytest.mark.parametrize("label", ["SPAM", "TRASH"])
def test_a_message_in_spam_or_trash_is_not_read(gmail, fake_gws, label):
    junk = message("m1", SINCE, mimeType="text/plain", body={"data": b64("buy now")})
    junk["labelIds"] = [label]
    fake_gws.json(GET, junk)
    with pytest.raises(HeinzelError, match="in Spam or Trash"):
        gmail.read("m1")


def test_long_address_headers_are_cut_before_parsing(gmail, fake_gws):
    many = message("m1", SINCE)
    many["payload"]["headers"].append({"name": "To", "value": ", ".join(f"p{i}@example.com" for i in range(20_000))})
    fake_gws.json(GET, many)
    assert len(gmail.read("m1").message.recipients) < 1_000


THREAD = ("gmail", "users", "threads", "get")
DRAFT = ("gmail", "users", "drafts", "get")
CREATE = ("gmail", "users", "drafts", "create")
SEND = ("gmail", "users", "drafts", "send")
MODIFY = ("gmail", "users", "threads", "modify")
PROFILE = ("gmail", "users", "getProfile")


def text(message_id: str, when: datetime, body: str, labels: list[str] | None = None, **extra: object):
    found = message(message_id, when, mimeType="text/plain", body={"data": b64(body)}, **extra)
    if labels is not None:
        found["labelIds"] = labels
    return found


def sent(created) -> object:
    """The MIME message a draft was created with, parsed back."""
    raw = created.body["message"]["raw"]
    return message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=policy.default)


def test_a_thread_has_its_messages_oldest_first_without_drafts_spam_or_trash(gmail, fake_gws):
    fake_gws.json(
        THREAD,
        {
            "id": "t1",
            "messages": [
                text("m2", datetime(2026, 10, 21, tzinfo=UTC), "Second"),
                text("m1", datetime(2026, 10, 20, tzinfo=UTC), "First"),
                text("m3", datetime(2026, 10, 22, tzinfo=UTC), "Unsent", labels=["DRAFT"]),
                text("m4", datetime(2026, 10, 22, tzinfo=UTC), "Junk", labels=["SPAM"]),
            ],
        },
    )
    thread = gmail.thread("t1")
    assert [(m.message.id, m.text) for m in thread.messages] == [("m1", "First"), ("m2", "Second")]
    assert thread.subject == "Meeting summary: Plan"
    assert "reply-to" not in thread.messages[0].headers
    assert next(r for r in fake_gws.api_runs if r.command == THREAD).params == {
        "userId": "me",
        "id": "t1",
        "format": "full",
    }


def test_a_thread_only_in_trash_or_too_long_is_refused(gmail, fake_gws, monkeypatch):
    fake_gws.json(THREAD, {"messages": [text("m1", SINCE, "x", labels=["TRASH"])]})
    with pytest.raises(HeinzelError, match="outside Spam and Trash"):
        gmail.thread("t1")
    monkeypatch.setattr(_mail, "MAX_THREAD", 0)
    with pytest.raises(HeinzelError, match="more than 0 messages"):
        gmail.thread("t1")
    with pytest.raises(HeinzelError, match="is not a Gmail conversation id"):
        gmail.thread("../t1")


def test_a_draft_reads_as_it_stands_with_its_link(gmail, fake_gws):
    fake_gws.json(DRAFT, {"id": "r-1", "message": text("m9", SINCE, "Hi Zoe", labels=["DRAFT"])})
    draft = gmail.draft("r-1")
    assert (draft.id, draft.content.text, draft.content.message.id) == ("r-1", "Hi Zoe", "m9")
    assert draft.url == "https://mail.google.com/mail/#drafts?compose=m9"
    fake_gws.json(DRAFT, {"id": "r-1"})
    with pytest.raises(HeinzelError, match="lacks its message"):
        gmail.draft("r-1")


def test_a_new_draft_is_one_plain_text_message_and_is_read_back(gmail, fake_gws):
    fake_gws.json(CREATE, {"id": "r-2", "message": {"id": "m9"}})
    fake_gws.json(DRAFT, {"id": "r-2", "message": text("m9", SINCE, "Hello Zoe", labels=["DRAFT"])})
    draft = gmail.create_draft(
        MailDraft(to=["zoe@example.com"], cc=["grace@example.com"], subject="Plän", text="Hello Zoe\n")
    )
    assert draft.id == "r-2"
    created = next(r for r in fake_gws.api_runs if r.command == CREATE)
    assert created.params == {"userId": "me"}
    assert "threadId" not in created.body["message"]
    mime = sent(created)
    assert (mime["To"], mime["Cc"], mime["Subject"]) == ("zoe@example.com", "grace@example.com", "Plän")
    assert mime.get_content_type() == "text/plain"
    assert mime.get_content() == "Hello Zoe\r\n"  # SMTP's line ends
    assert mime["In-Reply-To"] is None


def test_a_reply_answers_the_reply_to_in_the_same_conversation_with_its_references(gmail, fake_gws):
    original = message("m1", SINCE)
    original["payload"]["headers"] += [
        {"name": "Message-ID", "value": "<abc@mail.example.com>"},
        {"name": "References", "value": "<root@mail.example.com> not-an-id"},
    ]
    fake_gws.json(GET, original)
    fake_gws.json(CREATE, {"id": "r-3"})
    fake_gws.json(DRAFT, {"id": "r-3", "message": text("m9", SINCE, "Thanks", labels=["DRAFT"])})
    gmail.create_draft(MailDraft(text="Thanks", reply_to="m1"))
    get = next(r for r in fake_gws.api_runs if r.command == GET)
    assert "Reply-To" in get.params["metadataHeaders"]
    created = next(r for r in fake_gws.api_runs if r.command == CREATE)
    assert created.body["message"]["threadId"] == "tm1"
    mime = sent(created)
    assert mime["To"] == "hidden@example.com"
    assert mime["Cc"] is None
    assert mime["Subject"] == "Re: Meeting summary: Plan"
    assert mime["In-Reply-To"] == "<abc@mail.example.com>"
    assert mime["References"] == "<root@mail.example.com> <abc@mail.example.com>"


def test_reply_all_copies_the_other_recipients_but_not_you(gmail, fake_gws):
    original = message("m1", SINCE)
    original["payload"]["headers"] = [h for h in original["payload"]["headers"] if h["name"] != "Reply-To"]
    original["payload"]["headers"].append({"name": "Subject", "value": "Re: done"})
    fake_gws.json(GET, original)
    fake_gws.json(PROFILE, {"emailAddress": "Ada@Example.com"})
    fake_gws.json(CREATE, {"id": "r-4"})
    fake_gws.json(DRAFT, {"id": "r-4", "message": text("m9", SINCE, "Thanks", labels=["DRAFT"])})
    gmail.create_draft(MailDraft(text="Thanks", reply_to="m1", reply_all=True, subject="Our plan"))
    mime = sent(next(r for r in fake_gws.api_runs if r.command == CREATE))
    assert mime["To"] == "zoe@example.com"
    assert mime["Cc"] == "grace@example.com, team@example.com"
    assert mime["Subject"] == "Our plan"


def test_a_reply_to_a_message_without_a_sender_goes_nowhere_and_is_refused(gmail, fake_gws):
    original = message("m1", SINCE, sender="")
    original["payload"]["headers"] = [h for h in original["payload"]["headers"] if h["name"] != "Reply-To"]
    fake_gws.json(GET, original)
    with pytest.raises(HeinzelError, match="would go to nobody"):
        gmail.create_draft(MailDraft(text="Thanks", reply_to="m1"))
    assert all(r.command != CREATE for r in fake_gws.api_runs)


def test_sending_a_draft_sends_it_if_it_is_still_what_was_shown(gmail, fake_gws):
    fake_gws.json(DRAFT, {"id": "r-1", "message": text("m9", SINCE, "Hi", labels=["DRAFT"])})
    fake_gws.json(SEND, {"id": "m10", "threadId": "t1", "labelIds": ["SENT"]})
    done = gmail.send_draft("r-1", seen="m9")
    assert (done.id, done.thread, done.url) == ("m10", "t1", "https://mail.google.com/mail/#all/m10")
    (run,) = [r for r in fake_gws.api_runs if r.command == SEND]
    assert (run.params, run.body) == ({"userId": "me"}, {"id": "r-1"})
    fake_gws.json(SEND, {})
    with pytest.raises(HeinzelError, match="look in Sent"):
        gmail.send_draft("r-1", seen="m9")


def test_a_draft_edited_since_it_was_shown_is_not_sent(gmail, fake_gws):
    fake_gws.json(DRAFT, {"id": "r-1", "message": text("m11", SINCE, "Hi, and more", labels=["DRAFT"])})
    with pytest.raises(HeinzelError, match="changed since you saw it; nothing was sent"):
        gmail.send_draft("r-1", seen="m9")
    assert all(r.command != SEND for r in fake_gws.api_runs)


@pytest.mark.parametrize(("draft", "seen"), [("r 1", "m9"), ("../x", "m9"), ("r-1", "m/9")])
def test_malformed_ids_never_reach_a_send(gmail, fake_gws, draft, seen):
    with pytest.raises(HeinzelError, match="is not a Gmail"):
        gmail.send_draft(draft, seen=seen)
    assert fake_gws.api_runs == []


def test_relabelling_archives_and_files_by_label_name(gmail, fake_gws):
    fake_gws.json(MODIFY, {"id": "t1", "messages": [{"labelIds": ["Label_1", "UNREAD"]}, {"labelIds": ["Label_1"]}]})
    filed = gmail.relabel("t1", add=["summaries", " "], remove=["inbox", "INBOX"])
    assert (filed.thread, filed.labels) == ("t1", ["Summaries", "UNREAD"])
    run = fake_gws.api_runs[-1]
    assert (run.params, run.body) == (
        {"userId": "me", "id": "t1"},
        {"addLabelIds": ["Label_1"], "removeLabelIds": ["INBOX"]},
    )


@pytest.mark.parametrize(
    ("add", "remove", "problem"),
    [
        ([], [], "Name a label"),
        (["Nope"], [], "has no label 'Nope'; create it in Gmail first"),
        (["TRASH"], [], "TRASH can't be set or removed here"),
        ([], ["SPAM"], "SPAM can't be set or removed here"),
    ],
)
def test_relabelling_refuses_nothing_unknown_labels_and_spam_or_trash(gmail, fake_gws, add, remove, problem):
    fake_gws.json(
        LABELS,
        {"labels": [{"id": i, "name": i} for i in ("INBOX", "SPAM", "TRASH", "UNREAD")]},
    )
    with pytest.raises(HeinzelError, match=problem):
        gmail.relabel("t1", add=add, remove=remove)
    assert all(r.command != MODIFY for r in fake_gws.api_runs)


def test_a_reply_to_your_own_message_answers_whom_it_went_to(gmail, fake_gws):
    mine = message("m1", SINCE, sender="Ada <ada@example.com>")
    mine["payload"]["headers"] = [h for h in mine["payload"]["headers"] if h["name"] != "Reply-To"]
    fake_gws.json(GET, mine)
    fake_gws.json(CREATE, {"id": "r-5"})
    fake_gws.json(DRAFT, {"id": "r-5", "message": text("m9", SINCE, "Ping", labels=["DRAFT"])})
    gmail.create_draft(MailDraft(text="Ping", reply_to="m1"))
    mime = sent(next(r for r in fake_gws.api_runs if r.command == CREATE))
    assert mime["To"] == "grace@example.com, team@example.com"


def test_a_reply_leaves_out_what_is_no_plain_address_and_refuses_a_crowd(gmail, fake_gws):
    original = message("m1", SINCE, sender='"foo,bar"@example.com, Zoe <zoe@example.com>')
    original["payload"]["headers"] = [h for h in original["payload"]["headers"] if h["name"] != "Reply-To"]
    fake_gws.json(GET, original)
    fake_gws.json(CREATE, {"id": "r-6"})
    fake_gws.json(DRAFT, {"id": "r-6", "message": text("m9", SINCE, "x", labels=["DRAFT"])})
    gmail.create_draft(MailDraft(text="x", reply_to="m1"))
    assert sent(next(r for r in fake_gws.api_runs if r.command == CREATE))["To"] == "zoe@example.com"
    crowd = message("m2", SINCE)
    crowd["payload"]["headers"].append({"name": "Cc", "value": ", ".join(f"p{i}@example.com" for i in range(101))})
    fake_gws.json(GET, crowd)
    with pytest.raises(HeinzelError, match="more than 100 people"):
        gmail.create_draft(MailDraft(text="x", reply_to="m2", reply_all=True))


def test_a_reply_without_a_usable_message_id_or_conversation_carries_neither(gmail, fake_gws):
    original = message("m1", SINCE)
    original.pop("threadId")
    original["payload"]["headers"].append({"name": "Message-ID", "value": "<a b>\r\nBcc: x@example.com"})
    fake_gws.json(GET, original)
    fake_gws.json(CREATE, {"id": "r-7"})
    fake_gws.json(DRAFT, {"id": "r-7", "message": text("m9", SINCE, "x", labels=["DRAFT"])})
    gmail.create_draft(MailDraft(text="x", reply_to="m1"))
    created = next(r for r in fake_gws.api_runs if r.command == CREATE)
    assert "threadId" not in created.body["message"]
    mime = sent(created)
    assert (mime["In-Reply-To"], mime["References"], mime["Bcc"]) == (None, None, None)


def test_a_reply_to_a_message_without_id_or_time_is_refused(gmail, fake_gws):
    broken = message("m1", SINCE)
    broken.pop("internalDate")
    fake_gws.json(GET, broken)
    with pytest.raises(HeinzelError, match="lacks its id or time"):
        gmail.create_draft(MailDraft(text="x", reply_to="m1"))
    assert all(r.command != CREATE for r in fake_gws.api_runs)


def test_a_draft_made_but_not_read_back_says_it_exists(gmail, fake_gws):
    fake_gws.json(CREATE, {})
    with pytest.raises(HeinzelError, match="lacks its id; look in Drafts"):
        gmail.create_draft(MailDraft(to=["zoe@example.com"], subject="s", text="x"))
    fake_gws.json(CREATE, {"id": "r-8"})
    fake_gws.on(DRAFT, Process(1, json.dumps({"error": {"code": 500, "message": "oops"}}).encode()))
    with pytest.raises(HeinzelError, match=r"The draft r-8 was made.*look in Drafts, don't redo it"):
        gmail.create_draft(MailDraft(to=["zoe@example.com"], subject="s", text="x"))


def test_your_own_label_named_like_a_system_one_is_yours_to_set(gmail, fake_gws):
    fake_gws.json(LABELS, {"labels": [{"id": "SPAM", "name": "SPAM"}, {"id": "Label_9", "name": "Spam"}]})
    fake_gws.json(MODIFY, {"id": "t1", "messages": [{"labelIds": ["Label_9"]}]})
    assert gmail.relabel("t1", add=["spam"], remove=[]).labels == ["Spam"]
    assert fake_gws.api_runs[-1].body == {"addLabelIds": ["Label_9"], "removeLabelIds": []}
