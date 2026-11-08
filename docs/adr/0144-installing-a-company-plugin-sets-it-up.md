---
status: proposed
date: 2026-11-08
implementation: built
decision-makers: maintainer (the goal: install a company plugin and everything is set up), architect (shape)
amends: 0037, 0055
amends-0037: a playbook's recommended connector bindings are written at install; the install's confirmation is the yes
amends-0055: a company's recommended binding is a source of bindings, below the person's and above the sole adapter
related: 0012, 0032, 0040, 0047, 0056, 0136, 0138
---

# 0144: Installing a company plugin sets it up

## Context and Problem Statement

A company plugin (ACME, a layer, ADR 0040) says which connector serves each capability: its playbook's `defaults`
recommend `capabilities.tracker = "atlassian"` and the like (ADR 0037). Nothing applies a recommendation until the
person accepts it, and the only place that asks is the terminal wizard `hz setup <module>`, which no install runs. The
binding resolver (ADR 0055) knows four sources: the person's configured value, the sole installed adapter, the core's
default, nothing. So a person who installs ACME and every connector it names still has tracker, documents and mail
unbound (two trackers are installed, `mail` is never automatic), and System health lists eleven problems whose fixes
are shell commands in a web app. The owner's goal: install ACME, and everything is set up except the sign-ins only the
person can do.

## Decision Drivers

- One action: installing the company plugin is the person's yes to how the company reaches its tools.
- A binding the person set is theirs; nothing a company recommends ever changes it.
- Visible and undoable: the bindings are listed before the install and in its result, and undo removes exactly those
  setup wrote.
- One path for every head (ADR 0047): the web app, MCP, the CLI's `hz system plugin add` and `hz setup <module>` write
  through the same service and the same `ConfigEditor`.
- No shell command in the web app: what is left is data a head turns into its own buttons.

## Considered Options

1. Keep the wizard as the only path; the web app shows the command.
2. Teach the resolver a fifth source, "the company recommends", evaluated at every resolve.
3. Write the recommendations as configuration once, at install, through the same editor as `hz system config set`.

## Decision Outcome

Chosen option 3. Option 1 is today's problem. Option 2 makes a binding depend on which plugins happen to be installed
at each resolve, so the same configuration means different things on two machines, and the person can't see or undo
what serves a capability.

**Recommended plugins come with the install.** `[tool.heinzel.plugin] recommends-plugins` are installed with the plugin
by default (`with_recommended`, true for every head; off, they are offered as before). Each is one step
`recommends:<distribution>`; one that fails is said and doesn't stop the install; one a trusted catalog can't give is
offered.

**The bind step.** After the plugins are installed, the add runs a step `bind` in a fresh `hz` (which loads what was
installed): `hz system plugin bind --plugin <each installed distribution>`. For each `capabilities.*` key the playbooks
of those plugins recommend (the layer stack's precedence applied, ADR 0040), in order:

1. **A binding the person configured is never changed** (any value, `none` included): `kept`.
2. A recommendation is written only for a connector that is installed; otherwise it is `skipped`, and the checklist
   offers to install it.
3. It is written even where the sole installed connector serves already, so a second connector installed later does not
   unbind it.
4. Layers that disagree, or two playbooks that recommend different connectors, bind nothing: a `conflict` the person
   resolves.

Writes go through `ConfigEditor` into the user configuration. Each is remembered (state file
`setup-bindings.json`: the module that recommended it, the operation id, when) so Settings can say "set by ACME" and
`hz system plugin unbind acme` removes exactly those that still hold what setup wrote. `--no-bind` writes
nothing and remembers the playbooks as considered. `hz system plugin repair` considers each installed playbook once
per version of its plugin, so a company plugin installed before this decision is set up by the next repair.

**Precedence of a binding, with this decision.** configured by the person → written at install by a company's
recommendation (it *is* configuration) → the sole installed adapter → the core's default → unbound. The resolver itself
is unchanged.

**Setup without a terminal, and the checklist.** `system.plugin` adds `setup <module>` (the bindings, the playbook's own
settings given as `answers`, then the checklist; a step only a terminal can ask is `needs-cli`) and `checklist
[--module]`: one row per connector the playbook names, with `state` (`not-installed`, `not-connected`,
`needs-attention`, `connected`), one status line of at most 60 characters, one `action` (`install`, `sign-in`, `bind`,
`settings`, `check`) and `owner_only`. Steps are told through the operation-progress contract (ADR 0056) and logged as
structured events (ADR 0138: `plugin.bind.run`, `plugin.bind.binding`, `plugin.add.bind`, `plugin.add.recommends`). `hz
setup <module>` keeps its questions: it first offers the recommended bindings at once through the same `bind`, the
wizard then skips what is set, and it ends with the checklist.

**Checks carry a cause and an action.** A health `Check` gains `cause` (a few words, e.g. `Nobody serves mail`) and
`action` (`CheckAction`: kind, capability, connector, adapter and the command for a terminal), both left out of the JSON
when empty. The core's binding checks say what to do in words and carry the command only in the action; `hz system
doctor` prints it, a web head shows a button.

### Consequences

- Good: installing ACME binds code host, tracker, documents, calendar, mail and meetings; what is left are the person's
  sign-ins and site, which the checklist lists.
- Good: a person's binding always wins, and undo removes only what setup wrote.
- Bad: an install now also writes configuration. It is listed before (the preview's plugins and the playbook's
  bindings) and after (the result's `bindings`), and `--no-bind` keeps the old behaviour.
- Neutral: "nothing in a playbook is applied without a yes" (ADR 0037) stands; the install's confirmation is the yes
  for the bindings, the wizard's questions remain for its other settings, assets and templates.

### Confirmation

`tests/unit/modules/plugin_setup_test.py` holds the four rules, undo, the checklist states and the log events;
`tests/unit/modules/plugin_needs_test.py` the recommended installs and the bind step of an add and a repair;
`tests/integration/playbook_test.py` that `hz setup <module>` writes through the same `bind`.
`scripts/acme_setup_e2e.py --with-bindings` proves it end to end on a throwaway profile with fake vendor CLIs.

## More Information

- Research: the ACME setup journey (private management repository). The web head's checklist card, the
  install dialog's checkbox and System health grouped by cause build on the JSON of this decision.
