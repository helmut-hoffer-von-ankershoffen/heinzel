---
status: accepted
date: 2026-10-24
decision-makers: maintainer
amends: 0028
related: 0015, 0030, 0032, 0035, 0039, 0048
---

# 0056: Connectors show their sign-in, test live on demand, and bind themselves when they are alone

## Context and Problem Statement

Settings → Connectors in the web app ([ADR 0039](0039-a-web-head-a-react-app-served-by-the-api-head.md)) shows a card
per plugin that brings adapters, saying only "Not in use yet", and under them a free-text field per capability
binding. The maintainer asked to "see if connectors are connected (authenticated, live test, list tools if feasible)
and if not trigger from here", and rejected the text fields: "will not enter text here. think hard about usability".
Doctor is offline by design ([ADR 0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md),
statement 6), so it can't say whether a sign-in still works, and a capability served by exactly one installed
adapter still has to be bound by hand ([ADR 0028](0028-integrations-are-capability-adapters.md): "a sole installed
adapter is not bound automatically"). How does a person see and fix the state of a connector without typing?

## Decision Drivers

- Perfect onboarding (INTENT.md): installing a plugin and signing in to its CLI should be all it takes.
- The vendor's CLI owns sign-in and secrets (ADR 0035); hz never handles a password or token.
- Doctor stays offline; anything that reaches a service runs only when the person asks.
- Build on the lets that exist (`module.health`, `module.setup`, the capability registry) rather than a parallel
  mechanism.

## Considered Options

- **Sign-in state on `Check`, live health providers, automatic binding of a sole adapter** (chosen)
- **A new let kind for connectors** (`module.connector(...)` with a status and a probe): a second way to say what
  health checks already say, and every plugin would declare the same facts twice.
- **Parse the checks' text for "logged in"**: brittle, and every message would become an API.
- **Keep binding by hand and only add a picker**: the picker would offer one choice for most capabilities, which is
  no choice.

## Decision Outcome

Chosen option: **sign-in state on `Check`, live health providers, and a sole installed adapter serving its capability
automatically**, because the plugins' own health checks already know what a connector's state is, and a choice with
one option is not a choice.

- **`Check.login`** (a `Login`: `signed_in` true, false or unknown, `account`, `site`, `command`) says what a check
  found about the sign-in of the tool an adapter goes through and the vendor's command that makes one. Account and
  site are what the tool names (a login, the person's own address, a host), one cleaned line, never a secret; the
  command is run by the person in a terminal, where the vendor's CLI asks for any secret itself.
- **Live health providers**: `@module.health(live=True)` marks checks that reach the network: one cheap read-only
  call with a short timeout of the plugin's own (the CLI's "who am I", or a one-item read through the capability).
  Doctor leaves them out; they run only on demand, through `hz system connector test <name>`.
- **`system.connector`**: `hz system connector list` reports, per module that provides adapters, the capabilities it
  serves and how each is bound, its offline checks and sign-in, its state (`connected`, `not-signed-in`, `not-bound`,
  `unknown`), the methods it implements with their modes (from the capability registry), and the commands that sign
  it in and bind it; plus, per capability, the adapters that could serve it. `hz system connector test <name>` runs
  the live providers and reports their checks and latency. Both are on the CLI, MCP and web heads, not the REST head,
  which has no authentication ([ADR 0047](0047-plugins-are-managed-from-the-web-app-after-a-confirmation.md)).
- **Automatic binding** (amends ADR 0028; maintainer's decision, 2026-10-24): while `[capabilities]` doesn't set a
  capability, the only installed adapter that provides it serves it, shown as automatic. Several installed: the core's
  default serves if the capability has one, else nothing until the person picks. `mail` is never automatic
  (`Capability.automatic`): reading a mailbox stays the person's explicit choice. `none` is a binding that serves
  nothing, so a person can turn an automatic choice off; no adapter may take that name. A project `hz.toml` still
  can't bind, and installing a plugin stays the person's own act, so where data goes is still their choice; `hz system
  info` shows it as before.
- **Settings with known values carry them in their JSON Schema**: an `enum` (a `Literal`), or an `x-options` source
  the web head resolves (`adapters` of a capability, with `none`), so the web app's settings editor offers a choice
  and never a text field for them.
- **The web app** (planned, in a pull request of its own) shows a status pill per connector, a Test button, the methods it implements, and Connect, which
  opens the in-app terminal ([ADR 0048](0048-a-terminal-in-the-web-head.md)) with the sign-in command; the bindings are
  capability-first rows with a picker of the adapters that can serve each.

### Consequences

- Good: installing GitHub and running `gh auth login` is enough for `code-host`; nobody types an adapter name.
- Good: the state comes from the plugins' own checks; a new plugin gets a card by setting `login` on its sign-in check.
- Bad: installing a second provider of a capability that was served automatically unbinds it until the person picks;
  doctor and the web app say so.
- Bad: a live test reaches the vendor; it runs only when asked and reports the account, which is the person's own.

### Confirmation

`tests/unit/kernel/bindings_test.py` (automatic, default, `none`, configured, mail never automatic),
`tests/unit/kernel/module_test.py` (live health lets, the reserved name), `tests/unit/modules/connector_service_test.py`
(state mapping with fake checks) and `connector_render_test.py`, `tests/unit/modules/org_sync_test.py` (`none` is a
choice sync doesn't override), `tests/integration/connector_test.py` and the plugins'
`tests/integration/connector_test.py` (fake CLIs on `PATH`; doctor never runs a live test). Still to come with the web
app: its cards and pickers, and a test that a setting with known values never renders as a text field.

## More Information

- The maintainer's requests of 2026-10-24 quoted above. Automatic binding reverses ADR 0028's "a sole installed
  adapter is not bound automatically"; the rest of ADR 0028 stands.
