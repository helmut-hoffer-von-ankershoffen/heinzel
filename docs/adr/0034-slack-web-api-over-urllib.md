---
status: proposed
date: 2026-10-24
decision-makers: director (delegated by the maintainer)
related: 0017, 0024, 0028, 0030
---

# 0034: Read and post Slack through its Web API with urllib and a user token; threads found through a lookback

## Context and Problem Statement

The adapter serves `chat` of [ADR 0028](0028-integrations-are-capability-adapters.md): `messages(channel, window)`
(read) and `post(channel, text, thread=None)` (write). It needs a transport, a kind of token, and a way to find the
replies posted in a window, which Slack's Web API doesn't list by time: `conversations.history` lists top-level
messages, `conversations.replies` one thread. Which transport, which token, and which endpoints?

## Decision Drivers

- No new dependency: Heinzelmännchen's runtime doesn't depend on an HTTP client library or a vendor SDK.
- Least privilege: a read-only setup never asks for write access (ADR 0028), and the token stays in the keychain
  ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)).
- Per person: the first workflows (a weekly digest, 1:1 preparation) read the channels the person reads.
- Exact half-open windows, bounded work, and Slack's rate limits honoured.
- Text from Slack is data: no control or bidirectional characters, no raw ids for people, and no draft can page a
  channel.

## Considered Options

- **Web API over urllib; a user token; history over the window plus a lookback, then the threads with replies in the
  window** (chosen)
- **`search.messages`** (`in:#channel after:...`): one query returns replies too, but needs `search:read`, which reads
  every conversation the user can see, has day-granular dates, an index that lags, and caps results.
- **A bot token by default**: posts as an app, but sees only channels it was added to, so every reader would have to
  invite a bot first; kept as `token_kind = "bot"`.
- **The Events API or Socket Mode**: real-time, but needs a running app with a public endpoint or a socket, which a
  per-person CLI doesn't have.
- **`slack_sdk` or `httpx`**: a new dependency for a handful of GET and one POST.

## Decision Outcome

Chosen option: **Web API over urllib with a user token**, because it needs nothing beyond the standard library, reads
what the person reads with read-only scopes, and keeps windows exact. Defaults pending the maintainer's final answer
are settings: `slack.token_kind` (`user`) and `slack.api_url` (`https://slack.com/api`), both ignored in a project
`hz.toml`, `api_url` accepted only as https on slack.com.

- **Token.** One credential, `slack-token`, a user token (`xoxp-`, or a rotating `xoxe.xoxp-`) of an app the person
  creates for their own workspace, with the user token scopes `channels:history`, `groups:history`, `channels:read`,
  `groups:read`, `users:read` and `users:read.email`; `chat:write` only for `post`. Stripped, refused unless it is one
  line of printable ASCII with the configured kind's prefix, never shown. Doctor never contacts Slack, so it can't
  check scopes; a missing scope fails with the scope Slack names.
- **`messages`.** `conversations.history` from `since` minus `slack.thread_lookback_days` (30) to `until`, keeping
  what was posted in the window and noting every thread whose `latest_reply` is in it; `conversations.replies` for each
  of those, keeping the replies in the window. Joins, topic changes and hidden messages are left out; a broadcast reply
  counts once. `users.info` names authors and mentions (`@name`, `@someone` for a user Slack doesn't know); text keeps
  Markdown links and passes `clean_text`. A `#name` is resolved with `conversations.list`. Permalinks are built from
  `auth.test`'s workspace address, only if it is on slack.com.
- **`post`.** `chat.postMessage` with `parse=none`, no link names and no previews. `&`, `<` and `>` are escaped, so
  `<!channel>`, `<!here>`, `<@U…>` and `<#C…>` stay text; a written `@channel`, `@here` or `@everyone` gets a word
  joiner; Markdown links, bold, strike and headings become Slack's forms. Over 40,000 characters is refused, not cut.
  The workspace address is asked before posting, so no failure after a post invites a duplicate.
- **Bounds.** Per call: 100 pages per list, 300 threads, 20,000 messages, 600 requests, 50 MB, 5 MB per answer; past
  any, a `HeinzelError` says to narrow the window or the lookback.
- **Rate limits.** A `429` is retried up to three times after its `Retry-After` (5 seconds without one) when that is at
  most a minute, through `Transport.sleep`, which tests replace; otherwise the call fails with the time to retry.
- **Transport.** `heinzel_slack._transport.Transport` is the only code that sends requests or waits; redirects outside
  the API's origin are refused. Error messages carry Slack's error code only if it looks like one, never free text
  from an answer.

### Consequences

- Good: no dependency, read-only by default, exact windows, tests replace one class.
- Bad: a reply to a parent older than the lookback is missed; raising `thread_lookback_days` reads more history.
- Bad: a busy channel costs one request per 200 messages over the window and the lookback, one per thread with
  replies in the window, and one per person named.
- Bad: each person creates a Slack app; workspaces that forbid that need a shared app and, later, OAuth.
- Bad: Slack limits `conversations.history` and `conversations.replies` far more for apps distributed outside the
  Marketplace; an app made for one's own workspace isn't affected, a shared one would be.

### Confirmation

`plugins/heinzel-slack/tests/unit/adapter_test.py` (window, lookback, threads, broadcasts, names, bounds, post),
`client_test.py` (rate limits, budgets, cursors, errors, redaction, token checks), `text_test.py` (markup both ways,
neutralised broadcasts), `transport_test.py` and `tests/integration/transport_test.py` (same-origin redirects), and
`tests/integration/capability_test.py` (bindings, read-only handles can't post, no token in output, a project
`hz.toml` can neither bind nor redirect).

## More Information

- Open for the maintainer: user token versus a shared bot or OAuth app, and whether `search:read` is acceptable for
  replies to old threads. Both are settings or additions, not rewrites.
- Teams (`chat` later) follows ADR 0028's channel rule the same way.
