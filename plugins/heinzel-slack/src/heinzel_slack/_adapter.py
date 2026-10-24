"""The Slack adapter of the `chat` capability (ADR 0028): `messages` reads, `post` writes.

Messages of a window come from `conversations.history`, replies from `conversations.replies` of every thread whose
latest reply falls in the window, among the parents posted since `slack.thread_lookback_days` before it (ADR 0034).
Authors and mentions are named through `users.info`; a `#name` is resolved through `conversations.list`.
"""

import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from heinzel import Credentials, HeinzelError, Message, Participant, PostedMessage, Window, clean_text
from heinzel_slack._client import SlackClient, SlackError, connect
from heinzel_slack._models import SlackSettings
from heinzel_slack._text import MENTIONS, to_markup, to_text
from heinzel_slack._transport import Transport

PAGE = "200"
"""Items per page, Slack's recommended maximum."""
MAX_MESSAGES = 20_000
"""Messages one `messages` call keeps before asking for a shorter window."""
MAX_THREADS = 300
"""Threads one `messages` call reads replies of before asking for a shorter window."""
MAX_POST_CHARS = 40_000
"""Longest text `post` sends, as Slack markup; Slack cuts longer ones, so they are refused instead."""
MAX_NAME_CHARS = 200
"""Longest display name kept."""
KEPT = frozenset({None, "bot_message", "me_message", "thread_broadcast", "file_share"})
"""Subtypes that are things people said; joins, topic changes and other channel events are left out."""
_ID = re.compile(r"^[CG][A-Z0-9]{8,30}$")
_NAME = re.compile(r"^#[a-z0-9][a-z0-9._-]{0,79}$")
_TS = re.compile(r"^[0-9]{9,12}\.[0-9]{6}$")
_WORKSPACE = re.compile(r"^https://[a-z0-9][a-z0-9-]{0,62}(?:\.enterprise)?\.slack\.com/$")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)


class Slack:
    """Reads and posts messages of Slack channels, given as an id (`C0123456789`) or a `#name`."""

    def __init__(self, settings: SlackSettings, credentials: Credentials, transport: Transport) -> None:
        """Build the adapter; the kernel injects the settings, the credentials and the transport."""
        self._settings = settings
        self._credentials = credentials
        self._transport = transport

    def messages(self, channel: str, window: Window) -> list[Message]:
        """Messages posted in `channel` within `window`, top-level or reply, oldest first.

        A reply whose parent was posted before the window is included, the parent isn't, as long as the parent is at
        most `slack.thread_lookback_days` older than the window.

        Raises:
            HeinzelError: If the channel or window is malformed, no token is stored, Slack fails, or the window holds
                more than the adapter reads in one call.
        """
        since, until = _bounds(window)
        _channel(channel)
        if since == until:
            return []  # the half-open window is empty: nothing to ask
        client = connect(self._settings, self._credentials, self._transport)
        channel_id = _resolve(client, channel)
        workspace = _workspace(client)
        oldest = since - timedelta(days=self._settings.thread_lookback_days)
        found, threads = _history(client, channel_id, oldest, since, until)
        for parent in threads:
            for ts, item in _replies(client, channel_id, parent, since, until):
                _keep(found, ts, item)
        names = _names(client, found.values())
        kept = [_message(channel_id, workspace, item, names) for item in found.values()]
        return sorted(kept, key=lambda message: (message.posted_at, message.id))

    def post(self, channel: str, text: str, thread: str | None = None) -> PostedMessage:
        """Post `text` (plain text or Markdown) to `channel`, as a reply in `thread` when given.

        Broadcasts and mentions are neutralised (`to_markup`); link previews are off.

        Raises:
            HeinzelError: If the channel, thread or text is malformed or too long, no token is stored, or Slack
                refuses the post.
        """
        _channel(channel)
        if thread is not None and not _TS.fullmatch(thread):
            msg = f"{thread!r} is not a Slack thread id; give the ts of its first message, e.g. 1729000000.000100."
            raise HeinzelError(msg)
        markup = to_markup(text)
        if not markup.strip():
            msg = "There is no text to post."
            raise HeinzelError(msg)
        if len(markup) > MAX_POST_CHARS:
            msg = f"The text is {len(markup)} characters as Slack markup, more than {MAX_POST_CHARS}; post it in parts."
            raise HeinzelError(msg)
        client = connect(self._settings, self._credentials, self._transport)
        channel_id = _resolve(client, channel)
        workspace = _workspace(client)  # before posting: a failure after it would invite a duplicate post
        payload: dict[str, object] = {
            "channel": channel_id,
            "text": markup,
            "parse": "none",
            "link_names": False,
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if thread is not None:
            payload["thread_ts"] = thread
        answer = client.post("chat.postMessage", payload, f"channel {channel_id}")
        ts = str(answer.get("ts") or "")
        if not _TS.fullmatch(ts):
            msg = "Slack's answer to chat.postMessage lacks the new message's ts; is slack.api_url Slack's Web API?"
            raise HeinzelError(msg)
        return PostedMessage(
            channel=channel_id, id=ts, thread=thread, url=_permalink(workspace, channel_id, ts, thread)
        )


TOO_MANY = "ask for a shorter window, or lower slack.thread_lookback_days"


def _history(
    client: SlackClient, channel: str, oldest: datetime, since: datetime, until: datetime
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The messages of `[since, until)` by ts, and the threads with a reply in it among those since `oldest`.

    Raises:
        HeinzelError: If Slack fails, or the window holds more messages or threads than one call reads.
    """
    subject = f"channel {channel}"
    found: dict[str, dict[str, Any]] = {}
    threads: list[str] = []
    params = {"channel": channel, "oldest": _ts(oldest), "latest": _ts(until), "inclusive": "true", "limit": PAGE}
    for page in client.pages("conversations.history", params, "messages", subject, TOO_MANY):
        for item in _said(page):
            ts = _item_ts(item)
            if since <= _time(ts) < until:
                _keep(found, ts, item)
            if _replies_since(item, ts, since):
                threads.append(ts)
    if len(threads) > MAX_THREADS:
        msg = f"{subject} has more than {MAX_THREADS} threads with replies in the window; {TOO_MANY}."
        raise HeinzelError(msg)
    return found, threads


def _replies(
    client: SlackClient, channel: str, parent: str, since: datetime, until: datetime
) -> Iterator[tuple[str, dict[str, Any]]]:
    """The replies to `parent` posted in `[since, until)`, each with its ts.

    Yields:
        A reply's ts and the reply.

    Raises:
        HeinzelError: If Slack fails.
    """
    params = {"channel": channel, "ts": parent, "oldest": _ts(since), "latest": _ts(until)}
    params |= {"inclusive": "true", "limit": PAGE}
    for page in client.pages("conversations.replies", params, "messages", f"channel {channel}", TOO_MANY):
        for item in _said(page):
            ts = _item_ts(item)
            if ts != parent and since <= _time(ts) < until:
                yield ts, item


def _bounds(window: Window) -> tuple[datetime, datetime]:
    if window.since > window.until:
        msg = f"The window starts ({window.since.isoformat()}) after it ends ({window.until.isoformat()})."
        raise HeinzelError(msg)
    return window.since.astimezone(UTC), window.until.astimezone(UTC)


def _channel(text: str) -> None:
    if not (_ID.fullmatch(text) or _NAME.fullmatch(text)):
        msg = (
            f"{text!r} is not a Slack channel; give its id, e.g. C0123456789 (in Slack: channel details, at the "
            "bottom), or #name in lower case, e.g. #general."
        )
        raise HeinzelError(msg)


def _resolve(client: SlackClient, channel: str) -> str:
    """The id of `channel`, looked up among the channels the token sees when given as `#name`.

    Raises:
        HeinzelError: If no channel of that name is visible, or Slack fails.
    """
    if _ID.fullmatch(channel):
        return channel
    name = channel.removeprefix("#")
    params = {"types": "public_channel,private_channel", "exclude_archived": "true", "limit": PAGE}
    too_many = "give the channel's id instead of its name"
    for page in client.pages("conversations.list", params, "channels", channel, too_many):
        for item in page:
            found = str(item.get("id") or "")
            if item.get("name") == name and _ID.fullmatch(found):
                return found
    msg = (
        f"Slack has no channel {channel} the token can see: check the name, join the channel, or give its id "
        "(in Slack: channel details, at the bottom)."
    )
    raise HeinzelError(msg)


def _workspace(client: SlackClient) -> str | None:
    """The workspace's web address from `auth.test`, for permalinks; None unless it is a slack.com address."""
    url = str(client.call("auth.test", {}, "the token").get("url") or "")
    return url if _WORKSPACE.fullmatch(url) else None


def _permalink(workspace: str | None, channel: str, ts: str, thread: str | None) -> str | None:
    if workspace is None:
        return None
    link = f"{workspace}archives/{channel}/p{ts.replace('.', '')}"
    return f"{link}?thread_ts={thread}&cid={channel}" if thread and thread != ts else link


def _ts(moment: datetime) -> str:
    """`moment` as a Slack timestamp, seconds and microseconds since the epoch, exactly."""
    micros = (moment - _EPOCH) // _MICROSECOND
    return f"{micros // 1_000_000}.{micros % 1_000_000:06d}"


def _time(ts: str) -> datetime:
    seconds, _, micros = ts.partition(".")
    return _EPOCH + timedelta(seconds=int(seconds), microseconds=int(micros))


def _said(page: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    """The items of a page that are things people said, not channel events or hidden ones."""
    return (item for item in page if item.get("subtype") in KEPT and not item.get("hidden"))


def _item_ts(item: dict[str, Any]) -> str:
    ts = str(item.get("ts") or "")
    if not _TS.fullmatch(ts):
        msg = "Slack's answer holds a message without a valid ts; is slack.api_url Slack's Web API?"
        raise HeinzelError(msg)
    return ts


def _keep(found: dict[str, dict[str, Any]], ts: str, item: dict[str, Any]) -> None:
    if len(found) >= MAX_MESSAGES and ts not in found:
        msg = f"The window holds more than {MAX_MESSAGES} messages; ask for a shorter window."
        raise HeinzelError(msg)
    found[ts] = item


def _replies_since(item: dict[str, Any], ts: str, since: datetime) -> bool:
    """Whether `item` starts a thread whose latest reply was posted at or after `since`."""
    latest = str(item.get("latest_reply") or "")
    return item.get("thread_ts") == ts and _count(item) > 0 and bool(_TS.fullmatch(latest)) and _time(latest) >= since


def _count(item: dict[str, Any]) -> int:
    count = item.get("reply_count")
    return count if isinstance(count, int) and count > 0 else 0


def _names(client: SlackClient, items: Iterable[dict[str, Any]]) -> dict[str, Participant]:
    """Every author and mentioned user, by user id, as Slack shows them; unknown users are left out."""
    ids: set[str] = set()
    for item in items:
        if (user := _user(item)) is not None:
            ids.add(user)
        ids.update(MENTIONS.findall(str(item.get("text") or "")))
    people: dict[str, Participant] = {}
    for user in sorted(ids):
        answer = _user_info(client, user)
        if answer is not None:
            people[user] = answer
    return people


def _user(item: dict[str, Any]) -> str | None:
    user = str(item.get("user") or "")
    return user if MENTIONS.fullmatch(f"<@{user}>") else None


def _user_info(client: SlackClient, user: str) -> Participant | None:
    try:
        answer = client.call("users.info", {"user": user}, f"user {user}")
    except SlackError as exc:
        if exc.code in {"user_not_found", "users_not_found"}:
            return None  # e.g. a user of another workspace in a shared channel: shown without a name
        raise
    person = answer.get("user")
    if not isinstance(person, dict):
        return None
    record = cast("dict[str, Any]", person)
    profile = record.get("profile")
    details = cast("dict[str, Any]", profile) if isinstance(profile, dict) else {}
    name = _text(details.get("display_name")) or _text(details.get("real_name")) or _text(record.get("real_name"))
    email = _text(details.get("email"))
    return Participant(name=name or _text(record.get("name")), email=email if email and "@" in email else None)


def _text(value: object) -> str | None:
    text = clean_text(value).strip()[:MAX_NAME_CHARS] if isinstance(value, str) else ""
    return text or None


def _author(item: dict[str, Any], people: dict[str, Participant]) -> Participant:
    user = _user(item)
    if user is not None and user in people:
        return people[user]
    bot = item.get("bot_profile")
    bot_name = cast("dict[str, Any]", bot).get("name") if isinstance(bot, dict) else None
    return Participant(name=_text(bot_name) or _text(item.get("username")))


def _message(channel: str, workspace: str | None, item: dict[str, Any], people: dict[str, Participant]) -> Message:
    ts = _item_ts(item)
    parent = str(item.get("thread_ts") or "")
    thread = parent if _TS.fullmatch(parent) and parent != ts else None
    names = {user: person.name for user, person in people.items() if person.name}
    return Message(
        channel=channel,
        id=ts,
        author=_author(item, people),
        posted_at=_time(ts),
        text=to_text(str(item.get("text") or ""), names),
        thread=thread,
        reply_count=0 if thread else _count(item),
        url=_permalink(workspace, channel, ts, thread),
    )
