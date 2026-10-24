---
status: proposed
date: 2026-10-24
decision-makers: maintainer
related: 0002, 0003, 0018, 0026, 0027, 0028, 0039, 0041, 0044, 0045, 0047
---

# 0056: Modules contribute status items, header actions and Today widgets as data, viewed from their own operations

## Context and Problem Statement

Three parts of the web app ([ADR 0039](0039-a-web-head-a-react-app-served-by-the-api-head.md)) are hard-coded: the
status bar under the screen (the version, `hz org agenda`'s meeting now or next, and a clock that reads the clock
plugin's `GET /api/clock`), the buttons on the right of the header (refresh, terminal, chat) and the "Today" strip on
Home (cards from notices, the doctor, the daemon's jobs and the profile). The maintainer asked that any module can add
to all three "by implementing some new let". A plugin must not ship JavaScript or HTML: the app runs under a CSP that
allows this origin's own scripts only, and a plugin's code in the page would see the session token. Themes showed how
a plugin contributes to the app without code ([ADR 0044](0044-themes-are-data-a-plugin-ships.md)): typed data, checked
when the plugin is imported, rendered by the app. The questions are what a contribution is, where its values come
from, how one that acts (opens, runs, writes) stays safe, and which head serves them.

## Decision Drivers

- No plugin code in the browser; the app renders a closed set of shapes itself.
- Heads are views on operations ([ADR 0003](0003-heads-are-views-on-operations.md)): what the web shows, the CLI and
  MCP can show too, and capability gating ([ADR 0028](0028-integrations-are-capability-adapters.md)), `optional=True`
  and an operation's `heads=` apply unchanged.
- Private data (meetings, profiles) stays behind the web head's token, never on the unauthenticated REST head.
- One failing contribution never breaks the bar, the header or the strip.
- Nothing that writes runs silently; what the person confirms is built from what runs, never from a plugin's label
  ([ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md), [ADR 0047](0047-plugins-are-managed-from-the-web-app-after-a-confirmation.md)).

## Considered Options

- **Three let kinds, each a pure view over one of the module's own read-only operations, consumed by `system.ui`**
  (chosen)
- **Plugin JavaScript or HTML** (a web component, an iframe): the most flexible; code in the page sees the token,
  needs a CSP exception and is a supply-chain path into the person's browser. Refused like free-form CSS in ADR 0044.
- **Provider functions the kernel injects**, like `module.health`: the kernel registers only operations, jobs,
  services, adapters, info, health and setup functions as requesters of capabilities, so a provider that asks for
  `Calendar` would be refused or act without the operation's `requires` and `optional`; it would also bypass `heads=`,
  so a value the CLI may not show could still reach the web. A view over an operation inherits all of that.
- **A hint on `OperationLet`, like `render=`**: one operation feeds several contributions (the clock's status item,
  badge and widget), and a header action may need no data at all.
- **One let kind with a `slot` field**: the three differ in shape (a status item and a widget are viewed results, an
  action is static data with an optional badge), so one model would be a union checked by hand; a future slot would
  widen an enum silently instead of getting its own record.
- **Contributions as one operation per item** (`hz clock status`): a command per item clutters every head with
  fragments that only make sense on one screen.

## Decision Outcome

Chosen option: **three let kinds, `StatusLet`, `ActionLet` and `WidgetLet`, each a pure view over one of the module's
own read-only operations, consumed by a new `system.ui` submodule**, because every value shown is then an ordinary
operation's result, gated, validated and limited to its heads like any other, and the app only ever renders typed
data.

- **Declaration** (`heinzel.kernel._contributions`, data only): `@module.status(source=..., order=, refresh=)` and
  `@module.widget(source=..., order=, refresh=)` decorate a view function `view(result) -> StatusItem | None` or
  `-> Widget | None`; `module.action(name, label=, icon=, effect=, order=, source=None, badge=None, refresh=)`
  declares a header action whose optional `badge(result) -> int | str | None` views `source`. `source` names an
  operation of the same module that takes no required argument; the view takes nothing injected and returns None to
  show nothing. Names are lowercase and dashed; a module offers at most 3 status items, 2 actions and 4 widgets.
  `order` (0 to 1000, default 500) sorts within a slot, ties by id; `refresh` is 15 seconds to a day for status items
  and badges (default 60 s), 60 seconds to a day for widgets (default 5 min).
- **Values.** Every text is cleaned (`clean_text`, newlines become spaces) and length-limited: a status item's `text`
  80, a detail line, label or title 80, a body 280. `Tone` is `neutral`, `ok`, `warn` or `bad`; `Icon` is a closed
  list of icon names the app maps (a Literal, so the generated schema makes the app's map exhaustive). A link is
  either `https://` with a valid host, no user information and no whitespace, or an in-app path:
  `/` and dash-separated lowercase segments, with at most one `?name=value` of the same characters; `//`, `\`, `..`
  and control characters never pass. A badge is an integer (0 shows none) or at most 4 characters.
- **Shapes.** `StatusItem {text, detail: [{text, tone}] (at most 8), tone, icon?, live?, effect?, actions (at most
  4)}`, the actions shown with the detail, where `live` is
  rendered by the app in the person's locale and ticks: `{kind: "clock", zone}` (the time now in an IANA zone),
  `{kind: "time", at}` (an aware moment as a local time) or `{kind: "until", at}` (relative: "in 12 min"). `Widget`
  is one of `card {title, body, tone, actions (at most 3)}`, `list {title, items: [{text, detail?, tone, effect?}]
  (at most 8)}`, `metric {label, value, delta?, tone}` and `progress {label, done, total, tone}` (`0 <= done <=
  total`, `total >= 1`). The app adds "Ask about it" (it hands the widget's text to the chat, framed as what module
  `<qualname>` shows, never as the person's words) and dismiss to every widget.
- **Slots.** Each contribution names where it may show and whether it does by default: a status item in `footer`
  (the status bar) and `sidebar` (the bottom of the navigation, above the account line), `{"footer": true}` unless
  it says otherwise; an action in `header`; a widget in `today`. The person turns each slot on or off: `hz system ui
  show|hide <id> [--slot <slot>]` writes `[system.ui.contributions."<id>"] <slot> = true|false` to the user file
  (`HZ_SYSTEM_UI__CONTRIBUTIONS`), user scope like the theme (`ignored_in_project`); a slot not named keeps its default.
  Every entry carries `slots`, the effective choice, and `hz system ui list` lists them without running anything, so
  the app's Settings shows "Show in footer", "Show in sidebar", … on the page of every module that contributes, with
  no code per plugin.
- **Effects**, exactly one per action, at most one per status item or list entry: `navigate {path}` (an in-app path),
  `open {url}` (an https link, opened with `noopener noreferrer`), `operation {operation, arguments}` (an operation
  of the same module with scalar arguments) and `command {command}` (`hz` followed by plain words or single-quoted
  arguments, the grammar the terminal panel runs as is).
- **Consumer.** `system.ui` (ADR 0018: about Heinzelmännchen itself) lists them: `hz system ui status`, `hz system ui
  actions` and `hz system ui widgets`, each with `--item <id>` for one, read-only and on the CLI, MCP and web heads
  only (`heads={"cli", "mcp", "web"}`), never the REST head. Each entry is `{id: "<qualname>:<name>", module,
  plugin, order, refresh (seconds), <value>, problem}`. `system.ui` runs the source through the kernel like a head
  does, so validation, injection, capability handles and `optional=True` behave as for a call from the CLI, then
  calls the view; each entry is isolated: a `HeinzelError` becomes `problem` with its cleaned message, any other
  exception or a view returning the wrong type becomes `"<id> failed: <ExceptionType>"`, never a traceback or the
  exception's text. A declaration that can't work (no such source, a source that writes, takes a required argument
  or leaves out the web head, an effect naming an operation that doesn't exist) is an entry with `problem` and a
  degraded check in `hz system doctor`, so its author sees it.
- **Server-built calls.** For an `operation` effect the list adds `call {method, path, query, command, summary,
  read_only}`, built from the operation's path, its docstring's first line and the arguments validated against its
  signature; the app runs a read-only one directly and shows success or the error, and asks before a write, showing
  `command` and `summary`, never the contributor's label. For a `command` effect the list adds `runs`: true only when
  the command names a read-only operation the CLI exposes; the app presses Enter only when `runs` and its own
  `runsAsIs` both hold, otherwise the person reads it and presses Enter. ActionGate stays the chat's: it holds an
  agent's proposals, while here the person clicked a fixed, checked button.
- **The web app** fetches each list once for the first paint, then each entry by `--item` at its own `refresh`, so a
  slow or failing one neither delays nor breaks the others. Built-in parts (version, refresh, terminal, chat,
  notice cards) stay in the app. The status bar keeps drawing the clock's zones panel and the agenda itself for now,
  richer than the shapes (a pinned panel, translated text); it follows those two items' footer choice, renders the
  generic item everywhere else (the sidebar), and leaves both ids out of its generic row.
- **First users.** The clock plugin's status item shows the live time in the first of `clock.zones` (the person's
  primary zone) with the other zones and whether they are at work as detail lines; its header action opens `hz clock`
  in the terminal with the number of zones at work as the badge; its widget is the metric "At work now 2/3". `org`
  shows the meeting now or next as a status item, with the join link as its effect. `system.daemon` shows whether the
  daemon runs (ok, with its next tick), is paused (warn), is not installed, or fails, isn't loaded or stopped ticking
  (bad), with its jobs, last tick and failing jobs as detail, and buttons: Install and Uninstall open the terminal
  with the command for the person to run (installing stays a CLI act, ADR 0020), Pause and Resume are confirmed
  calls, Logs runs `hz system daemon logs`; other platforms say unsupported. The clock shows in the footer by default
  and in the sidebar once the person turns it on. `hz clock` and `hz org agenda` stay as they are.
- **Public API**, additive ([ADR 0026](0026-plugin-api-versioned-with-semver.md)): the shapes, effects, `Tone`,
  `Icon`, the three lets and decorators in `heinzel`; `operation_path`, the path every head gives an operation, in
  `heinzel.kernel`.

### Consequences

- Good: a plugin adds to the footer, the header and Home with a few lines of Python and no code in the browser; the
  CLI and agents see the same items (`hz system ui status`).
- Good: a contribution can't reach data its operation couldn't, can't run a write without the person reading the
  exact call, and can't link outside https or an in-app path.
- Bad: contributed text is English, outside the app's catalogue (`web/src/i18n.ts`); only `live` values are
  localized. The org agenda's footer text loses its translation keys.
- Bad: every refresh runs the source operation (the agenda already read the calendar every minute).
- Bad: the icon list and the shapes are a contract between plugins and the app: adding is additive, removing or
  renaming one is breaking.

### Confirmation

- `tests/unit/kernel/contributions_test.py`: links (protocol-relative, backslashes, `..`, user information, http),
  text cleaning and limits, zones, badges, widget shapes, refresh bounds and the caps per module.
- `tests/unit/modules/system/ui_test.py` and `tests/integration/contributions_test.py`: a failing view becomes a
  problem without the exception's text, a source that writes or leaves out the web head is refused and reported by
  doctor, server-built calls for read and write operations, `runs` only for read-only commands, an optional source
  that degrades, the lists through the CLI and the web head behind its token, and nothing on the REST head.
- `plugins/heinzel-clock/tests` and `tests/integration/org_agenda_test.py`: the first users above.
- `web/src/components/Contributions.test.tsx`: an isolated failing item, a live clock, the confirmation of a write.
- `tests/unit/public_api_test.py`: the new names.

## More Information

- Requested by the maintainer, 2026-10-24: status-bar items, then header buttons, then Today widgets, each "by
  implementing some new let".
- Extension, not decided: a `page` slot (a navigation entry with a screen composed of the same widget shapes),
  translations of contributed text, and the status bar drawing the clock and the agenda generically once the shapes
  carry what their panels show.
