from datetime import UTC, datetime, timedelta

import pytest
from heinzel_slack import TOKEN as TOKEN_LET
from heinzel_slack import Slack, SlackError, SlackSettings, Transport
from heinzel_slack._adapter import MAX_MESSAGES, MAX_POST_CHARS, MAX_THREADS, _time, _ts

from heinzel import Credentials, HeinzelError, Window
from heinzel.testing import MemoryBackend, assert_adapter

pytestmark = pytest.mark.unit

TOKEN = "xoxp-test-token-never-shown"
CHANNEL = "C0123456789"

SINCE = datetime(2026, 10, 1, tzinfo=UTC)
UNTIL = datetime(2026, 10, 8, tzinfo=UTC)
WINDOW = Window(since=SINCE, until=UNTIL)


def at(moment: datetime, micros: int = 0) -> str:
    return _ts(moment + timedelta(microseconds=micros))


def adapter(fake: Transport, token: str | None = TOKEN, **settings: object) -> Slack:
    credentials = Credentials(MemoryBackend(), [("slack", TOKEN_LET)])
    if token is not None:
        credentials.set(TOKEN_LET, token)
    return Slack(SlackSettings(**settings), credentials, fake)


def test_the_adapter_implements_chat_in_both_modes():
    assert_adapter(Slack, "chat", modes=("read", "write"))


def test_messages_of_the_window_oldest_first_half_open(fake_slack):
    fake_slack.history = [
        fake_slack.message(at(SINCE, -1), "before"),
        fake_slack.message(at(SINCE), "first"),
        fake_slack.message(at(SINCE + timedelta(days=2)), "second", user="U0GRACE"),
        fake_slack.message(at(UNTIL), "after"),
    ]
    found = adapter(fake_slack).messages(CHANNEL, WINDOW)
    assert [(m.text, m.author.name, m.author.email) for m in found] == [
        ("first", "Ada", "ada@acme.example"),
        ("second", "Grace H.", None),
    ]
    first = found[0]
    assert first.channel == CHANNEL
    assert first.id == at(SINCE)
    assert first.posted_at == SINCE
    assert first.url == f"https://acme.slack.com/archives/{CHANNEL}/p{at(SINCE).replace('.', '')}"
    assert first.thread is None


def test_history_is_asked_from_the_lookback_to_the_end_of_the_window(fake_slack):
    adapter(fake_slack, thread_lookback_days=10).messages(CHANNEL, WINDOW)
    history = next(r for r in fake_slack.requests if r.method == "conversations.history")
    assert history.query["oldest"] == at(SINCE - timedelta(days=10))
    assert history.query["latest"] == at(UNTIL)
    assert history.query["inclusive"] == "true"
    assert history.headers["Authorization"] == f"Bearer {TOKEN}"


def test_replies_in_the_window_come_with_their_thread_also_when_the_parent_is_older(fake_slack):
    old_parent, new_parent = at(SINCE - timedelta(days=3)), at(SINCE + timedelta(hours=1))
    fake_slack.history = [
        fake_slack.message(old_parent, "old question"),
        fake_slack.message(new_parent, "new question"),
    ]
    fake_slack.thread(
        old_parent,
        fake_slack.message(at(SINCE - timedelta(days=2)), "old answer"),
        fake_slack.message(at(SINCE + timedelta(hours=2)), "late answer", user="U0GRACE"),
    )
    fake_slack.thread(new_parent, fake_slack.message(at(SINCE + timedelta(hours=3)), "quick answer"))
    found = adapter(fake_slack).messages(CHANNEL, WINDOW)
    assert [(m.text, m.thread, m.reply_count) for m in found] == [
        ("new question", None, 1),
        ("late answer", old_parent, 0),
        ("quick answer", new_parent, 0),
    ]
    late = found[1]
    assert late.url is not None
    assert late.url.endswith(f"?thread_ts={old_parent}&cid={CHANNEL}")


def test_threads_whose_latest_reply_is_before_the_window_are_not_read(fake_slack):
    parent = at(SINCE - timedelta(days=5))
    fake_slack.history = [fake_slack.message(parent, "question")]
    fake_slack.thread(parent, fake_slack.message(at(SINCE - timedelta(days=4)), "answer"))
    assert adapter(fake_slack).messages(CHANNEL, WINDOW) == []
    assert "conversations.replies" not in fake_slack.methods()


def test_a_broadcast_reply_in_history_and_its_thread_counts_once(fake_slack):
    parent, reply = at(SINCE + timedelta(hours=1)), at(SINCE + timedelta(hours=2))
    fake_slack.history = [fake_slack.message(parent, "question")]
    fake_slack.thread(parent, fake_slack.message(reply, "answer", subtype="thread_broadcast"))
    fake_slack.history.append({**fake_slack.threads[parent][0]})
    found = adapter(fake_slack).messages(CHANNEL, WINDOW)
    assert [m.id for m in found] == [parent, reply]


def test_channel_events_and_hidden_messages_are_left_out(fake_slack):
    fake_slack.history = [
        fake_slack.message(at(SINCE, 1), "joined", subtype="channel_join"),
        fake_slack.message(at(SINCE, 2), "hidden", hidden=True),
        fake_slack.message(at(SINCE, 3), "said"),
    ]
    assert [m.text for m in adapter(fake_slack).messages(CHANNEL, WINDOW)] == ["said"]


def test_bots_are_named_by_their_profile_or_username(fake_slack):
    fake_slack.history = [
        fake_slack.message(
            at(SINCE, 1), "from a bot", user=None, subtype="bot_message", bot_profile={"name": "Deploy\u202ebot"}
        ),
        fake_slack.message(at(SINCE, 2), "from a hook", user=None, subtype="bot_message", username="hook"),
        fake_slack.message(at(SINCE, 3), "from nobody", user=None),
    ]
    found = adapter(fake_slack).messages(CHANNEL, WINDOW)
    assert [m.author.name for m in found] == ["Deploybot", "hook", None]
    assert "users.info" not in fake_slack.methods()


def test_mentions_become_names_and_unknown_people_stay_anonymous(fake_slack):
    fake_slack.history = [
        fake_slack.message(at(SINCE), "<@U0GRACE> and <@U0GONE|gone>: see <#C0OTHER0000|random> <!here>")
    ]
    found = adapter(fake_slack).messages(CHANNEL, WINDOW)
    assert found[0].text == "@Grace H. and @someone: see #random @here"
    assert sorted(r.query["user"] for r in fake_slack.requests if r.method == "users.info") == [
        "U0ADA",
        "U0GONE",
        "U0GRACE",
    ]


def test_an_author_without_a_profile_is_named_by_their_handle(fake_slack):
    fake_slack.users["U0ADA"] = {"id": "U0ADA", "name": "ada", "profile": "odd"}
    fake_slack.queue("users.info", fake_slack.ok(user={"id": "U0ADA", "name": "ada", "profile": "odd"}))
    fake_slack.history = [fake_slack.message(at(SINCE))]
    assert adapter(fake_slack).messages(CHANNEL, WINDOW)[0].author.name == "ada"


def test_a_user_answer_without_a_user_leaves_the_author_unnamed(fake_slack):
    fake_slack.queue("users.info", fake_slack.ok(user="nobody"))
    fake_slack.history = [fake_slack.message(at(SINCE))]
    assert adapter(fake_slack).messages(CHANNEL, WINDOW)[0].author.name is None


def test_an_email_that_is_no_address_is_dropped(fake_slack):
    fake_slack.users["U0ADA"]["profile"] = {"display_name": "Ada", "email": "not-an-address"}
    fake_slack.history = [fake_slack.message(at(SINCE))]
    assert adapter(fake_slack).messages(CHANNEL, WINDOW)[0].author.email is None


def test_a_missing_scope_for_names_fails_with_the_scope(fake_slack):
    fake_slack.queue("users.info", fake_slack.error("missing_scope", needed="users:read"))
    fake_slack.history = [fake_slack.message(at(SINCE))]
    with pytest.raises(SlackError, match="lacks the scope users:read"):
        adapter(fake_slack).messages(CHANNEL, WINDOW)


def test_a_channel_name_is_resolved_and_results_carry_the_id(fake_slack):
    fake_slack.history = [fake_slack.message(at(SINCE))]
    found = adapter(fake_slack).messages("#widgets", WINDOW)
    assert [m.channel for m in found] == [CHANNEL]
    assert fake_slack.methods()[0] == "conversations.list"


def test_an_unknown_channel_name_fails_and_says_what_to_check(fake_slack):
    with pytest.raises(HeinzelError, match="no channel #nope the token can see"):
        adapter(fake_slack).messages("#nope", WINDOW)


@pytest.mark.parametrize("channel", ["general", "#General", "D0123456789", "C01", "#a b", ""])
def test_malformed_channels_fail_before_any_request(fake_slack, channel):
    with pytest.raises(HeinzelError, match="is not a Slack channel"):
        adapter(fake_slack).messages(channel, WINDOW)
    assert fake_slack.requests == []


def test_an_empty_window_asks_nothing(fake_slack):
    assert adapter(fake_slack).messages(CHANNEL, Window(since=SINCE, until=SINCE)) == []
    assert fake_slack.requests == []


def test_a_window_that_ends_before_it_starts_fails(fake_slack):
    with pytest.raises(HeinzelError, match="after it ends"):
        adapter(fake_slack).messages(CHANNEL, Window(since=UNTIL, until=SINCE))


def test_windows_in_other_zones_mean_the_same_instants(fake_slack):
    zone = timedelta(hours=2)
    from datetime import timezone

    local = Window(since=SINCE.astimezone(timezone(zone)), until=UNTIL.astimezone(timezone(zone)))
    fake_slack.history = [fake_slack.message(at(SINCE), "first")]
    assert [m.text for m in adapter(fake_slack).messages(CHANNEL, local)] == ["first"]


def test_a_message_without_a_valid_ts_fails(fake_slack):
    fake_slack.history = [fake_slack.message("1790812800.5")]
    fake_slack.queue("conversations.history", fake_slack.ok(messages=[{"text": "no ts"}], response_metadata={}))
    with pytest.raises(HeinzelError, match="without a valid ts"):
        adapter(fake_slack).messages(CHANNEL, WINDOW)


def test_too_many_threads_fail_with_advice(fake_slack, monkeypatch):
    monkeypatch.setattr("heinzel_slack._adapter.MAX_THREADS", 1)
    fake_slack.page = 10
    for hour in (1, 2):
        parent = at(SINCE + timedelta(hours=hour))
        fake_slack.history.append(fake_slack.message(parent))
        fake_slack.thread(parent, fake_slack.message(at(SINCE + timedelta(hours=hour, minutes=1))))
    with pytest.raises(HeinzelError, match=r"more than 1 threads.*lower slack.thread_lookback_days"):
        adapter(fake_slack).messages(CHANNEL, WINDOW)
    assert MAX_THREADS == 300


def test_too_many_messages_fail_with_advice(fake_slack, monkeypatch):
    monkeypatch.setattr("heinzel_slack._adapter.MAX_MESSAGES", 2)
    fake_slack.history = [fake_slack.message(at(SINCE, n)) for n in range(3)]
    with pytest.raises(HeinzelError, match="more than 2 messages; ask for a shorter window"):
        adapter(fake_slack).messages(CHANNEL, WINDOW)
    assert MAX_MESSAGES == 20_000


def test_a_workspace_address_off_slack_gives_no_links(fake_slack):
    fake_slack.workspace = "https://evil.example/"
    fake_slack.history = [fake_slack.message(at(SINCE))]
    assert adapter(fake_slack).messages(CHANNEL, WINDOW)[0].url is None


def test_reading_needs_a_token(fake_slack):
    with pytest.raises(HeinzelError, match="needs a token; there is no anonymous access"):
        adapter(fake_slack, token=None).messages(CHANNEL, WINDOW)
    assert fake_slack.requests == []


def test_timestamps_round_trip_exactly():
    moment = datetime(2026, 10, 1, 12, 30, 15, 123456, tzinfo=UTC)
    assert _ts(moment) == "1790857815.123456"
    assert _time(_ts(moment)) == moment


# Posting


def test_post_sends_neutralised_markup_without_previews(fake_slack):
    posted = adapter(fake_slack).post(CHANNEL, "<!channel> **Ship it** @here & [notes](https://acme.example/n)")
    payload = fake_slack.posted[0]
    assert payload == {
        "channel": CHANNEL,
        "text": "&lt;!channel&gt; *Ship it* @\u2060here &amp; <https://acme.example/n|notes>",
        "parse": "none",
        "link_names": False,
        "unfurl_links": False,
        "unfurl_media": False,
    }
    assert posted.channel == CHANNEL
    assert posted.id == "1761000000.000001"
    assert posted.thread is None
    assert posted.url == f"https://acme.slack.com/archives/{CHANNEL}/p1761000000000001"
    post = next(r for r in fake_slack.requests if r.method == "chat.postMessage")
    assert post.headers["Content-Type"] == "application/json; charset=utf-8"
    assert fake_slack.methods() == ["auth.test", "chat.postMessage"]  # the link is known before posting


def test_post_replies_in_a_thread_to_a_named_channel(fake_slack):
    thread = "1790812800.000100"
    posted = adapter(fake_slack).post("#widgets", "Done.", thread=thread)
    assert fake_slack.posted[0]["thread_ts"] == thread
    assert fake_slack.posted[0]["channel"] == CHANNEL
    assert posted.thread == thread
    assert posted.url is not None
    assert posted.url.endswith(f"?thread_ts={thread}&cid={CHANNEL}")


@pytest.mark.parametrize("thread", ["", "1790812800", "abc", "1790812800.1"])
def test_post_refuses_a_malformed_thread_before_any_request(fake_slack, thread):
    with pytest.raises(HeinzelError, match="is not a Slack thread id"):
        adapter(fake_slack).post(CHANNEL, "x", thread=thread)
    assert fake_slack.requests == []


@pytest.mark.parametrize("text", ["", "  \n"])
def test_post_refuses_an_empty_text(fake_slack, text):
    with pytest.raises(HeinzelError, match="no text to post"):
        adapter(fake_slack).post(CHANNEL, text)
    assert fake_slack.requests == []


def test_post_refuses_a_text_slack_would_cut(fake_slack):
    with pytest.raises(HeinzelError, match="post it in parts"):
        adapter(fake_slack).post(CHANNEL, "<" * (MAX_POST_CHARS // 4 + 1))  # each `<` is four characters escaped
    assert fake_slack.requests == []


def test_post_to_an_archived_channel_fails_with_the_reason(fake_slack):
    fake_slack.queue("chat.postMessage", fake_slack.error("is_archived"))
    with pytest.raises(HeinzelError, match="archived"):
        adapter(fake_slack).post(CHANNEL, "hello")


def test_post_without_a_ts_in_the_answer_fails(fake_slack):
    fake_slack.queue("chat.postMessage", fake_slack.ok(channel=CHANNEL))
    with pytest.raises(HeinzelError, match="lacks the new message's ts"):
        adapter(fake_slack).post(CHANNEL, "hello")


def test_post_needs_a_token_of_the_configured_kind(fake_slack):
    with pytest.raises(HeinzelError, match="not a bot token"):
        adapter(fake_slack, token_kind="bot").post(CHANNEL, "hello")
    assert adapter(fake_slack, token="xoxb-bot", token_kind="bot").post(CHANNEL, "hello").id
