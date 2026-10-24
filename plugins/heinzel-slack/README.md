# heinzel-slack

Read and post Slack channel messages, the `chat` capability ([ADR 0028](../../docs/adr/0028-integrations-are-capability-adapters.md)):
a first-party [Heinzelmännchen](../../README.md) plugin. It is released with the core, at the same version, and not
installed by default:

```bash
hz system plugin add slack              # the release matching your hz; run it again to upgrade
hz system plugin remove heinzel-slack
```

## Set up

Bind the adapter in your user configuration, never in a project `hz.toml`, which Heinzelmännchen ignores for bindings:

```bash
hz system config set capabilities.chat slack
```

which writes

```toml
[capabilities]
chat = "slack"
```

Or per shell: `HZ_CAPABILITIES__CHAT=slack`.

The adapter acts as you, with a **user token** of a Slack app you create for yourself:

1. At [api.slack.com/apps](https://api.slack.com/apps), create an app from scratch in your workspace.
2. Under **OAuth & Permissions**, add these **User Token Scopes**, all read-only:
   `channels:history`, `groups:history` (messages of public and private channels), `channels:read`, `groups:read`
   (to resolve `#name`), `users:read` (authors' names) and `users:read.email` (their email; optional, without it
   `email` is empty). Add `chat:write` only if a publish operation should post for you.
3. Install the app to the workspace and copy the **User OAuth Token** (`xoxp-...`).
4. Store it in the OS keychain (the value is read from a hidden prompt, never the command line):

```bash
hz system credential set slack-token
```

On machines without a keychain, export `HZ_CREDENTIAL_SLACK_SLACK_TOKEN` instead. A token reads what you can read:
the adapter only reads and, with `chat:write`, only posts through publish operations. A bot token (`xoxb-...`) works
too, seeing only the channels the bot was added to; tell the adapter with `slack.token_kind`:

```toml
[slack]
token_kind = "user"          # or "bot"
thread_lookback_days = 30    # how far before a window to look for threads with replies in it
timeout = 30                 # seconds per request
api_url = "https://slack.com/api"
```

`api_url` and `token_kind` are ignored in a project `hz.toml`: they decide where the token goes and as whom, and
`api_url` only accepts https on slack.com, the hosts `hz system capability list` shows.

## Use

Workflows ask for `chat`, never for Slack:

```python
from datetime import datetime

from heinzel import Chat, Message, Module, PostedMessage, Window

module = Module("digest", "Weekly digest.", requires=["chat:read"])


@module.operation
def week(chat: Chat, since: datetime, until: datetime) -> list[Message]:
    """What the team said this week."""
    return chat.messages("#widgets", Window(since=since, until=until))


@module.operation(read_only=False, requires=["chat:write"])
def announce(chat: Chat, text: str) -> PostedMessage:
    """Post the digest."""
    return chat.post("#widgets", text)
```

| Method | Capability | Slack Web API |
| --- | --- | --- |
| `messages(channel, window)` | `chat:read` | `conversations.history` over the window and the lookback, `conversations.replies` of every thread with a reply in the window, `users.info` per author and mention, `auth.test` for links |
| `post(channel, text, thread=None)` | `chat:write` | `chat.postMessage` with `parse=none`, no link names and no previews |

A channel is its id (`C0123456789`, in Slack's channel details) or `#name`, resolved with `conversations.list`;
results carry the id. Windows are half-open `[since, until)`; messages come oldest first, top level and replies, a
reply to a parent posted before the window without that parent, as long as the parent is at most
`thread_lookback_days` older than the window. Join and topic events are left out. Text comes back as plain text with
Markdown links, mentions as `@name`, broadcasts as `@channel`, and without control, invisible or bidirectional
characters. Why these endpoints: [ADR 0034](../../docs/adr/0034-slack-web-api-over-urllib.md).

`post` escapes `&`, `<` and `>`, so no `<!channel>`, `<!here>`, `<@U…>` or `<#C…>` in a draft becomes a mention or a
broadcast, and puts an invisible word joiner into a written `@channel`, `@here` or `@everyone`. Markdown links,
`**bold**`, `~~strike~~` and headings become Slack's forms. Texts over 40,000 characters are refused, not cut.

A call reads at most 100 pages per list, 300 threads, 20,000 messages, 600 requests and 50 MB, then fails and says to
ask for a shorter window. Slack's rate limits are honoured: a `429` is retried up to three times after the
`Retry-After` it names, if that is at most a minute; otherwise the call fails with the time to retry. Slack limits
`conversations.history` and `conversations.replies` far more for apps distributed to other workspaces outside the
Marketplace; an app you create for your own workspace isn't.

```bash
hz slack info                   # this plugin, its version, the Slack API and the scopes it needs
hz system capability list       # who reads and writes what, through which adapter and hosts
hz system doctor                # the binding and the token's presence; never contacts Slack
```

Errors say what to do: a rejected token names `hz system credential set slack-token`; a missing scope names the
scope to add; an unknown or invisible channel says to check it or join it. Tokens never appear in output, errors or
logs: a stored token is stripped of surrounding whitespace and refused, without being shown, unless it is one line of
printable ASCII of the configured kind. Requests never follow redirects to another host.
