---
status: proposed
date: 2026-10-24
decision-makers: maintainer
amends: 0018, 0029, 0039
related: 0003, 0019, 0020, 0024, 0027, 0028, 0030, 0038, 0043
amended-by: 0049, 0050, 0051
---

# 0045: A chat that knows where you are and acts through hz, with every change confirmed in code

## Context and Problem Statement

The maintainer wants Heinzelmännchen to "do notifications, become proactive, [and have] a good integrated chat": the
daemon notices something, a notification says so, and a click opens a chat about it that can act. ADR 0039 gave the
web app a stateless chat (`hz system runner ask`) whose agent has no tools: it can only name commands. ADR 0029 kept
the agent read-only and deferred hz operations as agent tools. A chat that acts needs tools, and tools that write,
publish or spend need the person's yes. How does a chat get hz's operations as tools without an agent ever changing
something on its own, and where do its sessions, context and costs live?

## Decision Drivers

- Heads are views on operations (ADR 0003): the chat is operations every head can offer, not a UI feature.
- Writes happen only with the person's confirmation, and the rule must hold whatever the model does: enforced in
  code, never by asking the agent in its prompt.
- Agent agnostic (ADR 0029): Claude Code today, a Claude API runner or another agent later.
- Nothing is sent home (ADR 0027); names, not addresses, in prompts (ADR 0024, 0028).
- INTENT.md's non-goal "not a chat bot": the chat is no general-purpose assistant but a view on hz's operations that
  drafts and acts in your tools only once you confirm; INTENT.md says so now.
- Costs are capped and visible before and after every message.

## Considered Options

- **hz's operations as tools through the MCP head, scoped to one chat session; reads run, everything else is a
  proposal that only the person's `confirm` runs** (chosen)
- **Tell the agent in its prompt to ask first**: one jailbreak or confused model away from a write nobody approved.
- **Let the agent run writes behind Claude Code's permission prompts**: headless runs have no person at the prompt,
  and the rule would live in one vendor's CLI, not in hz.
- **Give the agent only read tools, and let the person type the commands it suggests**: safe, but not a chat that
  acts; the person copies commands the chat could have run for them.
- **Resume the agent's own session between messages** (Claude Code `--resume`): ties sessions to one runner and to
  its storage; hz keeps the transcript instead, so any runner continues it.

## Decision Outcome

Chosen option: **a chat session runs the bound agent runner with hz's operations as tools from the MCP head, served
over stdio for that session only; read operations run, every other one becomes a proposed action that runs only
when the person confirms it**, because the rule lives in hz's tool layer and in the confirming operation, where no
prompt can change it.

**A head feature built from operations.** `system.runner.chat` (`hz chat`) has `open`, `send`, `confirm`, `decline`,
`show`, `list` and `remove`, on the CLI and the web head only (`heads={"cli", "web"}`): never on the MCP head, so an
agent (the chat's own or any MCP client) can't reach `confirm`, and never on the unauthenticated REST head. The gate
assumes the chat's agent has no shell (see Runners); an agent you run yourself with a shell can type `hz chat confirm`
like you can. The CLI head mounts the group at the top level only, as `hz chat` (not under `hz system runner`), an
exception to ADR 0018 like `hz ui`, by the maintainer's request; without a subcommand it is the interactive chat. The
CLI head names the module (`system.runner.chat`) for that, the way it names `system` for `hz system serve`. The module
sits under `system.runner` because it is the runner's conversational face and shares its per-message cap; Home and the
MCP head need it in the core, so a plugin was not an option. The web app's Home is the chat, with a "Today" strip of cards (what needs you) above the
conversation; every other screen has the same session in its right sidebar.

**Tools are hz operations.** Each message runs the runner with a `ToolProfile` that names one MCP server, `hz system
serve mcp --chat <session>` started by the agent over stdio (no network port, so nothing else on the machine or the
network can call it). It offers what the MCP head offers: `heads=` applies, but what CLI help and the web navigation
hide outside expert mode (`audience="expert"`, ADR 0041) is still a tool. In chat mode:

- A read-only operation runs and returns its result as JSON, cut at 20,000 characters, with email addresses
  replaced by `[email]`.
- Any other operation does not run. The head hands it to the kernel's `ActionGate` as a `ProposedCall` (module,
  operation, tool, the arguments as JSON and the call as a command line) and returns "Not run … (action a1)". The
  `system.runner.chat` module provides the gate and stores the proposal with the session.
- `confirm` runs the stored arguments through `Kernel.invoke`, filtered to the operation's own parameters (a key
  naming an injected service is dropped), once: the action is saved as `running` before it runs, and a running, done,
  failed or declined action is refused. Only operations the MCP head offers can run. What the person reads (the
  command line in the CLI, the card's command and its arguments as JSON on the web) is built from what runs, never
  from text the proposal carried. `decline` records the no. The CLI asks `Run it? [y/N]` (anything but y declines;
  Ctrl-D leaves and keeps it pending); the web app shows a card with Confirm and Decline.
- Some operations never run from a chat, confirmed or not, because one click on a plausible proposal would install
  code or widen what agents may do: `system plugin add/remove/new`, `system update`, `system agent install/uninstall`,
  `system config init`, `system credential set/remove`, `system daemon install/uninstall/resume`, and `system config
  set/unset` of `hz.*`, `capabilities.*`, `system.runner.*`, `system.credential.*`, `system.daemon.*`,
  `system.agent.*`, `system.changelog.*`, `system.plugin.*`, `workflow.*` and `team.*`, and of every setting a project
  `hz.toml` may not set (`ignored_in_project`, in any module, plugins' too: what a repository someone else controls may
  not decide, an agent may not either; your profile is among them) (`system.runner.chat._policy`). They are refused
  when proposed and again at confirm; the person runs them in a terminal.

The kernel gains `ActionGate` and `ProposedCall` (`heinzel.kernel._chat`; not `ChatGate`, since `Chat` is the
messaging capability), `McpServer`, `ToolProfile.mcp_servers` and `ToolProfile.read_files` in the agent runner
protocol, `AgentResult.fix` (a command that fixes a failed run, such as `claude auth login`), `Progress.emitter()` and
its `Emit` type for streaming (instead of a new `AgentRunner` parameter, so existing adapters keep working), and
`redact_emails`. All are additive (ADR 0026). The agent session itself stays in mode `read`: it changes nothing; the
person does, through `confirm`. That is the rule for mode `read` with MCP servers: **every server in a profile must
change nothing on its own**; hz's chat server only proposes, and only `system.runner.chat` builds a profile with one.
An adapter written before this change ignores `mcp_servers`; ADR 0029's planned contract-test suite gains "honour
`mcp_servers` or warn".

**Runners.** The Claude Code adapter writes the servers to `--mcp-config` in its private temporary directory and
allows `mcp__<name>`. Claude Code 2.1.310 loads no `--mcp-config` server under `--safe-mode` (its init event lists
none), so such a run drops `--safe-mode` for `--setting-sources ""` (no user, project or local settings, so no
plugins, hooks or skills from them); `--restricted`, `--strict-mcp-config` and `--disable-slash-commands` stay. A chat
asks for `read_files=False`, so the run gets `--tools ""`: no built-in tools at all, not even Read, which could take an
absolute path; it reads only through hz. Every run now passes `--include-partial-messages`, so answers stream; it adds
lines to the output the 8 MiB cap bounds. Claude Code's own API errors (`is_error: true`, often with `subtype:
success`) are failures with their text, and a missing or expired login names its fix, `claude auth login`; doctor
asks `claude auth status --json` in an empty directory, which reads the login and calls no model. The command adapter gets an `{mcp_config}` placeholder
and warns when its command doesn't take it. A Claude API runner (a plugin, ADR 0029: no SDK in core) can connect to
the same stdio server as an MCP client, so the gate stays in hz whatever the runner.

The agent starts the chat's server in its own empty working directory, so the command carries `--project <dir>`,
the directory of the process that opened the chat: the server reads the same project `hz.toml` (settings, state
directory) as the head that confirms.

**Sessions and context.** A session is a private directory (0700, files 0600) under `<state_dir>/chat`: the
conversation and the person's decisions in `session.json`, written under a lock by the process answering or
confirming; what the agent proposed in `proposals.jsonl`, appended by the chat's MCP server under its own lock. A
session is opened with a context (`route:/doctor`, `contract:<name>`, `draft:<id>`, `event:<id>`, `text:<words>`, or a
bare event id) and an optional detail in words; the context seeds the runner's contract (its standing instructions) with what the
chat is about and which read tool finds it. Each message sends the most recent messages that fit in 12,000
characters (one message takes at most 4,000: its start and its end), so any runner continues a session. Session and
action ids are checked against their patterns, and a session file must name its own directory. Pruning skips a
session that is answering or confirming, and a session removed meanwhile is never recreated. Bounds: 200 messages and 100 proposals per session; sessions are
removed `keep_days` (30) after their last change and beyond `keep_sessions` (50).

**Notification to chat.** `hz chat open --context <event-id> [--detail "<text>"] --json` returns a session; the web
app opens a chat about an event from `/?chat-context=event:<id>`, resumes one from `/?chat=<session>`, and focuses a
Today card from `/?card=<id>` (a notice's card is `event:<id>`). Opening a chat about a notice without a detail fills
it from the notice itself (title, body, where it shows), read through the kernel's `Notifications`
([ADR 0043](0043-desktop-notifications-one-policy-shown-once.md)); the Today strip shows the last day's notices
(`hz system notify list`) and leaves out a doctor or failed-run card a notice already covers.

**Streaming.** The web head answers `POST /api/stream/<path>` for each of its operations with server-sent events:
what the operation emits through `Progress.emitter()` (`text`, `tool`, `task`), then `result` or `error`, behind the
same token and checks as `/api/<path>`. The routes are left out of the OpenAPI document (each answers its
operation's result inside a `result` event), so `web/src/api/stream.ts` types the result by the operation's own
response type. The page reads it with `fetch`, which sends the token header that `EventSource` can't. `hz chat` prints
the same events. Notifications should use this format and reader for their server pushes. Closing the tab doesn't
stop a run: it finishes within its cap and timeout, and its answer is kept; Ctrl-C in `hz chat` stops the agent and
its process group.

**Privacy.** Email addresses (Unicode and `%40`-encoded ones too) are redacted from the context, the transcript,
every tool result (before it is cut) and every tool error before they reach the runner; an unexpected error reaches
the agent as its type only. Sessions stay on this machine. The chat's MCP server, like every program hz starts, gets
no `HZ_CREDENTIAL_*` variables: operations that need a credential read it from the keychain, so on a machine that
keeps credentials only in the environment, the chat's read tools can't use them.

**Costs.** Each message is capped by `[system.runner] ask_max_usd` and by what is left of `[system.runner.chat]
session_max_usd` (default 2 USD), both user scope only; a message is refused once less than one cent is left. An
answer whose cost isn't reported (stopped at its timeout, or a runner that doesn't say) counts at the cap it ran
with, so the session cap holds whatever the runner reports. The caps show before the first message, and each answer
shows its cost and the session's total.

### Consequences

- Good: the chat acts with the whole surface of hz, plugins included, and no model can make it write: the gate is a
  type the MCP head asks for and an operation the agent can't reach.
- Good: the same session works in a terminal, on Home and in the sidebar; any runner continues it.
- Bad: the Claude Code chat run gives up `--safe-mode` for `--setting-sources ""`; a user-level `CLAUDE.md` may
  still load into its context. It has no built-in tools, so it reads nothing but what hz's tools return.
- Bad: every message resends a transcript, which costs tokens a resumed agent session would cache.
- Bad: query parameters carry messages to the local web head, as every operation's parameters do (ADR 0039); they
  never leave the machine and the server logs no URLs.
- Neutral: `system runner ask` stays, for one-off questions from scripts and agents.

### Confirmation

- `tests/unit/heads/mcp_chat_test.py`: a chat gets the MCP head's whole tool surface and none of the chat's own
  operations; a read runs and comes back without email addresses; a write runs nothing and is proposed with its
  exact arguments.
- `tests/unit/modules/runner_chat_test.py`: sessions are private, never paths, listed newest first and pruned by age
  and count; proposals get one id each and are bounded; the context and transcript are redacted; a message gets the
  session's MCP server, the per-message and the remaining session cap; `confirm` runs a proposal once with its
  arguments, refuses unknown, declined and CLI-only operations, and a busy session.
- `tests/integration/chat_test.py`: `hz chat` with a fake runner streams the answer and runs a proposal only after
  `y`, declines on anything else, `--continue` resumes; `hz chat open --context <event-id>`; the chat's MCP server
  over a real MCP client proposes and runs nothing; the web head streams a message as server-sent events behind the
  token; the REST head has no chat and no streams.
- `tests/unit/modules/runner_claude_test.py`: MCP servers in a private `--mcp-config` instead of `--safe-mode`;
  streamed text and tool events; recorded API errors (not logged in, an expired login, rate limit, overload) are
  failures with a message and a fix. `web/src/layout/ChatPanel.test.tsx` and `web/src/pages/Home.test.tsx`: confirm
  cards run an action only on Confirm, notification links open a chat, Today cards open, ask and dismiss.

## More Information

- Decided by the maintainer, 2026-10-24: notifications, proactivity and an integrated chat; the chat-first Home
  "like a consumer assistant, but for product teams"; writes confirmed in the chat, enforced in hz's tool layer.
- Open: an approval card for drafts (ADR 0038) as a Today card; the notify service's event operation and its id format;
  a Claude API runner plugin; whether `send` should run in the background and survive closing the tab.

**Amendment 2026-10-24: one chat panel, beside or below.** Requested by the maintainer the same day: the chat is a
right sidebar whenever the window has room, on Home too. The web app has one panel with the chat on top and the
terminal under it (each alone when the other is closed); it sits beside the screen while the screen keeps at least
30rem next to it, and below the screen, the same stack across the width, when the window is narrower; never both, and
no overlay. Home shows "Today" in the main area and opens the panel; its cards start a chat about themselves in it. The
panel's header switches between sessions (`hz chat list`), the Workshop (a session about nothing in particular) and a
new chat; its list groups items by kind, so the durable Heinzelmännchen of ADR 0049 become one more group. Nothing about
sessions, tools or confirmation changes.
