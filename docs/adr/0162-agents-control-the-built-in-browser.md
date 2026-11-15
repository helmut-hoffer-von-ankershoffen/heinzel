---
status: proposed
date: 2026-11-15
implementation: built
decision-makers: maintainer (need), architect (shape)
amends: 0140, 0045, 0142, 0146
amends-0140: the sidebar browser has tabs, and an agent may open and drive its own tabs through the desktop
amends-0045: a browser input runs without a per-call confirmation, unless it commits; an operation's own marker decides whether an agent's write runs, waits for the person or is refused
amends-0142: tokens are also made, listed and revoked in Settings; MCP over HTTP is a path on hz's web server, switched on and off
amends-0146: a Heinzel calls the tools of found MCP servers directly; hz is the MCP client
related: 0019, 0041, 0068, 0084, 0157, 0159, 0160
---

# 0162: Agents control the built-in browser

The owner's decisions are recorded under More Information.

## Context and Problem Statement

The desktop app has a small browser beside the work ([ADR 0140](0140-isolate-sidebar-browser-content-in-the-desktop.md)):
one isolated guest per window, one persistent website session (`persist:hz-browser`), public HTTPS only. Only the
person drives it. The maintainer decided: "Agents must be able to remote control browser including
opening/closing tabs. Model like other browser MCPs." And, asked which agents: "I mean Heinzel - and expose as well
via mcp".

So the first users are the Heinzels, hz's own chat agents ([ADR 0157](0157-one-kind-of-chat-every-chat-is-with-a-heinzel.md)).
The maintainer then corrected the path: "internally the Heinzel must be able to directly call all mcps loaded and have
direct access to mcp tools of Heinzel - the mcp server of hz must only be needed to start if external agents want to
interact with Heinzel / at best via same Webserver as hz for ui and api".

What the code did before this decision:

- A Heinzel's chat turn is an agent-runner session whose only tool source is one MCP server, hz itself: the turn
  starts `hz system serve mcp --chat <session>` as a stdio child process (`modules/system/runner/chat/_service.py`),
  for the Claude Code runner and for the Pydantic AI engine runner alike
  ([ADR 0159](0159-plugins-bring-their-own-ai-models-through-pydantic-ai.md); the engine runner speaks to that child
  with its own small client, `modules/ai/access/_mcp_stdio.py`). So a Heinzel reaches hz's own operations through a
  process and a protocol hop, never directly.
- The MCP servers hz found ([ADR 0146](0146-found-mcp-servers-are-on-by-default-and-each-can-be-switched-off.md)) have
  a switch and nothing else: "This slice starts, stops and connects to nothing." No Heinzel can call any of their
  tools.
- An in-process tool catalog exists (`modules/heinzel/_host_tools.py`, `_interactive_turn.py`), but it is internal,
  bound to a host allowlist, and never runs a write.
- MCP over HTTP for outside agents is a separate foreground process on its own port (8765), CLI only
  ([ADR 0142](0142-mcp-over-http-takes-per-agent-bearer-tokens-with-read-and-write-scopes.md)).

Writes from a Heinzel are proposed and the person confirms them ([ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md)).

The browser holds the person's website sign-ins. An agent that drives it acts as the person on those sites, and every
page it reads may contain text written to steer it. One confirmation per click would make the feature unusable; no
confirmation at all would let a page talk an agent into sending, buying or deleting.

## Decision Drivers

- Heinzels first, and directly: a Heinzel calls its tools inside hz, with no MCP server in between.
- hz's MCP server exists for outside agents only, and nothing extra has to run for it.
- One rule set: approval and the commit gate are the same code on both paths.
- Familiar: the tools look like the established browser MCPs, so any model can use them without training.
- The rules are in code. Nothing here depends on the model behaving.
- The person sees an agent work, and can take over or stop it at any moment.
- ADR 0140's containment stays whole: public HTTPS only, no product session, no native privileges.
- Small: one module, one bridge, one gate.

## Considered Options

- **hz operations in a `ui browser` module, called directly by Heinzels and over MCP by outside agents, executed by
  the desktop over a bridge, on agent-owned tabs of the shared
  website session, free on any public site, with a commit gate and hard limits** (chosen).
- **Ship Playwright MCP or a headless Chromium beside hz**: a second browser the person cannot see, with its own
  sign-ins, outside ADR 0140's network policy. Rejected.
- **Let agents drive the person's own tab**: the agent and the person fight over one page, and the agent sees whatever
  the person has open. Rejected as the default; sharing one tab on purpose comes in phase 2.
- **A separate website session for agents**: safest, and the agent is signed in nowhere, which removes most of the
  use. Rejected, see the owner decisions.
- **A grant per agent and site, asked in a desktop dialog**: the first draft. Rejected by the maintainer: "Allow
  agents to freely use the browser."
- **Injected page scripts instead of the debugger protocol**: the page can see and tamper with injected helpers, and
  synthetic events are not trusted events. Rejected.

## Decision Outcome

Chosen option: **agents drive their own tabs in the built-in browser through hz operations.** Checkable statements:

#### 1. Tabs

1. The sidebar browser has tabs. A tab has an opaque id (`t-` and 8 hex digits), an owner and one guest view as in
   ADR 0140. The owner is `person` or one agent. At most 8 tabs of the person, 4 per agent, 16 in all per window.
2. All tabs use `persist:hz-browser`. An agent tab is signed in wherever the person is.
3. An agent sees, reads and drives only tabs it opened. The person's tabs are not listed to any agent.
   Phase 2: "Share this tab with <agent>" hands one tab over on the person's click, and "Stop sharing" takes it back.
4. Agent tabs are in the same tab strip. A Heinzel's tab shows the Heinzel's name and face; an outside agent's tab
   shows its token's actor name. Selecting one shows the live page.
   Above it a bar says "<agent> is using this tab" with **Take over** and **Stop**. The Browser button in the
   toolbar shows a mark while any agent tab exists, also when the panel is closed.
5. **Take over** pauses that agent on that tab: its calls return "the person took over this tab" until **Hand back**.
   Real keyboard or mouse input by the person inside an agent tab takes over as well. **Stop** closes all tabs of
   that agent and refuses its browser calls until the person's next message to it (a Heinzel) or until the person
   allows it again (an external agent).
6. Agent tabs work with the panel closed, are never restored after a restart, and close after 30 minutes without a
   call. Closing the product window closes them.

#### 2. The tools

A new core module `ui browser`. Tool names follow the command path, as for every hz operation. Each takes `tab`
unless noted. All are `audience: everyday`: a Heinzel's chat turn gets the everyday operations
([ADR 0041](0041-expert-mode-newcomers-see-the-everyday-commands.md)), and using the browser is everyday work for
a Heinzel. A Heinzel calls them directly (section 4); outside agents get the same operations on the MCP head.

Only `ui_browser_evaluate` is `expert`.

| Tool | Inputs | read_only | Phase |
|---|---|---|---|
| `ui_browser_tabs` | none; returns `available`, and the caller's tabs: id, url, title, status, paused | yes | 1 |
| `ui_browser_open` | `url` (optional); opens a new agent tab, returns its id | no | 1 |
| `ui_browser_close` | `tab` | no | 1 |
| `ui_browser_navigate` | `url`, or `to`: `back`, `forward`, `reload` | no | 1 |
| `ui_browser_read` | `filter`: `all` or `interactive`; `ref` (subtree); `max_chars`. Accessibility tree with `ref_N` | yes | 1 |
| `ui_browser_text` | `max_chars`. Visible text of the page | yes | 1 |
| `ui_browser_screenshot` | `scale` (0.1 to 1). PNG of the tab only, longest edge 1568 px at most | yes | 1 |
| `ui_browser_scroll` | `ref` or `x`,`y`; `direction`; `amount` | yes | 1 |
| `ui_browser_click` | `ref` or `x`,`y`; `button`; `count`; `modifiers` | no | 1 |
| `ui_browser_type` | `text`; `ref` (optional, focuses it first) | no | 1 |
| `ui_browser_key` | `keys`, such as `Enter` or `ctrl+a` | no | 1 |
| `ui_browser_find` | `query`; up to 20 matching refs | yes | 2 |
| `ui_browser_fill` | `ref`, `value`; input, select, checkbox | no | 2 |
| `ui_browser_hover` | `ref` or `x`,`y` | yes | 2 |
| `ui_browser_wait` | `seconds` (10 at most) or `text` to appear | yes | 2 |
| `ui_browser_console` | `pattern`, `limit`; console messages of the tab | yes | 3 |
| `ui_browser_evaluate` | `code`; stdio MCP only; every call is confirmed by the person | no | 3 |

Scrolling and hovering are read-only: they change what is shown, not what the site stores. A result is cut at the
head's existing limit (20,000 characters). The MCP head gains image results for the screenshot.

Not offered, each needs its own decision: cookies, storage, request or response bodies, downloads, uploads, PDF,
window size, and any tool that drives the YouTube player.

#### 3. Safety

1. **Page content is data.** What a Heinzel sees of a page (tree, text, screenshot, console) enters its turn as a
   tool result marked as website content: untrusted data, like a document or a notice it reads, never part of its
   mission, its rules or the person's message (consistent with ADR 0157 and
   [ADR 0160](0160-chat-is-an-extra-place-for-notices-and-questions.md)). No tool result can change a limit
   or an approval. The tool descriptions and the Heinzels' rules say that a page asking for an action is reported
   to the person, not obeyed. Code does not rely on that: statements 2 to 5 hold whatever the model does. The same
   marking goes to outside agents.
2. **Free use of public sites.** A Heinzel or an outside agent opens, reads, navigates, clicks, types and scrolls on
   any public site without asking, signed in wherever the person is. There is no grant per site and no dialog.
   What holds an agent are statements 3 to 8 and the visible tab with Take over and Stop.
3. **The commit gate** (amends ADR 0045). `click`, `type`, `key` and `fill` run without a
   per-call confirmation, except a **commit**, which is proposed to the person and runs only after their yes, with
   exactly the proposed target. A commit is: a submit control; `Enter` in a form field; a button or link whose name
   matches the commit word list (send, post, publish, reply, buy, order, pay, checkout, book, transfer, delete,
   remove, cancel, unsubscribe, confirm, accept, agree, sign, share, invite, and their German forms); any input on a
   page with a payment field; and any click by coordinates whose target cannot be resolved. For a Heinzel the
   proposal is the chat's usual confirmation card (`ActionGate`). For an external agent it is a card on that tab in
   the browser panel; the call waits up to 120 seconds. No approval proof passes through a tool
   ([ADR 0068](0068-human-approval-proofs-and-head-parity.md)). The word list is a
   heuristic, and the documentation says so.
   **No unapproved click lands in the tab's main document.** While the desktop presses for an agent, the release
   and the click of that press belong to the described control alone: a guard in the desktop's isolated world, on the
   page's window, stops and prevents any trusted mouseup, pointerup, click, auxclick or dblclick that
   would reach anything else, the page's own root included. It is armed for one press, ends when the press is
   over and by itself after one second. A benign page whose layout shifts under the pointer therefore cannot turn
   an agent's ordinary click into "I agree" or "add to cart". The tool answers "done" only when the click was seen
   on the described control; otherwise "Not confirmed", and the agent reads the page again. A click into a frame or
   at a place the desktop cannot identify, which only ever runs after the person's yes, is answered "sent, not
   confirmed" and logged so. Limits, stated plainly: the guard covers the tab's main document only. Frames inside the
   page are not armed; a frame of another site is protected by the checks before the release and by letting a
   missed press go outside the page (section 5, statement 5), not by the guard. A click the person makes elsewhere
   in that tab during that second is swallowed too. The guard's listeners are put into each document when it
   commits, which is early but not guaranteed first. Registering them ahead of every script of a page needs the
   protocol's page events (`Page.enable`), which are not switched on in 0.9.45: how a page's dialogs behave with
   them on is not proven. So a page that itself listens on its window in the capture phase, from a
   script that ran first, can still see a stray release or click. That residual is accepted: it is a page acting on
   its own events on purpose, not the label or the cart button of a benign page, which listen on themselves or on
   the document and are covered. Registering the listeners first is a follow-up after 0.9.45 (weighed against
   capturing the pointer on the target), to be run against a test page with `alert`, `confirm`, `prompt` and a
   leave prompt. No guest preload is used for this (ADR 0140). The desktop never answers a page's dialog: guests keep dialogs off, so a `confirm()` is
   never a yes. When the tab is already on another page before the click could be seen, the tool says "navigated";
   it counts as done only if the page first went to the address the pressed link had when the press was armed,
   and is "sent, not confirmed" for a redirect on a timer, a swapped address or a button.
4. **Never.** Typing into a password, one-time-code or payment-card field, and answering a passkey,
   device or permission prompt. The tool says "ask the person to take over". Downloads, popups and external
   protocols stay refused as in ADR 0140.
5. **Non-public targets stay refused.** Every agent navigation passes the same `browserUrl` check and CONNECT broker
   as the person's: no local names, literal addresses, other schemes, credentials, or the product origin.
6. **No way to the hz session.** Commands name tab ids only. The desktop acts only on guests of its tab table, never
   the product window or another webContents. The debugger is attached to guests only, with a fixed allowlist of
   protocol methods: no cookie, storage, network-body, target or browser domain. A screenshot is of the guest view.
7. **Scheduled and unattended turns.** A Heinzel's scheduled turn uses the browser like any other turn. It never
   commits without the person: a commit is proposed and waits for the person's yes, as every proposed action of an
   unattended turn already does; the turn goes on or ends without it.
8. **Per-agent scope and limits.** The actor is the Heinzel's session, the MCP token's actor (ADR 0142), or
   `local` for stdio. Over HTTP the input tools need the `write` scope. Per agent: 60 calls and 10 navigations a
   minute, 4 tabs. Beyond that the call is refused with the wait.
9. **Audit.** Every call logs `browser.call` with actor, tool, tab, host, gate outcome (`ran`, `proposed`,
   `refused`, `paused`, `unconfirmed`) and duration. Typed text and page content are never logged, only their length. Phase 2 shows
   the last actions of an agent on its tab.
10. **Without the desktop app** (the PWA, a browser tab, a headless server) there is no built-in browser. The tools
   stay listed; `ui_browser_tabs` returns `available: false` and every other tool fails with "The built-in browser
   needs the desktop app, open on this computer." A person chatting from the phone can let a Heinzel use the
   browser of the running desktop: commits are confirmed in the chat.

#### 4. Heinzels call tools directly; hz's MCP server is for outside agents

**Inside hz.**

1. A Heinzel's turn gets its tools in-process, as a toolset of the turn's Pydantic AI agent (ADR 0159): hz's own
   operations (exactly the set the MCP head exposes to a chat), the browser tools among them, and the tools of every
   found MCP server that is switched on (ADR 0146). No hz MCP server is started for a Heinzel, and none has to be
   on.
2. **One tool policy.** The rule the chat MCP head enforces today moves into one kernel function that both paths
   call: a read-only operation runs and returns JSON without email addresses, cut at the result limit; any other
   is proposed through `ActionGate`; a browser input runs unless it is a commit (section 3). The direct toolset
   and the MCP head are two callers of that function, so approval and the commit gate cannot differ between them.
3. **Found MCP servers.** hz is the MCP client. A kernel service starts a switched-on server on first use and stops
   it when idle, re-reads how to start it from the source file each time and keeps no secret (ADR 0146's
   guardrails). Its tools appear to the Heinzel as `mcp__<server>__<tool>`. A tool the server marks read-only runs;
   every other tool, and every tool of unknown kind, is proposed. Its results are untrusted data, like a page.
   Stdio servers first, then HTTP servers with fixed headers. A server that needs an OAuth sign-in held by another
   app is listed as "not usable from hz yet".
4. **Where a turn still runs through an agent CLI** (the Claude Code runner), that CLI is a separate program and
   takes tools only over MCP. It keeps the private stdio pipe hz starts for the turn, now a thin adapter over the
   same toolset and policy, so it offers the found servers' tools as well. That pipe is not the server below: it
   has no port, no token and no switch.

**For outside agents.**

5. MCP over HTTP is served at `/mcp` on hz's web server, the one that serves the UI and the API (port 8737), so
   whatever shares the web app (Tailscale Serve, [ADR 0084](0084-explicit-https-origins-for-private-web-sharing.md))
   shares it too. No second port and no second process; port 8765 goes away.
6. `/mcp` takes only a bearer token of ADR 0142. The web app's session never authenticates it, a token never opens
   the UI or the API, and the path is outside the web app's page fallback. Off, it answers 404.
7. **One setting, two controls.** Settings → MCP has the switch "Let outside agents use hz (MCP)", the status and
   the URL. `hz system serve mcp-start`, `mcp-stop` and `mcp-status` write and show the same setting, named like
   their siblings `mcp-check` and `mcp-install-project`. Each shows what the other did; the web server follows the
   setting without a restart, and it survives restarts. Off by default.
8. **Tokens** in Settings and on the CLI (`hz system serve mcp-token create`, `list`, `revoke`): a name and the
   scope `read` or `write`. A new token is shown once, with a copy button; hz keeps only its hash. With no token
   the endpoint refuses every request, and the page says "Create a token first". A copy button gives the ready
   line, such as `claude mcp add --transport http hz <URL>/mcp --header "Authorization: Bearer <token>"`.
9. The token operations are also on the web head for the signed-in person (amends ADR 0142's "CLI only"). They stay
   private and are never an MCP tool, so no agent can make or widen a token.
10. `hz system serve mcp` over stdio stays as it is, for an agent on the same computer.
11. The desktop app and the PWA both show Settings → MCP.

#### 5. Implementation path

1. **Bridge.** The Electron main process opens one WebSocket to the daemon's web head and authenticates with its
   own bridge token, not the session token. The main process makes that token and hands it only to the service it
   starts, in `HEINZEL_UI_BROWSER_BRIDGE`; it is in no page, URL, file or API answer. The session token is refused,
   and a service the desktop did not start refuses every bridge socket. The daemon sends commands, the desktop
   answers; both sides check exact keys, as `hz:browser` does today. A kernel service `BrowserBridge` is what the operations call; without a connected
   desktop it raises the "needs the desktop app" error. The web page is not in this path and sees no page content.
2. **Who decides what.** The daemon owns actor, the commit gate, limits and the audit log. The desktop owns
   the tab table and tab ownership, the URL policy, the protocol allowlist and the never-list of safety statement 4.
3. **Guests.** `mini-browser.js` keeps `Map<window, Map<tabId, guest>>`; a guest is created exactly as today. The
   `hz:browser` IPC gains `tab` and the actions `tabs`, `new-tab`, `select-tab`, `close-tab`, `take-over`,
   `hand-back` and `stop-agent`.
4. **Debugger protocol, not injected scripts.** `webContents.debugger` is attached to an agent tab while it has an
   owner. Refs come from `Accessibility.getFullAXTree`: `ref_N` maps to a backend node id per tab and per snapshot,
   and a new main document invalidates all refs. Input is `DOM.scrollIntoViewIfNeeded`, `DOM.getBoxModel`, then
   `Input.dispatchMouseEvent`, `Input.insertText` and `Input.dispatchKeyEvent`: trusted events, nothing the page can
   intercept beforehand. Page text and the phase-2 `find` use fixed hz scripts in an isolated world, never agent text.
5. **Two-step input.** For an input the desktop first returns what the target is (role, name, tag, type,
   autocomplete, form facts, host). The daemon runs the gate. The desktop then acts only if the target still has the
   same description; otherwise "the page changed, read it again". A confirmed commit is checked the same way.
   A click is checked once more between press and release: the pointer goes to the target first and is not moved
   again, the target is described again, a recorder in the isolated world says what the press really hit, and one
   last call, with nothing sent after it, checks that the element under the pointer is still the target. A press
   that fails any of this is let go at (-1,-1), outside the page, so the release is over no element of a frame and
   no widget in a frame is clicked. After a release at the target the desktop asks whether the click reached the
   described control and says done only then. What is under the pointer is always asked of the page itself
   (`elementFromPoint` in the isolated world), in the coordinates the mouse events use; the protocol's own
   `DOM.getNodeForLocation` goes by other coordinates once a page is scrolled and is not on the list. A tab loads an
   empty page first, so the debugger is attached before its first real page and that page gets the listeners as it
   commits. `Page.enable`, `Page.addScriptToEvaluateOnNewDocument` and `Page.handleJavaScriptDialog` are not on the
   protocol list.
6. **Hidden agent tabs have a size.** Only the selected tab is shown (ADR 0140 stays as it is), and a hidden view
   has no size: its page sees a window of 0 by 0, so nothing can be laid out or hit-tested and every click would
   be refused. An agent tab that is not shown therefore gets a viewport from the protocol,
   `Emulation.setDeviceMetricsOverride`, with exactly the width and height its view has when shown (the panel's,
   or 1280 by 800 before the panel was ever laid out), `deviceScaleFactor` 0 and `mobile` false, and no screen,
   position, scale or viewport parameter. It stays while the tab is hidden, so the page keeps its layout and scroll
   position between calls, and `Emulation.clearDeviceMetricsOverride` removes it the moment the tab is shown,
   taken over or handed back; it ends with the debugger session when the tab closes. It is never set on a tab the
   person is looking at, and never on the person's own tabs. Nothing appears on screen. It is set only after the
   tab's first page has loaded: a guest that never loaded anything does not answer the protocol, so no protocol
   call goes to it, and the first call after the debugger attaches has a time limit. If a hidden tab cannot be
   given its viewport, that is never silent: the tab's state says `sized: false`, `scroll`, `describe` and `click`
   on it are refused with the reason, and the desktop logs it. `Page.captureScreenshot`
   with a clip would not do: it only sizes one picture, while reading, hit-testing and input need a page that is
   laid out.
7. **Screenshots.** `capturePage` on the guest with `stayHidden`, so an unselected tab can be captured; if a hidden
   view paints blank on a platform, `Page.captureScreenshot`. The desktop test decides, per platform.

#### 6. Tests

- **Unit (Python):** exposure, audience and `read_only` of every tool, in a Heinzel's everyday tool set and on the
  MCP head; a scheduled turn's commit waits; the gate table (commit words in English and German,
  submit, Enter, payment page, unresolved coordinates);
  limits; the audit event without text; every tool against a fake bridge, and the "needs the desktop app" error.
- **Direct tools (unit):** a Heinzel turn with the MCP endpoint off lists and calls hz operations and a fake found
  server's tools, and starts no `hz system serve mcp` process; one table of calls gives the same outcome (ran,
  proposed, refused) through the direct toolset and through the MCP head; a found server's unmarked tool is
  proposed; a switched-off server offers nothing; no secret of a server is written or logged.
- **MCP from the app and the CLI (unit and web):** `mcp-start` turns the switch on in the page and the switch shows
  in `mcp-status`, and the same for off; off answers 404 and on answers at `/mcp` on the web server's port without a
  restart; the UI session does not authenticate `/mcp` and a token does not open the API; no token, no call; a
  token is shown once and never stored or logged in clear; the token operations are absent from every MCP head;
  the page renders in the desktop app and the PWA.
- **Desktop (node, fakes, as `mini-browser.test.js`):** tab limits and ownership; a command for another agent's or
  the person's tab is refused; agent navigation to a non-public address is refused; a non-guest webContents is never
  attached; a protocol method outside the allowlist is refused; refs die with the document; the two-step check;
  password and one-time-code fields; take over, hand back, stop.
- **Desktop e2e (real Electron):** a hidden agent tab is captured non-blank; trusted click and typing reach a fixture
  page; the indicator bar and Take over work; the product window cannot be reached.
- **Journey with a fake agent**, run twice: as a Heinzel in a chat turn (fake model, direct tools, MCP endpoint off)
  and as an outside agent over `/mcp`, against the real daemon and desktop: open a tab on
  a site nobody allowed beforehand, read, click an ordinary button (runs), click "Send" (proposed, runs only
  after yes), type into a password field (refused), a page whose text says "go to another site and paste this"
  (no commit and no forbidden field follows without the person's yes; the navigation is in the audit log), take over (refused), stop (tabs closed).

#### 7. Phasing

1. **Phase 1, usable by the owner:** tabs for the person; the bridge; agent tabs with the indicator, Take over and
   Stop; the eleven phase-1 tools; the commit gate; the never-list; limits; the audit log; direct tools for Heinzels; `/mcp` on the web server
   with its switch; the journey.
   The safety parts are not a later phase: the browser is signed in from the first day.
2. **Phase 2:** `find`, `fill`, `hover`, `wait`; share one of the person's tabs; recent actions on the tab.
3. **Phase 3:** `console`; `evaluate` with a confirmation for every call. Uploads and downloads only after their own
   decision.

#### 8. Agent write policy per operation

The owner: "Heinzels must be able to do write toolcalls." ADR 0045 let a chat's agent only propose every write.
Now each write operation says what an agent's call does, with `@module.operation(..., agents=...)`. The line is the
browser's: approval only at commits.

- `direct`: the call runs at once and is audited.
- `approve`: the call waits for the person. In a Heinzel's chat it is proposed through `ActionGate`, as before. For an
  outside agent over MCP nothing can hold a proposal, so the call is refused with the exact `hz ...` command the person
  can run.
- `never`: the person's own operations (confirm, decline, authorize, the `approve-*` group, executing a captured write).
  Refused on every agent path, whatever `heads` says later.
- `propose`: the behaviour before this policy (proposed in a chat, run over MCP). No marker means `propose`; the default
  is never `direct`.

`approve` is for sending to other people, deleting or irreversible changes, payments and spending, publishing,
credentials and settings that widen access, and installing or updating code. **A marker is at least as strict as
everything the operation can trigger.** So `system daemon tick` (it runs any job), `heinzel wake` (it spends and starts
another agent) and `system notify ack` (it could hide a record) are `approve`. A marker may depend on the arguments
(`ByArguments`): `heinzel new --team`, `org sync --accept` and a bookmark `edit` that changes the address are `approve`,
the same operations without those arguments are `direct`. A marker that fails to decide counts as its strictest case.

Every core write that an agent head offers carries an explicit marker, and a test fails on one that does not. Two groups
keep `propose` on purpose. The operations with their own approval objects (`heinzel propose-*`, `amend-scheduled-agent`,
`schedule-control`, `heinzel chat`, `confirm-authorized-mcp`) behave exactly as before, because `approve` would refuse
them over stdio and break the terminal challenge of schedule approval. Private writes stay proposed for a Heinzel,
because a direct write never holds `private:write`.

The marker is the operation's own word, so the kernel bounds it. A plugin's write that requires `mail:write`,
`chat:write` or `calendar:write` is at most `approve`, whatever it declares; `tracker:write` may be `direct`, so a
Heinzel can create and follow issues. Whether a module is built in comes from discovery (`Origin.builtin`), never from
the module. A plugin written before this policy has no markers, so its writes stay proposed in a chat until it declares
them. Over stdio and with a write token on `/mcp`, `propose` runs as it did before; that part is today's behaviour, not
a new protection.

**The caller.** An operation may ask for `Caller` by type: who calls (person, heinzel, local-agent, token, daemon), its
id, the chat session of a Heinzel's turn, and whether the turn is attended. The head that authenticated the call sets
it; the kernel injects it and refuses any argument of that type, so no model, request or head can pass one. A call path
that says nothing has no caller, and an operation that asks for one is refused there. An outside agent over stdio is a
`local-agent`, never the person. An operation that takes a session or a Heinzel as plain data compares it with the
caller for a Heinzel's call.

**Identity of a direct write.** A Heinzel's direct write runs with a non-human authentication that may read private data
and never write it, after the request thread's own identity is cleared. The authentications a person holds (`cli`,
`api-bearer`, `web-session`, `web-device`) are pinned in a test, and no agent authentication may join them: the services
that issue approvals trust exactly these.

**Unattended turns and budgets.** A turn the person started and watches is attended. A scheduled turn is not, and
neither is a turn over the chat's stdio pipe, which cannot say who watches; both take the stricter side. Every agent
caller has a write budget per minute and per day (30 and 500 attended, 10 and 100 unattended), counted in the process
that runs the turn. Past it a write is refused, not queued.

**Browser inputs are outside the write budget.** A browser tool that changes a page (open, navigate, click, type, key,
close) is marked `direct`, passes its marker check and leaves its `agent.write` line on every path, but only the
browser's own per-actor limits apply: 60 calls and 10 navigations a minute, with no daily cap. Ten writes a minute would
stop a scheduled turn after a few clicks. A browser commit is never a direct write: it still waits for the person.

**Audit.** Every write an agent asks for leaves one `agent.write` line on every path, stdio included: the caller,
whether it was attended, module and operation, the id of what changed, a digest of the arguments, the policy and the
outcome (ran, failed, proposed, refused, over budget). Arguments and error messages are never written, only the error's
type. A failed write's message reaches the agent without email addresses, like a read's.

**Known limit.** An outside agent that also has a shell is not bound by `approve`: the refusal names the `hz` command,
and the CLI is a human head. That is today's limit and stays one until `approve` operations ask on a terminal when the
CLI is not interactive. A Heinzel's turn has no shell.

**A plugin's direct writes need the owner's yes.** A plugin's markers are known only once its code is installed, so the
preview of `hz system plugin add` cannot show them. Instead a plugin's `direct` write is proposed until the owner
allowed that plugin: `hz system write plugins` lists the operations that ask, `hz system write allow <plugin>` is the
yes, and `hz system doctor` says when a plugin waits. The yes names the operations of the installed version, so an
update that turns another operation `direct` waits again. It lifts no ceiling: a mail, chat or calendar write still
asks.

**The owner's record.** Besides the audit line, hz keeps one entry per write an agent asked for in the owner's state
directory, private and bounded, without arguments. `hz system write list` shows it per agent, and `--unattended` shows
what ran with nobody watching; the doctor carries that count as the digest. Settings, MCP shows under each token what
its agent changed. The record and the yes are the owner's (`cli` and `web` only, `allow` is `never`), so no agent can
read, thin or widen them.

**Accepted for phase 1.** Three limits are known and accepted. First, a chain of direct writes can do what one `approve`
would stop: a Heinzel can rename a bookmark, add another with a new address and move it into place, and entries do not
yet carry who made them. Second, the ceiling sees only what an operation declares in `requires`; a plugin that sends
mail without declaring `mail:write` is outside it, which the conformance kit and review must catch. Third, because the
ceiling also applies to an unmarked write, an outside agent is now refused a plugin's unmarked mail, chat or calendar
write over stdio and `/mcp`, with the owner's command, where it ran before. That one is stricter on purpose.

**The yes and the record, in detail.** The owner's yes names the operation with the marker it had, so a version that
loosens the marker waits again; it does not see a change inside the operation's code. The argument digest is keyed with
a salt of the installation, kept private beside the record, so a guessed argument cannot be confirmed from a line. The
recorded target is an id only: free text and email addresses are never kept. Unknown callers count as unattended: a
token and an agent over stdio have the smaller budget and appear in the digest, and a turn is attended only when a human head made the call that runs it (the web chat, the person's own CLI). The chat's stdio pipe stays unattended in every case.

**No "ran" entry in the chat.** The chat's action list is the list of approvals: its states, its limits and the review
screens all read an entry as something the person decided. A write that ran by itself would read there as confirmed. So
phase 1 does not add it; the owner sees every direct write in `hz system write list`, and the Heinzel's answer says
what it did. A separate "done by itself" list in the chat is a later head.

**Not in this decision.** Private operations on `/mcp` for owner tokens are a separate later head (module scope, short
expiry, loopback by default, no presence mark). A notification for the digest is not built; the doctor line and the list
are. Bookmarks follow the policy on the CLI and on both MCP transports: `add`, `folder`, `move`, `restore` and a rename
are `direct`; `remove` and an address change are `approve`; the favicon cache is `never`. At `/mcp` a read token lists,
a write token writes.

### Consequences

- Good: a Heinzel or a coding agent can do real work on the web as the person, in sight, with the person's hand on
  the stop.
- Good: a Heinzel's turn needs no child server, and outside access is one path on the port people already share.
- Bad: a found server's tool marked read-only by its author runs without a question; the mark is the server's word.
- Good: ADR 0140's containment is unchanged, and the agent path adds no way around it.
- Bad: the commit gate is a word list and form facts. A site can name its send button anything. The
  visible tab, the never-list and the audit log are what the person relies on, and the documentation must say so.
- Bad: with no grant per site, a page that steers an agent can make it read another site where the person is signed
  in, or open an address that carries what it read. Nothing is committed without the person, but what was read can
  leave. The maintainer accepted this for use without friction; the limits, the visible tab and the log remain.
- Bad: sites with their own automation rules may object to an agent on the person's account; that stays the
  person's choice per site.
- Bad: the debugger can be noticed by a site, and some sites treat it as a bot signal.

### Confirmation

The tests of section 6. The journey is the release gate for phase 1. Before the first release an independent review
checks safety statements 2 to 7 against the code, as ADR 0140 asks for its own negative probes.

## More Information

#### Owner decisions

0. **Direct tools, and MCP for outside agents.** Heinzels call tools directly, found MCP servers included; hz's MCP
   server is only for outside agents, on the web server, "startable via ui and cli": section 4.
1. **Who.** "I mean Heinzel - and expose as well via mcp": Heinzels first, the MCP head as well.
2. **Free use.** "Allow agents to freely use the browser.": no grant per site and no dialog.
3. **Whose sign-ins.** Agent tabs use the person's website session (`persist:hz-browser`).
4. **Confirmation.** Only at commits, through the existing approval path.
5. **Legal.** One sentence in the browser panel's help and the docs that some sites forbid automated use of an
   account; no blocklist.

Tool shapes follow Claude in Chrome and Claude's built-in browser tools (tabs, navigate, read with refs, find,
click, type, key, scroll, screenshot, page text, form input) and Playwright MCP (snapshot refs, tab tools).
[Electron debugger](https://www.electronjs.org/docs/latest/api/debugger) attaches the protocol to one webContents.
