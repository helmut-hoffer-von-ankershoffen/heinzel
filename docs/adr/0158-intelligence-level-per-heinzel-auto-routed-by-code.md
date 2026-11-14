---
status: proposed
date: 2026-11-14
implementation: partial
decision-makers: maintainer (decision), architect (shape)
related: 0057, 0115, 0159
---

# 0158: Intelligence level per Heinzel; Auto is routed by code

## Context and Problem Statement

In the new chat every chat is with a Heinzel. People should not have to know model names to get a good answer, and
a model picker by the chat input makes every message a choice about vendors and prices. The owner decided:
models are chosen by level. Per Heinzel the person picks the AI provider, for example a plugin's
`ai-models` option ([ADR 0159](0159-plugins-bring-their-own-ai-models-through-pydantic-ai.md)) or the configured AI
access ([ADR 0057](0057-ai-access-is-a-module-one-choice-like-the-theme.md)), and Auto, Low, Medium or High maps to
that provider's models. An exact model is an advanced choice in the Heinzel's details, never by the chat input.

Until this record, a turn on a plugin's models ran on `[ai.access] plugin_model` and `plugin_effort`, one choice for
every chat; release 0.9.43 adds a model picker by the chat input (ADR 0159, statement 7) as the interim interface.

## Decision Drivers

- No model names in the chat: the chat shows the Heinzel, not its vendor.
- The model never picks its own spend: what runs is decided by code before the turn and recorded with it.
- A provider's model list is incomplete and changes every few weeks; a level must still resolve when the list has no
  hints at all, and must resolve the same way twice for the same list.
- A level never widens authority: it chooses within what the person and their grants already allow.
- Big work merges dark ([ADR 0115](0115-merge-unfinished-work-dark-behind-named-feature-flags.md)).

## Considered Options

1. **A level per Heinzel, mapped by code to the provider's models** (chosen).
2. **A model picker per chat** (0.9.43's interim picker): puts vendor names and prices in front of every message.
3. **Let the model route itself** (a cheap model decides whether to call a bigger one): the model would pick its own
   spend, and the choice could not be predicted or reviewed.
4. **A new required `level` field in `ModelOffer`**: a breaking change of the plugin interface; an optional hint is
   left as a follow-up within interface 1.x.

## Decision Outcome

Chosen option 1. Checkable statements:

**What a Heinzel stores**

1. **The profile holds the intelligence.** A Heinzel's private profile (the `profile.updated` body) has
   `intelligence` with `level` (`auto`, `low`, `medium` or `high`, default `auto`), an optional `provider` (an AI
   access option's name; none means the configured AI access) and an optional exact `model` (an id as ADR 0159
   allows). The default is left out of the stored body, so a profile saved before this record reads unchanged and
   one saved with the default is byte-identical to before. It is sticky: it changes only when the owner sets it.
2. **Reading and setting it are owner-private operations.** `heinzel level` reads the Heinzel's intelligence and
   what it resolves to now; `heinzel set-level` sets level, provider or exact model against the exact profile
   revision the owner saw, with a request id that makes a retry idempotent. Both are `private`; they are on the CLI,
   the web head and the private API, and not on MCP: a model can't change its own level. Setting `provider` or
   `model` to an empty string clears it.

**How a level becomes a model**

3. **One pure resolver.** `resolve(provider, level, offers, exact=None, granted=None)` in
   `heinzel/modules/heinzel/_levels.py` returns the model and effort, the level it ran as, and how it chose (`exact`,
   `table`, `default` or `provider-default`). Same inputs, same answer; it calls nothing.
4. **Auto is routed by code, never by the model.** In this record Auto resolves by one fixed rule: it runs as
   Medium, and the turn records `auto` asked, `medium` ran, rule `auto-fixed-medium`. A later router, deterministic
   and recorded per turn in the same way, replaces the rule; it may read the message and the Heinzel's state, never
   a model's own opinion of what it needs.
5. **Models by level from the offers.** Only offers that call tools, aren't marked unavailable and (with a grant)
   are granted are candidates, in the provider's order. A code-defined table sorts an offer into a tier by the words
   in its id and aliases: `haiku`, `mini`, `nano`, `lite`, `small`, `tiny` are Low; `opus`, `pro`, `ultra`, `large`
   are High; `sonnet`, `flash`, `medium` are Medium; Low words are checked first. The first candidate of the level's
   tier wins (`table`); with none, the provider's first candidate, its default (`default`). The table lives in code,
   is tested row by row, and is extended by a release, never by a setting.
6. **Effort by level from the offer's hints.** Low, Medium and High ask for effort `low`, `medium` and `high`. An
   offer without `thinking` takes no effort (none is passed). An offer that lists `efforts` gets the nearest listed
   one, the lower on a tie, so a level never asks a model for more than it takes. An offer with `thinking` and no
   list gets the level's effort.
7. **The exact model wins.** With an exact model set, the resolver uses it and only the effort follows the level. It
   must be offered, available, call tools and (with a grant) be granted; otherwise the turn is refused with a
   sentence that names the action and the way out ("clear the exact model in the Heinzel's details").
8. **A provider that lists no models** (Claude Code, the built-in Model API option) resolves to its own default:
   no model and no effort are passed, the level is still recorded. An exact model on such a provider is refused,
   since hz can't enforce it (Claude Code's chat refuses model overrides).
9. **A level never widens a grant.** Where a model grant binds a run, the resolver chooses only among the granted
   models and refuses an exact model the grant doesn't cover. A level changes neither caps, budget mode, tools nor
   approvals; the dollar-cap checks of ADR 0159 still run before the turn.

**Where it applies**

10. **Behind the `one_chat` flag.** With the flag on, a chat turn on a Heinzel resolves its model through its
    intelligence: the provider is the Heinzel's `provider`, or else the configured AI access. With the flag off,
    every turn runs exactly as before (ADR 0159's `plugin_model` and `plugin_effort`). The schema is added either way.
11. **Recorded per turn.** The turn's backend binding records the level asked, the level it ran as, the rule and how
    the model was chosen, next to the model and effort. Before every request of a turn hz checks that the provider,
    plugin version, intelligence and budget mode still hold, from the configuration and the profile only, without
    calling the plugin; a change ends the turn as a change of access does today.
12. **What the chat shows.** The chat never shows a model name: the level may be shown (slice 4's time line, the
    Heinzel's page), and the exact model only in the Heinzel's details. The interim picker of 0.9.43 is replaced by
    the level when `one_chat` is on; the page that edits the level and hides the picker comes in later slices.

### Consequences

- Good: people choose how hard a Heinzel thinks, not which vendor's model; a provider's list can change without the
  person relearning names.
- Good: what ran is decided before the turn and recorded with it, so cost reviews can explain every turn.
- Bad: the word table is a guess for lists without tiers; an unknown id falls back to the provider's default. The
  optional `level` hint below fixes that for plugins that say.
- Bad: on Claude Code a level changes nothing the model sees yet: its chat can't take a model override.
- Neutral: Auto runs as Medium until the router ships; the turn record says so.

### Confirmation

- `tests/unit/modules/levels_test.py`: the resolver for every level and Auto, offers without hints, a provider
  without offers, the exact model winning and refused, effort clipping, the table's rows, and grant refusals.
- `tests/unit/modules/levels_profile_test.py`: the stored profile (default left out, round trip, revision checks,
  idempotent retry), the two operations, and a turn's choice with the flag on and off.

## More Information

Built in slice 14 of the chat build: the schema, the resolver, the two operations and the turn's choice behind
`one_chat`. Later: the Auto router (its own slice), the Heinzel's page that shows and sets the level (slices 7 and a
UI slice), the level in slice 4's time line, and an optional `level` hint on `ModelOffer` (additive, interface 1.1)
so a plugin can sort its own models into tiers.
