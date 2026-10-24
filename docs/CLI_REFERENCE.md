# CLI reference

🧝 Heinzelmännchen: open-source helpers that keep product teams running. Every operation accepts --json. Commands marked (expert) run like any other but are listed in help and completion only in expert mode: hz system expert on.

**Usage**:

```console
$ hz [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `-V, --version`: Show the version and repository, Python, platform, installation and plugins, then exit.
* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `ai`: (expert) How hz reaches AI: the AI access...
* `atlassian`: (expert) Read Jira issues and Confluence...
* `calendar`: Your calendar: today, the week, free time,...
* `chat`: Chat with the agent runner in this...
* `clock`: Show the time in each of the team&#x27;s time...
* `github`: (expert) Read pull requests and issues...
* `google`: (expert) Google Docs, Sheets, Drive,...
* `graph`: Show your team&#x27;s promises: who keeps which...
* `join`: Join a team from its invite line: finds...
* `mail`: Your mail: unread and important first,...
* `org`: Your profile, and the team, org and...
* `report`: Telemetry: log what a Heinzelmännchen did,...
* `setup`: Set up what is still missing, wizard...
* `system`: Inspect, configure, diagnose and update...
* `team`: Your team&#x27;s repository: create one (`hz...
* `ui`: Open the web app in your browser, served...
* `workflow`: Workflow contracts: Markdown files an...
* `zoom`: (expert) Read meetings from Zoom&#x27;s...

## `hz ai`

(expert) How hz reaches AI: the AI access in use, its status and a live test.

**Usage**:

```console
$ hz ai [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `access`: (expert) Pick how hz reaches AI: Claude...

### `hz ai access`

(expert) Pick how hz reaches AI: Claude Code, your company&#x27;s gateway, or an option a plugin adds.

**Usage**:

```console
$ hz ai access [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: (expert) List the AI access options, the...
* `test`: (expert) Run one cheap live check of an AI...
* `use`: (expert) Use an AI access option: sets...

#### `hz ai access list`

(expert) List the AI access options, the one in use, whether each is signed in, and what runs each use.

**Usage**:

```console
$ hz ai access list [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz ai access test`

(expert) Run one cheap live check of an AI access option and keep the result; this may cost a little.

**Usage**:

```console
$ hz ai access test [OPTIONS]
```

**Options**:

* `--name <str>`: The option to test, e.g. `gateway`; the one in use when not given.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz ai access use`

(expert) Use an AI access option: sets capabilities.ai_access, and agent_runner if the option names one.

**Usage**:

```console
$ hz ai access use [OPTIONS] {name}
```

**Arguments**:

* `name`: The AI access to use, one `hz ai access list` shows, e.g. `claude-code`.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz atlassian`

(expert) Read Jira issues and Confluence pages through acli: the tracker and documents capabilities.

**Usage**:

```console
$ hz atlassian [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `info`: (expert) Show this plugin&#x27;s version, the...

### `hz atlassian info`

(expert) Show this plugin&#x27;s version, the Atlassian site and the acli commands it runs.

**Usage**:

```console
$ hz atlassian info [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz calendar`

Your calendar: today, the week, free time, and events created or answered once you confirm.

**Usage**:

```console
$ hz calendar [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--on <str>`: The day: `tomorrow` or a date such as 2026-10-26; today when not given.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

**Commands**:

* `cancel`: (expert) Cancel an event you organize, for...
* `free`: Show when you (and the people you name)...
* `move`: (expert) Move an event you organize to...
* `new`: Create an event in your calendar and...
* `now`: Show the meeting you are in and your next...
* `remind`: (expert) Record a notice with the join...
* `respond`: Answer an invitation: accepted, declined...
* `search`: (expert) Find events around today whose...
* `show`: (expert) Show one event in full: when,...
* `week`: Show your events of the next seven days,...

### `hz calendar cancel`

(expert) Cancel an event you organize, for everyone; shown first, cancelled with `--confirm`.

**Usage**:

```console
$ hz calendar cancel [OPTIONS] {event}
```

**Arguments**:

* `event`: The event&#x27;s id, as `hz calendar` and `hz calendar week` list it.  [required]

**Options**:

* `--notify / --no-notify`: Tell the guests by mail.  [default: notify]
* `--confirm / --no-confirm`: Carry it out. Without it, nothing changes: the answer says what would happen. An agent passes it only after the person approved that summary.  [default: no-confirm]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz calendar free`

Show when you (and the people you name) are free within your working hours on a day.

**Usage**:

```console
$ hz calendar free [OPTIONS]
```

**Options**:

* `--on <str>`: The day: `tomorrow` or a date such as 2026-10-26; today when not given.
* `--people <str>`: Others to find time with, by address, comma-separated: ada@example.com,bo@example.com.
* `--minutes <int>`: The shortest free time worth listing.  [default: 30]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz calendar move`

(expert) Move an event you organize to another time; shown first, moved with `--confirm`.

**Usage**:

```console
$ hz calendar move [OPTIONS] {event} {start}
```

**Arguments**:

* `event`: The event&#x27;s id, as `hz calendar` and `hz calendar week` list it.  [required]
* `start`: The new start: `10:30` (today), `tomorrow 9:00`, `2026-10-26 14:00`.  [required]

**Options**:

* `--minutes <int>`: A new length; it keeps its own.
* `--notify / --no-notify`: Tell the guests by mail.  [default: notify]
* `--confirm / --no-confirm`: Carry it out. Without it, nothing changes: the answer says what would happen. An agent passes it only after the person approved that summary.  [default: no-confirm]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz calendar new`

Create an event in your calendar and invite its guests; shown first, created with `--confirm`.

**Usage**:

```console
$ hz calendar new [OPTIONS] {title} {start}
```

**Arguments**:

* `title`: What it is called, one line.  [required]
* `start`: When it starts: `10:30` (today), `tomorrow 9:00` or `2026-10-26 14:00`.  [required]

**Options**:

* `--minutes <int>`: How long it lasts.  [default: 30]
* `--people <str>`: Whom to invite, by address, comma-separated: ada@example.com,bo@example.com.
* `--location <str>`: Where it takes place.
* `--description <str>`: Notes for the guests.
* `--video / --no-video`: Add a video call from your calendar&#x27;s conferencing.  [default: no-video]
* `--notify / --no-notify`: Tell the guests by mail.  [default: notify]
* `--confirm / --no-confirm`: Carry it out. Without it, nothing changes: the answer says what would happen. An agent passes it only after the person approved that summary.  [default: no-confirm]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz calendar now`

Show the meeting you are in and your next one, counting down, in one line for a status bar.

**Usage**:

```console
$ hz calendar now [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz calendar remind`

(expert) Record a notice with the join link for each meeting starting within ` remind_minutes`.

**Usage**:

```console
$ hz calendar remind [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz calendar respond`

Answer an invitation: accepted, declined or tentative; shown first, answered with `--confirm`.

**Usage**:

```console
$ hz calendar respond [OPTIONS] {event} {answer}:<accepted|declined|tentative>
```

**Arguments**:

* `event`: The event&#x27;s id, as `hz calendar` and `hz calendar week` list it.  [required]
* `answer:<accepted|declined|tentative>`: Your answer: accepted, declined or tentative.  [required]

**Options**:

* `--confirm / --no-confirm`: Carry it out. Without it, nothing changes: the answer says what would happen. An agent passes it only after the person approved that summary.  [default: no-confirm]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz calendar search`

(expert) Find events around today whose title, location or people hold every word of `query`.

**Usage**:

```console
$ hz calendar search [OPTIONS] {query}
```

**Arguments**:

* `query`: Words the title, location or a person&#x27;s name holds, e.g. `planning`.  [required]

**Options**:

* `--days <int>`: Days before and after today to look through.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz calendar show`

(expert) Show one event in full: when, where, who, your answer and its links.

**Usage**:

```console
$ hz calendar show [OPTIONS] {event}
```

**Arguments**:

* `event`: The event&#x27;s id, as `hz calendar` and `hz calendar week` list it.  [required]

**Options**:

* `--days <int>`: Days before and after today to look through.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz calendar week`

Show your events of the next seven days, from today.

**Usage**:

```console
$ hz calendar week [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz chat`

Chat with the agent runner in this terminal: it reads with hz&#x27;s operations and asks you before it changes anything. /exit or Ctrl-D leaves; `hz chat --continue` comes back.

**Usage**:

```console
$ hz chat [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `-c, --continue`: Continue the most recent chat instead of opening one.
* `--session <str>`: Continue this chat session (`hz chat list`).
* `--context <str>`: What a new chat is about: `route:/contracts`, `contract:weekly`, `draft:&lt;id&gt;`, `event:&lt;id&gt;`.
* `--detail <str>`: What you know about it, in words.
* `--help`: Show this message and exit.

**Commands**:

* `confirm`: Run an action the agent proposed, with...
* `decline`: Decline an action the agent proposed; it...
* `list`: List chat sessions, most recent first;...
* `open`: Open a chat session, optionally about a...
* `remove`: (expert) Delete a chat session and...
* `send`: Send a message in a chat session and wait...
* `show`: (expert) Show a chat session: its context,...

### `hz chat confirm`

Run an action the agent proposed, with exactly the arguments shown; only you can confirm.

**Usage**:

```console
$ hz chat confirm [OPTIONS] {session} {action}
```

**Arguments**:

* `session`: The chat session&#x27;s id, from `hz chat list`.  [required]
* `action`: The proposed action&#x27;s id, e.g. `a1`.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz chat decline`

Decline an action the agent proposed; it never runs.

**Usage**:

```console
$ hz chat decline [OPTIONS] {session} {action}
```

**Arguments**:

* `session`: The chat session&#x27;s id, from `hz chat list`.  [required]
* `action`: The proposed action&#x27;s id, e.g. `a1`.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz chat list`

List chat sessions, most recent first; they are kept for system.runner.chat.keep_days.

**Usage**:

```console
$ hz chat list [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz chat open`

Open a chat session, optionally about a screen, a contract, a draft or a notification event.

**Usage**:

```console
$ hz chat open [OPTIONS]
```

**Options**:

* `--context <str>`: What the chat is about, as `kind:ref`: `route:/contracts`, `contract:weekly`, `draft:&lt;id&gt;`, `event:&lt;id&gt;`; a bare id is a notification event.
* `--detail <str>`: What you know about it, in words, e.g. a notification&#x27;s text.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz chat remove`

(expert) Delete a chat session and everything it holds; returns the sessions left.

**Usage**:

```console
$ hz chat remove [OPTIONS] {session}
```

**Arguments**:

* `session`: The chat session&#x27;s id, from `hz chat list`.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz chat send`

Send a message in a chat session and wait for the answer; this may cost money.

**Usage**:

```console
$ hz chat send [OPTIONS] {session} {message}
```

**Arguments**:

* `session`: The chat session&#x27;s id, from `hz chat list`.  [required]
* `message`: What to say.  [required]

**Options**:

* `--budget-usd <float>`: Most this answer may cost in US dollars, within the per-message and session caps.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz chat show`

(expert) Show a chat session: its context, messages, actions and costs.

**Usage**:

```console
$ hz chat show [OPTIONS] {session}
```

**Arguments**:

* `session`: The chat session&#x27;s id, from `hz chat list`.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz clock`

Show the time in each of the team&#x27;s time zones.

**Usage**:

```console
$ hz clock [OPTIONS]
```

**Options**:

* `--at ISO-8601`: Moment to show instead of now (ISO 8601).
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz github`

(expert) Read pull requests and issues from GitHub: the code-host and tracker capabilities.

**Usage**:

```console
$ hz github [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `info`: (expert) Show this plugin&#x27;s version, the...

### `hz github info`

(expert) Show this plugin&#x27;s version, the Heinzelmännchen it runs on and the GitHub host it reads.

**Usage**:

```console
$ hz github info [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz google`

(expert) Google Docs, Sheets, Drive, Calendar and Gmail: the documents, calendar and mail capabilities.

**Usage**:

```console
$ hz google [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `files`: (expert) List the Google Docs and Sheets...
* `info`: (expert) Show this plugin&#x27;s version, the...

### `hz google files`

(expert) List the Google Docs and Sheets you can open that changed recently, newest first.

**Usage**:

```console
$ hz google files [OPTIONS]
```

**Options**:

* `--days <int>`: How many days back to look for changes, 1 to 366.  [default: 7]
* `--name <str>`: Only files whose name contains this text; all Docs and Sheets when not given.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz google info`

(expert) Show this plugin&#x27;s version, the Heinzelmännchen it runs on and the gws, scopes and APIs it uses.

**Usage**:

```console
$ hz google info [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz graph`

Show your team&#x27;s promises: who keeps which artifact, how each promise stands, where the flow waits.

**Usage**:

```console
$ hz graph [OPTIONS]
```

**Options**:

* `--artifact <str>`: Only this artifact, its parts and what reads or writes them, by node id.
* `--heinzel <str>`: Only this Heinzelmännchen and what it reads and writes, by its id; with --artifact, both.
* `--state <on_time|waiting_for_approval|late|fell_back|offline>`: Only nodes in this state: on_time, waiting_for_approval, late, fell_back or offline.
* `--at ISO-8601`: The graph as of this time, ISO 8601 with offset; now by default.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz join`

Join a team from its invite line: finds out who you are, joins the team, then sets up what is missing.

**Usage**:

```console
$ hz join [OPTIONS] {URL}
```

**Arguments**:

* `URL`: The team repository&#x27;s URL, the invite: https, ssh or a local path.  [required]

**Options**:

* `--yes`: Make it your team without asking (when its roster lists you).
* `--propose`: When the roster doesn&#x27;t list you, ask its owners to add you without asking: opens a pull request.
* `--role <str>`: Your role, so hz doesn&#x27;t ask it.
* `--handle, --as <str>`: Your handle in the team&#x27;s roster.
* `--answers <file>`: Answer in advance from a TOML file with a table per module, e.g. `role = &quot;engineer&quot;` in the org table; runs without a terminal, for coding agents and teams. Never put secrets in it.
* `--json`: Print what happened as JSON on stdout.
* `--help`: Show this message and exit.

## `hz mail`

Your mail: unread and important first, conversations, drafts and confirmed sends.

**Usage**:

```console
$ hz mail [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--everything / --no-everything`: List read mail in the inbox too, not only unread.  [default: no-everything]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

**Commands**:

* `archive`: (expert) Archive a conversation (out of...
* `draft`: (expert) Keep a new message in your...
* `label`: (expert) File a conversation under labels...
* `notice`: (expert) Record a notice for each...
* `preview`: (expert) Show a draft as it stands,...
* `reply`: (expert) Keep a reply in your drafts, in...
* `search`: (expert) Find mail of the last days by...
* `send`: (expert) Send a draft: shown first with...
* `show`: Show a conversation: every message&#x27;s text,...
* `unread`: Count the unread mail in your inbox, in...

### `hz mail archive`

(expert) Archive a conversation (out of the inbox, nothing deleted); shown first, done with `--confirm`.

**Usage**:

```console
$ hz mail archive [OPTIONS] {thread}
```

**Arguments**:

* `thread`: The conversation&#x27;s id: `thread` of a message `hz mail` lists.  [required]

**Options**:

* `--confirm / --no-confirm`: Carry it out. Without it, nothing changes: the answer says what would happen. An agent passes it only after the person approved that summary.  [default: no-confirm]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz mail draft`

(expert) Keep a new message in your drafts, unsent; shown first, kept with `--confirm`.

**Usage**:

```console
$ hz mail draft [OPTIONS] {to} {subject} {text}
```

**Arguments**:

* `to`: Email addresses, comma-separated: ada@example.com,bo@example.com.  [required]
* `subject`: The subject, one line.  [required]
* `text`: The message, plain text.  [required]

**Options**:

* `--cc <str>`: Email addresses, comma-separated: ada@example.com,bo@example.com.
* `--confirm / --no-confirm`: Carry it out. Without it, nothing changes: the answer says what would happen. An agent passes it only after the person approved that summary.  [default: no-confirm]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz mail label`

(expert) File a conversation under labels (never Spam or Trash); shown first, done with `--confirm`.

**Usage**:

```console
$ hz mail label [OPTIONS] {thread}
```

**Arguments**:

* `thread`: The conversation&#x27;s id: `thread` of a message `hz mail` lists.  [required]

**Options**:

* `--add <str>`: Labels to add, comma-separated, e.g. `Projects`.
* `--remove <str>`: Labels to remove, comma-separated; `unread` marks it read.
* `--confirm / --no-confirm`: Carry it out. Without it, nothing changes: the answer says what would happen. An agent passes it only after the person approved that summary.  [default: no-confirm]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz mail notice`

(expert) Record a notice for each important unread message of the last hour, from whom and about what.

**Usage**:

```console
$ hz mail notice [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz mail preview`

(expert) Show a draft as it stands, possibly edited in your mail app since.

**Usage**:

```console
$ hz mail preview [OPTIONS] {draft}
```

**Arguments**:

* `draft`: The draft&#x27;s id, as `hz mail draft` or `hz mail reply` answered it.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz mail reply`

(expert) Keep a reply in your drafts, in its conversation, unsent; shown first, kept with `--confirm`.

**Usage**:

```console
$ hz mail reply [OPTIONS] {message} {text}
```

**Arguments**:

* `message`: The message to answer: `id` of one `hz mail show` lists.  [required]
* `text`: The message, plain text.  [required]

**Options**:

* `--everyone / --no-everyone`: Answer everyone it went to, not only its sender.  [default: no-everyone]
* `--confirm / --no-confirm`: Carry it out. Without it, nothing changes: the answer says what would happen. An agent passes it only after the person approved that summary.  [default: no-confirm]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz mail search`

(expert) Find mail of the last days by sender, subject and label.

**Usage**:

```console
$ hz mail search [OPTIONS] {query}
```

**Arguments**:

* `query`: Terms like `from:ada@example.com subject:&quot;Plan&quot; label:INBOX`; all must match.  [required]

**Options**:

* `--days <int>`: Days back to look through.  [default: 30]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz mail send`

(expert) Send a draft: shown first with its recipients and text, sent with `--confirm` if unchanged since.

**Usage**:

```console
$ hz mail send [OPTIONS] {draft}
```

**Arguments**:

* `draft`: The draft&#x27;s id, as `hz mail draft` or `hz mail reply` answered it.  [required]

**Options**:

* `--seen <str>`: The draft version to send: run `hz mail send DRAFT` first and copy the command it prints.
* `--confirm / --no-confirm`: Carry it out. Without it, nothing changes: the answer says what would happen. An agent passes it only after the person approved that summary.  [default: no-confirm]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz mail show`

Show a conversation: every message&#x27;s text, oldest first, as plain text without addresses.

**Usage**:

```console
$ hz mail show [OPTIONS] {thread}
```

**Arguments**:

* `thread`: The conversation&#x27;s id: `thread` of a message `hz mail` lists.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz mail unread`

Count the unread mail in your inbox, in one line for a status bar; never fails.

**Usage**:

```console
$ hz mail unread [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz org`

Your profile, and the team, org and company you work with.

**Usage**:

```console
$ hz org [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `agenda`: (expert) Show the meeting you are in now...
* `show`: Show your profile: who you are, your role,...
* `sync`: Show what your team recommends beside what...

### `hz org agenda`

(expert) Show the meeting you are in now and your next one, from your calendar, or how to connect one.

**Usage**:

```console
$ hz org agenda [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz org show`

Show your profile: who you are, your role, time zone and working hours, and what is still missing.

**Usage**:

```console
$ hz org show [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz org sync`

Show what your team recommends beside what you have; apply only what you accept, to your user file.

**Usage**:

```console
$ hz org sync [OPTIONS]
```

**Options**:

* `--accept <str>`: Recommendations to apply, by id and comma-separated (binding:documents), or all.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz report`

Telemetry: log what a Heinzelmännchen did, for your team&#x27;s graph (`hz report published &lt;id&gt; &lt;url&gt;`).

**Usage**:

```console
$ hz report [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `approved`: Log that a Heinzelmännchen&#x27;s draft was...
* `drafted`: Log that a Heinzelmännchen put a draft...
* `fell-back`: Log that a Heinzelmännchen used a stale...
* `heartbeat`: Log that a Heinzelmännchen is alive; one a...
* `published`: Log where a Heinzelmännchen published: a...
* `read`: Log that a Heinzelmännchen read an...
* `sync`: (expert) Copy your Heinzelmännchen&#x27;s logs...

### `hz report approved`

Log that a Heinzelmännchen&#x27;s draft was approved, by whom; the entry&#x27;s time is when.

**Usage**:

```console
$ hz report approved [OPTIONS] {heinzel} {target} {by}
```

**Arguments**:

* `heinzel`: The Heinzelmännchen&#x27;s id, a slug unique in your team, e.g. costas-tracker.  [required]
* `target`: What it acted on: an https URL, an email address, contract:team/&lt;name&gt;#&lt;output&gt; or chat:&lt;channel&gt;.  [required]
* `by`: Who approved it, by roster handle.  [required]

**Options**:

* `--description <str>`: One line a reader of the team&#x27;s graph should know; never content or addresses.
* `--contract <str>`: The team contract it keeps, team:&lt;name&gt;.
* `--part <str>`: The part of the target it concerns, e.g. its row in a tracker.
* `--at ISO-8601`: When it happened, ISO 8601 with offset, within the last 30 days; now by default.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz report drafted`

Log that a Heinzelmännchen put a draft where it will be approved, and from which contract version.

**Usage**:

```console
$ hz report drafted [OPTIONS] {heinzel} {target}
```

**Arguments**:

* `heinzel`: The Heinzelmännchen&#x27;s id, a slug unique in your team, e.g. costas-tracker.  [required]
* `target`: What it acted on: an https URL, an email address, contract:team/&lt;name&gt;#&lt;output&gt; or chat:&lt;channel&gt;.  [required]

**Options**:

* `--contract-version <str>`: The contract&#x27;s version it drafted from.
* `--description <str>`: One line a reader of the team&#x27;s graph should know; never content or addresses.
* `--contract <str>`: The team contract it keeps, team:&lt;name&gt;.
* `--part <str>`: The part of the target it concerns, e.g. its row in a tracker.
* `--at ISO-8601`: When it happened, ISO 8601 with offset, within the last 30 days; now by default.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz report fell-back`

Log that a Heinzelmännchen used a stale version of an input to keep its deadline.

**Usage**:

```console
$ hz report fell-back [OPTIONS] {heinzel} {target} {input}
```

**Arguments**:

* `heinzel`: The Heinzelmännchen&#x27;s id, a slug unique in your team, e.g. costas-tracker.  [required]
* `target`: What it acted on: an https URL, an email address, contract:team/&lt;name&gt;#&lt;output&gt; or chat:&lt;channel&gt;.  [required]
* `input`: The input whose stale version it used, as a target.  [required]

**Options**:

* `--description <str>`: One line a reader of the team&#x27;s graph should know; never content or addresses.
* `--contract <str>`: The team contract it keeps, team:&lt;name&gt;.
* `--part <str>`: The part of the target it concerns, e.g. its row in a tracker.
* `--at ISO-8601`: When it happened, ISO 8601 with offset, within the last 30 days; now by default.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz report heartbeat`

Log that a Heinzelmännchen is alive; one a day is enough for the team to see it isn&#x27;t offline.

**Usage**:

```console
$ hz report heartbeat [OPTIONS] {heinzel}
```

**Arguments**:

* `heinzel`: The Heinzelmännchen&#x27;s id, a slug unique in your team, e.g. costas-tracker.  [required]

**Options**:

* `--contract <str>`: The team contract it keeps, team:&lt;name&gt;.
* `--at ISO-8601`: When it happened, ISO 8601 with offset, within the last 30 days; now by default.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz report published`

Log where a Heinzelmännchen published: a document, an email address, a chat channel.

**Usage**:

```console
$ hz report published [OPTIONS] {heinzel} {target}
```

**Arguments**:

* `heinzel`: The Heinzelmännchen&#x27;s id, a slug unique in your team, e.g. costas-tracker.  [required]
* `target`: What it acted on: an https URL, an email address, contract:team/&lt;name&gt;#&lt;output&gt; or chat:&lt;channel&gt;.  [required]

**Options**:

* `--version <str>`: The artifact&#x27;s version, as its source names it.
* `--description <str>`: One line a reader of the team&#x27;s graph should know; never content or addresses.
* `--contract <str>`: The team contract it keeps, team:&lt;name&gt;.
* `--part <str>`: The part of the target it concerns, e.g. its row in a tracker.
* `--at ISO-8601`: When it happened, ISO 8601 with offset, within the last 30 days; now by default.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz report read`

Log that a Heinzelmännchen read an artifact, which version, and by when its contract needs it.

**Usage**:

```console
$ hz report read [OPTIONS] {heinzel} {target}
```

**Arguments**:

* `heinzel`: The Heinzelmännchen&#x27;s id, a slug unique in your team, e.g. costas-tracker.  [required]
* `target`: What it acted on: an https URL, an email address, contract:team/&lt;name&gt;#&lt;output&gt; or chat:&lt;channel&gt;.  [required]

**Options**:

* `--version <str>`: The artifact&#x27;s version, as its source names it.
* `--needed-by ISO-8601`: By when the reader&#x27;s contract needs it, ISO 8601 with offset.
* `--description <str>`: One line a reader of the team&#x27;s graph should know; never content or addresses.
* `--contract <str>`: The team contract it keeps, team:&lt;name&gt;.
* `--part <str>`: The part of the target it concerns, e.g. its row in a tracker.
* `--at ISO-8601`: When it happened, ISO 8601 with offset, within the last 30 days; now by default.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz report sync`

(expert) Copy your Heinzelmännchen&#x27;s logs into your team repository&#x27;s checkout, for you to commit and push.

**Usage**:

```console
$ hz report sync [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz setup`

Set up what is still missing, wizard style: every step not finished yet, in order; optional steps can be skipped, and a run continues where the last one stopped. With MODULE, set up only that module.

**Usage**:

```console
$ hz setup [OPTIONS] [MODULE]
```

**Arguments**:

* `MODULE`: Set up only this module, by qualified name (`hz setup clock`); every module when left out.

**Options**:

* `--restart`: Forget which steps finished and go through every one again.
* `--answers <file>`: Answer in advance from a TOML file with a table per module, e.g. `role = &quot;engineer&quot;` in the org table; runs without a terminal, for coding agents and teams. Never put secrets in it.
* `--print-answers`: Print the answers of the steps that finished as an answers file, and run nothing.
* `--json`: Print the outcome of every step as JSON on stdout; questions, if any, go to stderr.
* `--help`: Show this message and exit.

## `hz system`

Inspect, configure, diagnose and update Heinzelmännchen itself.

**Usage**:

```console
$ hz system [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `agent`: (expert) Install the Heinzelmännchen...
* `capability`: (expert) Show which adapters serve which...
* `changelog`: (expert) Show the installed version&#x27;s...
* `config`: (expert) Show, get, set and initialize...
* `connector`: (expert) Show whether each connector is...
* `credential`: (expert) Store and check the credentials...
* `daemon`: (expert) Run modules&#x27; jobs in the...
* `doctor`: Run every module&#x27;s health checks; exits...
* `expert`: Expert mode: list every command, or just...
* `info`: (expert) Show versions, interpreter,...
* `layer`: (expert) Show the layer plugins installed...
* `modules`: (expert) List every module and the lets it...
* `notify`: (expert) Pop up what deserves your...
* `plugin`: (expert) Find, add, remove, enable,...
* `release`: (expert) Ask where Heinzelmännchen was...
* `runner`: Run bounded agent sessions with Claude...
* `serve`: (expert) Serve every operation to coding...
* `theme`: (expert) Pick how the web app looks: the...
* `update`: Update Heinzelmännchen, keeping plugins;...

### `hz system agent`

(expert) Install the Heinzelmännchen skills for coding agents.

**Usage**:

```console
$ hz system agent [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `install`: (expert) Copy Heinzelmännchen&#x27;s skills...
* `skills`: (expert) List the Agent Skills that ship...
* `uninstall`: (expert) Remove Heinzelmännchen&#x27;s skills...

#### `hz system agent install`

(expert) Copy Heinzelmännchen&#x27;s skills into each configured agent skill directory.

**Usage**:

```console
$ hz system agent install [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system agent skills`

(expert) List the Agent Skills that ship with Heinzelmännchen.

**Usage**:

```console
$ hz system agent skills [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system agent uninstall`

(expert) Remove Heinzelmännchen&#x27;s skills from each configured agent skill directory.

**Usage**:

```console
$ hz system agent uninstall [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system capability`

(expert) Show which adapters serve which capabilities, and who reads and writes what.

**Usage**:

```console
$ hz system capability [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: (expert) List capabilities, their bindings...

#### `hz system capability list`

(expert) List capabilities, their bindings and adapters, and every requirement with its adapter and hosts.

**Usage**:

```console
$ hz system capability list [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system changelog`

(expert) Show the installed version&#x27;s changelog as Markdown, or with `--newer` what newer releases bring.

**Usage**:

```console
$ hz system changelog [OPTIONS]
```

**Options**:

* `--newer / --no-newer`: Show what the releases newer than the installed version bring instead, newest first; asks GitHub, with GH_TOKEN or GITHUB_TOKEN if set. As JSON: a list of {version, date, notes, url}.  [default: no-newer]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system config`

(expert) Show, get, set and initialize configuration.

**Usage**:

```console
$ hz system config [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `get`: (expert) Show one setting in effect, with...
* `init`: (expert) Write a configuration file with...
* `reveal`: (expert) Show a configuration file in...
* `set`: (expert) Change a setting in the user or...
* `show`: (expert) Show the configuration files...
* `unset`: (expert) Remove a setting from the user or...

#### `hz system config get`

(expert) Show one setting in effect, with its environment variable.

**Usage**:

```console
$ hz system config get [OPTIONS] {key}
```

**Arguments**:

* `key`: Setting as section.key, e.g. hz.log_level or example.greeting.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system config init`

(expert) Write a configuration file with every setting commented out, ready to edit.

**Usage**:

```console
$ hz system config init [OPTIONS]
```

**Options**:

* `--path <path>`: Where to write; defaults to the user configuration file.
* `--force / --no-force`: Overwrite an existing file.  [default: no-force]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system config reveal`

(expert) Show a configuration file in Finder, Explorer or your desktop&#x27;s file manager; nothing is created.

**Usage**:

```console
$ hz system config reveal [OPTIONS] {scope}:<site|user|project>
```

**Arguments**:

* `scope:<site|user|project>`: Which configuration file: site, user or project.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system config set`

(expert) Change a setting in the user or project configuration file, after validating it.

**Usage**:

```console
$ hz system config set [OPTIONS] {key} {value}
```

**Arguments**:

* `key`: Setting as section.key, e.g. hz.log_level or example.greeting.  [required]
* `value`: New value; TOML syntax for numbers, booleans and lists.  [required]

**Options**:

* `--scope <user|project>`: Which file to change: user, or the project&#x27;s hz.toml.  [default: user]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system config show`

(expert) Show the configuration files Heinzelmännchen reads and every setting in effect.

**Usage**:

```console
$ hz system config show [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system config unset`

(expert) Remove a setting from the user or project configuration file, so its default applies again.

**Usage**:

```console
$ hz system config unset [OPTIONS] {key}
```

**Arguments**:

* `key`: Setting as section.key, e.g. hz.log_level or example.greeting.  [required]

**Options**:

* `--scope <user|project>`: Which file to change: user, or the project&#x27;s hz.toml.  [default: user]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system connector`

(expert) Show whether each connector is signed in and serves something, and test one live.

**Usage**:

```console
$ hz system connector [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: (expert) List connectors with their...
* `test`: (expert) Test a connector live: one cheap...

#### `hz system connector list`

(expert) List connectors with their sign-in, what they serve and their tools; offline.

**Usage**:

```console
$ hz system connector list [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system connector test`

(expert) Test a connector live: one cheap read-only call through its tool, with a short timeout.

**Usage**:

```console
$ hz system connector test [OPTIONS] {name}
```

**Arguments**:

* `name`: The connector, as `hz system connector list` names it, e.g. `github`.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system credential`

(expert) Store and check the credentials modules need, in the OS keychain.

**Usage**:

```console
$ hz system credential [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: (expert) List the credentials modules...
* `remove`: (expert) Remove a credential&#x27;s value from...
* `set`: (expert) Store a credential, read from...

#### `hz system credential list`

(expert) List the credentials modules declare and whether each is stored, never their values.

**Usage**:

```console
$ hz system credential list [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system credential remove`

(expert) Remove a credential&#x27;s value from the keychain.

**Usage**:

```console
$ hz system credential remove [OPTIONS] {name}
```

**Arguments**:

* `name`: Name of the credential, as `hz system credential list` shows it.  [required]

**Options**:

* `--service <str>`: Service it is kept under; needed only when several services declare the name.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system credential set`

(expert) Store a credential, read from standard input or a hidden prompt, never from the command line.

**Usage**:

```console
$ hz system credential set [OPTIONS] {name}
```

**Arguments**:

* `name`: Name of the credential, as `hz system credential list` shows it.  [required]

**Options**:

* `--service <str>`: Service it is kept under; needed only when several services declare the name.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system daemon`

(expert) Run modules&#x27; jobs in the background, supervised by launchd or systemd.

**Usage**:

```console
$ hz system daemon [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `install`: (expert) Install and load the launchd...
* `jobs`: (expert) List every job with its schedule,...
* `logs`: (expert) Show the latest lines of the...
* `pause`: (expert) Pause the daemon: ticks run no...
* `resume`: (expert) Resume a paused daemon.
* `status`: (expert) Show whether the daemon is...
* `tick`: (expert) Run the jobs that are due and...
* `uninstall`: (expert) Unload and remove the daemon&#x27;s...

#### `hz system daemon install`

(expert) Install and load the launchd agent or systemd timer that ticks every few minutes.

**Usage**:

```console
$ hz system daemon install [OPTIONS]
```

**Options**:

* `--interval <str>`: Minutes between ticks, dividing an hour: 1m … 30m, or 1h.  [default: 5m]
* `--dry-run / --no-dry-run`: Only show the unit that would be written.  [default: no-dry-run]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon jobs`

(expert) List every job with its schedule, next slot and last run.

**Usage**:

```console
$ hz system daemon jobs [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon logs`

(expert) Show the latest lines of the daemon&#x27;s log.

**Usage**:

```console
$ hz system daemon logs [OPTIONS]
```

**Options**:

* `--lines <int>`: How many of the latest lines to show.  [default: 50]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon pause`

(expert) Pause the daemon: ticks run no job until resumed.

**Usage**:

```console
$ hz system daemon pause [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon resume`

(expert) Resume a paused daemon.

**Usage**:

```console
$ hz system daemon resume [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon status`

(expert) Show whether the daemon is installed, loaded and paused, its last tick and failing jobs.

**Usage**:

```console
$ hz system daemon status [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon tick`

(expert) Run the jobs that are due and write a receipt for each; exits with 1 if any job failed.

**Usage**:

```console
$ hz system daemon tick [OPTIONS]
```

**Options**:

* `--dry-run / --no-dry-run`: Only show which jobs are due; run nothing.  [default: no-dry-run]
* `--job <str>`: Run this job now, as `&lt;module&gt;:&lt;job&gt;` from `hz system daemon jobs`.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system daemon uninstall`

(expert) Unload and remove the daemon&#x27;s unit; receipts and logs stay.

**Usage**:

```console
$ hz system daemon uninstall [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system doctor`

Run every module&#x27;s health checks; exits with 1 if anything is down.

**Usage**:

```console
$ hz system doctor [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system expert`

Expert mode: list every command, or just the everyday ones.

**Usage**:

```console
$ hz system expert [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

**Commands**:

* `off`: Turn expert mode off: help, completion and...
* `on`: Turn expert mode on: help, completion and...

#### `hz system expert off`

Turn expert mode off: help, completion and the web app list the everyday commands.

**Usage**:

```console
$ hz system expert off [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system expert on`

Turn expert mode on: help, completion and the web app list every command.

**Usage**:

```console
$ hz system expert on [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system info`

(expert) Show versions, interpreter, settings, modules and what modules report.

**Usage**:

```console
$ hz system info [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system layer`

(expert) Show the layer plugins installed and the stack you are in, general to specific.

**Usage**:

```console
$ hz system layer [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: (expert) List the installed layer plugins:...
* `show`: (expert) Show your stack: company, org and...

#### `hz system layer list`

(expert) List the installed layer plugins: where each sits, what it brings and what is wrong with the tree.

**Usage**:

```console
$ hz system layer list [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system layer show`

(expert) Show your stack: company, org and site, team; what the layers bring and the settings they recommend.

**Usage**:

```console
$ hz system layer show [OPTIONS]
```

**Options**:

* `--layer <str>`: Show this layer&#x27;s stack instead of yours, e.g. acme-payments-checkout.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system modules`

(expert) List every module and the lets it advertises.

**Usage**:

```console
$ hz system modules [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system notify`

(expert) Pop up what deserves your attention: in the web app while it is open, else natively.

**Usage**:

```console
$ hz system notify [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `ack`: (expert) Mark notices as shown, so the...
* `install`: (expert) Install the native notifier...
* `list`: (expert) Show the latest notices, whether...
* `settings`: (expert) Open the system&#x27;s notification...
* `test`: (expert) Record a test notice, report each...

#### `hz system notify ack`

(expert) Mark notices as shown, so the daemon doesn&#x27;t pop them up again.

**Usage**:

```console
$ hz system notify ack [OPTIONS] {ids}
```

**Arguments**:

* `ids`: Ids of the notices that were shown, from `list`, separated by commas.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system notify install`

(expert) Install the native notifier (Heinzel Notifier on macOS) and pop up a test notification now.

**Usage**:

```console
$ hz system notify install [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system notify list`

(expert) Show the latest notices, whether each popped up and who showed it, and how they reach you.

**Usage**:

```console
$ hz system notify list [OPTIONS]
```

**Options**:

* `--limit <int>`: How many of the latest notices to show.  [default: 50]
* `--before <str>`: Only notices older than this id, for the next page.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system notify settings`

(expert) Open the system&#x27;s notification settings to allow hz&#x27;s pop-ups (macOS: System Settings).

**Usage**:

```console
$ hz system notify settings [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system notify test`

(expert) Record a test notice, report each way it can reach you, and exit 1 if nothing pops it up.

**Usage**:

```console
$ hz system notify test [OPTIONS]
```

**Options**:

* `--native / --no-native`: Pop it up through the native notifier now too, as the web app&#x27;s test button does.  [default: no-native]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system plugin`

(expert) Find, add, remove, enable, disable, list and scaffold plugins.

**Usage**:

```console
$ hz system plugin [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `add`: (expert) Show what a plugin would install,...
* `develop`: (expert) Add the plugin in a directory on...
* `disable`: (expert) Switch a plugin off without...
* `enable`: (expert) Switch a disabled plugin on again.
* `history`: (expert) List plugin changes made on this...
* `list`: (expert) List installed plugins, enabled...
* `new`: (expert) Scaffold a plugin repository from...
* `remove`: (expert) Remove a plugin.
* `search`: (expert) Find plugins to install:...

#### `hz system plugin add`

(expert) Show what a plugin would install, then add it with `--confirm`; adding it again replaces it.

**Usage**:

```console
$ hz system plugin add [OPTIONS] {spec}
```

**Arguments**:

* `spec`: A first-party plugin by name (`github`, `acme`), a PyPI package named heinzel-&lt;name&gt; (`heinzel-crm==1.2.0`), or a git repository over https (`git+https://github.com/acme-example/heinzel-crm@v1.0.0`); private GitHub repositories use your gh login. Pass `requirement` from an earlier answer to install exactly what it showed.  [required]

**Options**:

* `--confirm / --no-confirm`: Install it. Without it, nothing is installed: the answer shows the source, version and publisher to confirm first, because a plugin runs with your rights.  [default: no-confirm]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system plugin develop`

(expert) Add the plugin in a directory on this machine, to try a plugin you are writing.

**Usage**:

```console
$ hz system plugin develop [OPTIONS] {path}
```

**Arguments**:

* `path`: The plugin&#x27;s checkout, e.g. `.` in a repository scaffolded by `new`.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system plugin disable`

(expert) Switch a plugin off without removing it, in ` disabled_modules` of your user file.

**Usage**:

```console
$ hz system plugin disable [OPTIONS] {distribution}
```

**Arguments**:

* `distribution`: Distribution name, as `hz system plugin list` shows it.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system plugin enable`

(expert) Switch a disabled plugin on again.

**Usage**:

```console
$ hz system plugin enable [OPTIONS] {distribution}
```

**Arguments**:

* `distribution`: Distribution name, as `hz system plugin list` shows it.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system plugin history`

(expert) List plugin changes made on this machine, oldest first: what was installed, from where, by whom.

**Usage**:

```console
$ hz system plugin history [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system plugin list`

(expert) List installed plugins, enabled or not, with their source and the modules and adapters they bring.

**Usage**:

```console
$ hz system plugin list [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system plugin new`

(expert) Scaffold a plugin repository from the Heinzelmännchen template, asking for answers not given.

**Usage**:

```console
$ hz system plugin new [OPTIONS] {destination}
```

**Arguments**:

* `destination`: Directory for the new plugin repository.  [required]

**Options**:

* `--data <str>`: Answer a template question as key=value, e.g. plugin_name=crm; repeat for each.
* `--template <str>`: Copier template: a git URL, gh:owner/repo or a local path.  [default: gh:helmut-hoffer-von-ankershoffen/heinzel]
* `--ref <str>`: Template version; defaults to this Heinzelmännchen&#x27;s release tag.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system plugin remove`

(expert) Remove a plugin.

**Usage**:

```console
$ hz system plugin remove [OPTIONS] {distribution}
```

**Arguments**:

* `distribution`: Distribution name, as `hz system plugin list` shows it.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system plugin search`

(expert) Find plugins to install: first-party ones, and community ones by querying the GitHub API.

**Usage**:

```console
$ hz system plugin search [OPTIONS]
```

**Options**:

* `--text <str>`: Words that name or describe the plugin, e.g. `calendar`; sent to GitHub&#x27;s search. All plugins if omitted.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system release`

(expert) Ask where Heinzelmännchen was installed from for its latest release, and whether to update.

**Usage**:

```console
$ hz system release [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system runner`

Run bounded agent sessions with Claude Code or a configured command.

**Usage**:

```console
$ hz system runner [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `ask`: (expert) Ask the bound agent runner,...

#### `hz system runner ask`

(expert) Ask the bound agent runner, read-only and within a cost cap; this may cost money.

**Usage**:

```console
$ hz system runner ask [OPTIONS] {prompt}
```

**Arguments**:

* `prompt`: What to ask: a question, or the conversation so far ending with one; nothing is kept between calls.  [required]

**Options**:

* `--budget-usd <float>`: Most this answer may cost in US dollars, at most the setting system.runner.ask_max_usd.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system serve`

(expert) Serve every operation to coding agents (MCP) or over HTTP (REST), or run the daemon in the foreground.

**Usage**:

```console
$ hz system serve [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `api`: (expert) Serve every operation as a REST...
* `daemon`: (expert) Run the daemon&#x27;s tick in the...
* `mcp`: (expert) Serve every operation as an MCP...

#### `hz system serve api`

(expert) Serve every operation as a REST endpoint; OpenAPI docs at /docs.

**Usage**:

```console
$ hz system serve api [OPTIONS]
```

**Options**:

* `--host <str>`: Interface to listen on.  [default: 127.0.0.1]
* `--port <int>`: Port to listen on.  [default: 8000]
* `--help`: Show this message and exit.

#### `hz system serve daemon`

(expert) Run the daemon&#x27;s tick in the foreground every interval, printing each result as a JSON line.

**Usage**:

```console
$ hz system serve daemon [OPTIONS]
```

**Options**:

* `--interval DURATION`: Time between ticks, e.g. 30s, 5m or 1h.  [default: 5m]
* `--help`: Show this message and exit.

#### `hz system serve mcp`

(expert) Serve every operation as an MCP tool, e.g. `claude mcp add hz -- hz system serve mcp`.

Raises:
    typer.Exit: With 2 when --chat is given with another transport than stdio.

**Usage**:

```console
$ hz system serve mcp [OPTIONS]
```

**Options**:

* `--transport <stdio|streamable-http>`: stdio for agents that start hz; streamable-http to share.  [default: stdio]
* `--host <str>`: Interface for streamable-http.  [default: 127.0.0.1]
* `--port <int>`: Port for streamable-http.  [default: 8765]
* `--chat SESSION`: Serve the tools of this chat session: reads run, everything else waits for your confirmation in the chat. Started by `hz chat` for its agent, over stdio only.
* `--project <path>`: Read the configuration as if started in this directory (its hz.toml), as the chat does.
* `--help`: Show this message and exit.

### `hz system theme`

(expert) Pick how the web app looks: the built-in theme or one a plugin offers.

**Usage**:

```console
$ hz system theme [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `list`: (expert) List the themes of the web app,...
* `use`: (expert) Use a theme in the web app:...

#### `hz system theme list`

(expert) List the themes of the web app, with every colour, font and size, and which one is in effect.

**Usage**:

```console
$ hz system theme list [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

#### `hz system theme use`

(expert) Use a theme in the web app: writes ` name` to your configuration file.

**Usage**:

```console
$ hz system theme use [OPTIONS] {name}
```

**Arguments**:

* `name`: The theme to use, one `hz system theme list` shows, e.g. `default`.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz system update`

Update Heinzelmännchen, keeping plugins; see what it brings with `hz system changelog --newer`.

**Usage**:

```console
$ hz system update [OPTIONS]
```

**Options**:

* `--source <str>`: Install from here instead, e.g. git+https://github.com/org/heinzel@v1.2.0 or a wheel.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz team`

Your team&#x27;s repository: create one (`hz team new`), join one by its URL (`hz team join`), add people to it.

**Usage**:

```console
$ hz team [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `add`: Add someone to your team&#x27;s roster, as one...
* `adopt`: (expert) Make an existing checkout your...
* `join`: Clone a team repository with your own...
* `new`: Create a team repository from the...
* `show`: Show your team: its repository, checkout...

### `hz team add`

Add someone to your team&#x27;s roster, as one of its owners: a pull request the other owners see.

**Usage**:

```console
$ hz team add [OPTIONS] {handle}
```

**Arguments**:

* `handle`: Their handle: lowercase letters, digits, dashes.  [required]

**Options**:

* `--login <str>`: Their GitHub login; an owner needs one.
* `--role <str>`: Their role: one of engineer, principal, engineering-manager, engineering-leadership, product-manager, product-leadership, program-manager.  [default: engineer]
* `--owner / --no-owner`: Make them an owner: they approve roster changes.  [default: no-owner]
* `--by <str>`: Your own GitHub login, when `gh` isn&#x27;t signed in as you.
* `--yes / --no-yes`: Carry out the steps that wait for you: remote changes, and making the team yours.  [default: no-yes]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz team adopt`

(expert) Make an existing checkout your team repository; replaces the deprecated `workflow.team_repository`.

**Usage**:

```console
$ hz team adopt [OPTIONS] {path}
```

**Arguments**:

* `path`: A checkout of your team repository you already have.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz team join`

Clone a team repository with your own access, find you in its roster, and make it your team.

**Usage**:

```console
$ hz team join [OPTIONS] {url}
```

**Arguments**:

* `url`: The team repository&#x27;s URL, the invite: https, ssh or a local path.  [required]

**Options**:

* `--propose / --no-propose`: When the roster doesn&#x27;t list you, open a pull request adding you; owners merge it.  [default: no-propose]
* `--handle <str>`: Your handle: the roster entry that is you when it has no login yet, or the one to propose.
* `--role <str>`: Your role when you propose to be added: one of engineer, principal, engineering-manager, engineering-leadership, product-manager, product-leadership, program-manager.  [default: engineer]
* `--login <str>`: Your GitHub login, when `gh` isn&#x27;t signed in; asked of `gh api user` otherwise.
* `--yes / --no-yes`: Carry out the steps that wait for you: remote changes, and making the team yours.  [default: no-yes]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz team new`

Create a team repository from the template: you its first owner, its roster drafted for review.

**Usage**:

```console
$ hz team new [OPTIONS] {name}
```

**Arguments**:

* `name`: The team&#x27;s name: lowercase words joined by dashes, e.g. payments.  [required]

**Options**:

* `--github <str>`: Create it on GitHub as this private repository, owner/name; only on this machine if none.
* `--roster <str>`: Draft the roster from a GitHub team you can read: github:&lt;org&gt;/&lt;team&gt;, or github:&lt;org&gt;.
* `--role <str>`: Your role: one of engineer, principal, engineering-manager, engineering-leadership, product-manager, product-leadership, program-manager.  [default: engineering-manager]
* `--handle <str>`: Your handle in the team; defaults to your GitHub login.
* `--login <str>`: Your GitHub login, when `gh` isn&#x27;t signed in; asked of `gh api user` otherwise.
* `--yes / --no-yes`: Carry out the steps that wait for you: remote changes, and making the team yours.  [default: no-yes]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz team show`

Show your team: its repository, checkout and members, and what is wrong with them.

**Usage**:

```console
$ hz team show [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz ui`

Open the web app in your browser, served on localhost; Ctrl-C stops it.

**Usage**:

```console
$ hz ui [OPTIONS]
```

**Options**:

* `--port <int>`: Port on 127.0.0.1;  ui_port by default, 0 picks a free one.
* `--browser / --no-browser`: Open the default browser at the app; --no-browser prints only its URL.  [default: browser]
* `--devices`: List the browsers and installed apps paired with hz, then stop.
* `--revoke <str>`: Unpair a device by its id from --devices, or `all`, then stop.
* `--help`: Show this message and exit.

## `hz workflow`

Workflow contracts: Markdown files an agent drafts from, by hand or on your daemon&#x27;s schedule (`hz workflow new` starts one).

**Usage**:

```console
$ hz workflow [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `check`: (expert) Check contracts and list every...
* `discover`: (expert) Find candidates for each...
* `drafts`: List the drafts runs saved locally, newest...
* `graph`: (expert) Show the workflow your contracts...
* `list`: List the contracts in your and your team&#x27;s...
* `new`: Write a starter contract, or a playbook&#x27;s...
* `playbooks`: (expert) List the playbooks plugins...
* `prune`: (expert) Delete drafts and records older...
* `run`: Render a contract now: fetch its inputs,...
* `sent`: (expert) Record how much of a draft you...
* `show`: Show a contract as the daemon runs it:...

### `hz workflow check`

(expert) Check contracts and list every problem with file and line; exits with 1 on errors, so CI can gate.

**Usage**:

```console
$ hz workflow check [OPTIONS]
```

**Options**:

* `--path <path>`: A contract file, or a directory searched for *.prose.md; every contract found if none.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow discover`

(expert) Find candidates for each playbook&#x27;s asset types through the capabilities you have bound; reads only.

**Usage**:

```console
$ hz workflow discover [OPTIONS]
```

**Options**:

* `--playbook <str>`: Only this module&#x27;s playbook; every playbook&#x27;s if omitted.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow drafts`

List the drafts runs saved locally, newest first, with their paths and records, or show the newest.

**Usage**:

```console
$ hz workflow drafts [OPTIONS]
```

**Options**:

* `--name <str>`: Only this contract&#x27;s drafts; every contract&#x27;s if omitted.
* `--latest / --no-latest`: Print the newest draft instead of the list.  [default: no-latest]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow graph`

(expert) Show the workflow your contracts make: what each maintains and reads, cycles, unmaintained inputs.

**Usage**:

```console
$ hz workflow graph [OPTIONS]
```

**Options**:

* `--mermaid / --no-mermaid`: Print only the Mermaid flowchart, to paste into a document or a pull request.  [default: no-mermaid]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow list`

List the contracts in your and your team&#x27;s workflows directories, with their schedule and problems.

**Usage**:

```console
$ hz workflow list [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow new`

Write a starter contract, or a playbook&#x27;s template (--template), into your workflows directory.

**Usage**:

```console
$ hz workflow new [OPTIONS] {name}
```

**Arguments**:

* `name`: The contract&#x27;s name: lowercase words joined by dashes, e.g. weekly-digest.  [required]

**Options**:

* `--team / --no-team`: Write it into the team repository&#x27;s workflows/ instead of your own directory.  [default: no-team]
* `--template <str>`: Start from a playbook&#x27;s template contract, `&lt;module&gt;:&lt;name&gt;` (`hz workflow playbooks` lists them), instead of the blank starter.
* `--asset <str>`: Fill a template&#x27;s asset placeholder as type=reference, e.g. weekly-report=doc-123 (`hz workflow discover` finds candidates); repeat for each.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow playbooks`

(expert) List the playbooks plugins contribute: asset types, template contracts and recommended settings.

**Usage**:

```console
$ hz workflow playbooks [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow prune`

(expert) Delete drafts and records older than ` retention_days` (default 90); receipts are kept.

**Usage**:

```console
$ hz workflow prune [OPTIONS]
```

**Options**:

* `--dry-run / --no-dry-run`: Only list what would be deleted; delete nothing.  [default: no-dry-run]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow run`

Render a contract now: fetch its inputs, run the agent and save the draft; nothing is published.

**Usage**:

```console
$ hz workflow run [OPTIONS] {name}
```

**Arguments**:

* `name`: The contract&#x27;s name, or `&lt;namespace&gt;:&lt;name&gt;` (`team:` or `me:`) when both have it.  [required]

**Options**:

* `--dry-run / --no-dry-run`: Only fetch the inputs and show the packet; run no agent.  [default: no-dry-run]
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow sent`

(expert) Record how much of a draft you changed before sending it; `hz workflow show` reports it.

**Usage**:

```console
$ hz workflow sent [OPTIONS] {name}
```

**Arguments**:

* `name`: The contract&#x27;s name, or `&lt;namespace&gt;:&lt;name&gt;` (`team:` or `me:`) when both have it.  [required]

**Options**:

* `--file <path>`: What you finally sent, saved as a text or Markdown file; or give --ref.
* `--ref <str>`: The document the approved version lives in, read through the documents capability.
* `--draft <path>`: The draft it was edited from; the contract&#x27;s newest draft if omitted.
* `--edit-minutes <float>`: Your own estimate of the minutes you spent editing; optional.
* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

### `hz workflow show`

Show a contract as the daemon runs it: schedule, requirements, adapters, readiness, next steps.

**Usage**:

```console
$ hz workflow show [OPTIONS] {name}
```

**Arguments**:

* `name`: The contract&#x27;s name, or `&lt;namespace&gt;:&lt;name&gt;` (`team:` or `me:`) when both have it.  [required]

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.

## `hz zoom`

(expert) Read meetings from Zoom&#x27;s meeting-summary emails: the meetings capability.

**Usage**:

```console
$ hz zoom [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `info`: (expert) Show this plugin&#x27;s version, the...

### `hz zoom info`

(expert) Show this plugin&#x27;s version, the Heinzelmännchen it runs on and where its meetings come from.

**Usage**:

```console
$ hz zoom info [OPTIONS]
```

**Options**:

* `--format <text|json>`: Output format; defaults to the output_format setting (HZ_OUTPUT_FORMAT), which is text.
* `--json`: Shorthand for --format json, for scripts and agents.
* `--help`: Show this message and exit.
