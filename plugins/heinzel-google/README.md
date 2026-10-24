# heinzel-google

Read Google Docs, Sheets, Drive, Calendar and Gmail, the `documents`, `calendar` and `mail` capabilities
([ADR 0028](../../docs/adr/0028-integrations-are-capability-adapters.md)): a first-party
[Heinzelmännchen](../../README.md) plugin. It is released with the core, at the same version, and not installed by
default:

```bash
hz system plugin add google             # the release matching your hz; run it again to upgrade
hz system plugin remove heinzel-google
```

It is the **default provider** for Google Workspace, not the only possible one: workflows ask for `documents`,
`calendar` and `mail`, and any other plugin that provides those capabilities can be bound instead, in configuration.

## How it reaches Google

Through the open-source [Google Workspace CLI](https://github.com/googleworkspace/cli), `gws` (0.22 or newer): it
signs in with Google, keeps its own credentials (encrypted, with the key in the OS keychain, by default) and calls
Google's APIs. This plugin never sees a token. It runs `gws <service> <resource> <method> --params <JSON> --format
json` as an argument list (never through a shell), with the executable resolved to an absolute path, from the root
directory (so no project's `.env` reaches `gws`), with an allowlisted environment (never Heinzelmännchen's own
credentials or other secrets), a
timeout that stops `gws` and anything it started, and at most 10 MB of output read. Why `gws`:
[ADR 0038](../../docs/adr/0038-google-workspace-through-the-gws-cli.md).

What leaves this machine, and where to: file ids, the Drive query (the modified-time window and the `--name` text)
the calendar window and the mail search, to Google's API hosts (`www.googleapis.com` for Drive and Calendar,
`docs.googleapis.com`, `sheets.googleapis.com`, `gmail.googleapis.com`); `gws` also fetches API descriptions from Google and refreshes its sign-in at
`oauth2.googleapis.com`. Nothing goes anywhere else: gws 0.22 documents no telemetry or update check, and none was
found in it.

## Set up

Install `gws` (it needs Node.js), give it an OAuth client once (`gws auth setup`, which needs `gcloud`, or a Desktop
OAuth client as its README describes), then let setup do the rest:

```bash
npm install -g @googleworkspace/cli
hz google setup         # finds gws, signs it in read-only, binds documents, calendar and (if you say so) mail
hz system doctor        # finds gws, checks its version and local sign-in state; never contacts Google
```

By hand, setup is:

```bash
gws auth login --scopes https://www.googleapis.com/auth/documents.readonly,https://www.googleapis.com/auth/spreadsheets.readonly,https://www.googleapis.com/auth/drive.metadata.readonly,https://www.googleapis.com/auth/calendar.readonly
hz system config set capabilities.documents google
hz system config set capabilities.calendar google
```

Mail is opt-in: setup asks, defaulting to no, and then signs in again with Gmail's read-only scope added
(`,https://www.googleapis.com/auth/gmail.readonly`), which lets the plugin read all of your mail; then
`hz system config set capabilities.mail google`.

These are the narrowest read-only scopes for what the adapters do; `drive.metadata.readonly` lists and identifies files
and never reads their contents (Google classes it as restricted, which matters only for published OAuth clients).
Bindings go in your user configuration, never in a project `hz.toml`, which Heinzelmännchen ignores for them.

Optional settings, in `[google]` or `HZ_GOOGLE__<FIELD>`: `gws_path`, the absolute path of `gws` when it isn't on
`PATH` (only absolute `PATH` entries are searched; both are ignored in a project `hz.toml`: a repository someone else
controls must not choose the program that reads your data), and `timeout`, the seconds each `gws` run may take
(default 60, also ignored in a project `hz.toml`).

## Use

Workflows ask for `documents` and `calendar`, never for Google:

```python
from pydantic import AwareDatetime

from heinzel import Calendar, Document, Documents, Event, Module, Window

module = Module("prep", "One-on-one preparation.", requires=["documents:read", "calendar:read"])


@module.operation
def week(calendar: Calendar, since: AwareDatetime, until: AwareDatetime) -> list[Event]:
    """This week's events."""
    return calendar.events(Window(since=since, until=until))


@module.operation
def notes(documents: Documents) -> Document:
    """The running notes, as Markdown."""
    return documents.read("https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit")
```

| Method | Capability | `gws` command |
| --- | --- | --- |
| `read(ref)` | `documents:read` | `drive files get` (metadata), then `docs documents get` with `includeTabsContent` or `sheets spreadsheets get` with `includeGridData` |
| `events(window)` | `calendar:read` | `calendar events list` on `primary`, `singleEvents`, `showDeleted`, paged by `pageToken` |
| `messages(query, window)` | `mail:read` | `gmail users messages list` with Gmail's search, then `messages get` (metadata) for each |
| `read(message)` | `mail:read` | `gmail users messages get` (full) |

- **Documents** are a Docs, Sheets or Drive link (`https://docs.google.com/document/d/<id>/...`,
  `.../spreadsheets/d/<id>/...`, `https://drive.google.com/file/d/<id>/...`, `.../open?id=<id>`) or a bare file id;
  links to any other host are refused before `gws` runs, and a Drive shortcut is followed once. A Doc comes back as
  Markdown-flavoured text, every tab under its title: headings, lists, bold and italic, links, tables; person chips by
  name, never their email, and mail links by their text; titles and link targets can't start a line of their own;
  images and drawings are left out. A Sheet comes back as one `## <sheet>` table per sheet
  with the values as people see them. `revision` is the Doc's revision id or the Sheet's Drive version. Uploaded Word
  or Excel files are refused: open them as a Google Doc or Sheet first.
- **Events** are your primary calendar's, every occurrence of a recurring event, cancelled ones included, by start.
  Attendees are display names and emails, without meeting rooms; the workflow packet builder drops the emails before
  anything reaches an agent's prompt. `conference_id` is the Zoom meeting number when the event has a Zoom link (in
  its conferencing data, location or description), else the Meet code, so the `meetings` capability can find the
  event's meeting; conference links are kept only on the conferencing services' own hosts (Meet, Zoom, Teams, Webex),
  without their query, where passcodes live. A display name that is an address is dropped.
- **Mail** is your Gmail: `messages` takes the shared query syntax (`from:<address or domain> subject:<text>
  label:<name>`) and a window. The window and the sender go into Gmail's search and the label as its id; the subject
  is matched by the plugin, because Gmail's `subject:` matches whole words only. Results come oldest first, at most
  200 messages a window (one `gws` run each). `read` gives the text part, or the HTML part as text, in the part's own
  charset, without attachments, and only the headers that carry no addresses. Senders and recipients are names and
  emails, parsed before any encoded name is decoded, so a display name can't pose as an address; the workflow packet
  builder drops the addresses before anything reaches an agent's prompt. A message longer than 2 million characters,
  or with more than 500 MIME parts, is refused, not cut.
- **Text is data.** Every string returned passes `heinzel.clean_text` (no control, invisible or bidirectional
  characters).
- **Bounds.** One `gws` run prints at most 10 MB, one call reads at most 100 pages or 25 MB, a Doc's text at most four
  times its JSON, tables and tabs nest at most 8 deep, a Sheet at most a million cells; past that, an error says to
  narrow the window or that the file is too large. Rate limits are honoured: a rate-limited call is retried after 1, 2
  and 4 seconds, then fails saying when to retry.

```bash
hz google info                  # this plugin, its version, the gws it uses, the scopes and APIs
hz google files --days 3        # Docs and Sheets you can open that changed in the last three days
hz system capability list       # who reads what, through which adapter and hosts
```

Errors say what to do: no `gws` names the install; not signed in or an expired sign-in names the exact
`gws auth login` command above (doctor names `hz google setup`); a missing scope or a disabled API says which; a file the account can't see says so.
Token-shaped strings in anything `gws` says are redacted before it is shortened.
