---
status: proposed
date: 2026-11-14
implementation: partial
decision-makers: maintainer (decision), architect (shape)
related: 0057, 0159
---

# 0158: Intelligence level per Heinzel; Auto is routed by code

## Context and Problem Statement

Every chat is with a Heinzel. People should not need model names to get a good answer, and a model picker by the
chat input turns every message into a choice of vendor and price. The owner decided: models are chosen by level.
Per Heinzel the person picks the AI provider, for example a plugin's `ai-models` option
([ADR 0159](0159-plugins-bring-their-own-ai-models-through-pydantic-ai.md)) or the configured AI access
([ADR 0057](0057-ai-access-is-a-module-one-choice-like-the-theme.md)), and a level: Auto, Low, Medium or High. In the
owner's words, "the provider defines level of intelligence": the provider says which of its models is Low, Medium and
High, and Heinzel never invents a mapping of its own.

An exact model is an advanced choice on the Heinzel's page, never by the chat input.

Release 0.9.43 shipped an interim model picker by the chat input (ADR 0159, statement 7, as first written). This
record replaces it.

## Decision Drivers

- No model names in the chat: the chat shows the Heinzel, not its vendor.
- The provider knows its models; a word list in Heinzel would guess wrong and age with every new model.
- The model never picks its own spend: what runs is decided by code before the turn and recorded with it.
- A level never widens authority: it chooses within what the person and their grants already allow.

## Considered Options

1. **The provider marks its models by level; the Heinzel picks a level** (chosen).
2. **A model picker per chat** (0.9.43's interim picker): vendor names and prices in front of every message.
3. **A table in Heinzel that sorts models by the words in their names**: Heinzel's own mapping; the owner rejected it.
4. **Let the model route itself** (a cheap model decides whether to call a bigger one): the model would pick its own
   spend, and the choice could not be predicted or reviewed.

## Decision Outcome

Chosen option 1. Checkable statements:

**What a Heinzel stores**

1. **The profile holds the intelligence.** A Heinzel's private profile (the `profile.updated` body) has
   `intelligence`: `level` (`auto`, `low`, `medium` or `high`, default `auto`), an optional `provider` (an AI access
   option's name; none means the configured AI access) and an optional exact `model`. The default is left out of the
   stored body, so a profile saved before this record reads and hashes unchanged. It changes only when the owner sets
   it.
2. **Reading and setting it are owner-private operations.** `heinzel level` reads the Heinzel's intelligence and what
   a turn would run on now; `heinzel set-level` sets level, provider or exact model against the exact profile
   revision the owner saw, with a request id that makes a retry idempotent. They are on the CLI, the web head and the
   private API, not on MCP: a model can't change its own level. An empty `provider` or `model` clears it.

**The provider defines the levels**

3. **A provider marks a model per level.** A plugin marks an offer with the optional `ModelOffer.level` (`low`,
   `medium` or `high`); Core's `model_for_level` reads it. Built-in providers, such as Claude Code and the built-in
   Model API option, declare their levels the same way. A provider may mark one, two or all three levels.
4. **A level the provider doesn't mark runs on the nearest one it marks**, the lower on a tie, so a level never spends
   more than asked. The turn records the level asked and the level it ran as.
5. **A provider that marks no level** runs its own default, as Settings pinned it (`[ai.access] plugin_model` for a
   plugin, the runner's default for Claude Code). The turn records the level asked and `provider-default`.
6. **Effort follows the level** within what the marked model takes: Low, Medium and High ask for effort `low`,
   `medium` and `high`; a model without `thinking` takes none, and a model that lists `efforts` gets the nearest one
   it lists, the lower on a tie.
7. **One pure resolver.** `resolve(provider, level, declared, offers, exact=None, granted=None)` in
   `heinzel/modules/heinzel/_levels.py` returns the model and effort, the level it ran as, and how it chose (`exact`,
   `declared`, `nearest` or `provider-default`). Same inputs, same answer; it calls nothing.

**Auto, the exact model and authority**

8. **Auto is computed by code among the provider's levels, never by the model.** In this record Auto asks for Medium
   by one fixed rule (and so runs on the nearest marked level), and the turn records rule `auto-fixed-medium`. A later
   router, deterministic and recorded per turn the same way, replaces the rule; it may read the message and the
   Heinzel's state, never a model's own opinion of what it needs.
9. **The exact model wins.** Set on the Heinzel's page, it replaces the level's model; only the effort follows the
   level. It must be offered, available, call tools and (with a grant) be granted; otherwise the turn is refused with
   a sentence that names the action and the way out ("clear the exact model on the Heinzel's page"). On a provider
   that lists no models it is refused, since hz can't hold the provider to it.
10. **A level never widens a grant.** Where a model grant binds a run, only granted models count as marked, and an
    exact model the grant doesn't cover is refused. A level changes neither caps, budget mode, tools nor approvals;
    the dollar-cap checks of ADR 0159 still run before the turn.

**Where it applies and what the chat shows**

11. **Always, for a turn of a Heinzel.** There is no feature flag. A chat turn of a Heinzel resolves its model through
    its intelligence: the provider is the Heinzel's `provider`, or else the configured AI access. A provider that is
    neither the configured AI access nor an installed plugin's models refuses the turn, naming the Heinzel's page.
12. **Recorded per turn.** The turn's backend binding records the level asked, the level it ran as, the rule and how
    the model was chosen, next to the model and effort. Before every request of a turn hz checks that the provider,
    plugin version, pinned default, intelligence and budget mode still hold, from the configuration and the profile
    only, without calling the plugin; a change ends the turn as a change of access does.
13. **No model names in the chat, and no picker by the chat input.** Release 0.9.44 removes 0.9.43's picker. The
    level may be shown in the chat (the time line) and on the Heinzel's page; the exact model only on the Heinzel's
    page. `hz chat send --model` stays for the command line.

### Consequences

- Good: people choose how hard a Heinzel thinks, not which vendor's model; a provider's list can change without the
  person relearning names, and the provider stays the authority on its own models.
- Good: what ran is decided before the turn and recorded with it, so cost reviews can explain every turn.
- Bad: a plugin that marks no level gives every level the same model; only the effort differs.
- Neutral: Auto asks for Medium until the router ships; the turn record says so.

### Confirmation

- `tests/unit/modules/levels_test.py`: the resolver for every level and Auto, the nearest marked level and its tie,
  a provider that marks none, the exact model winning and refused, effort, and grant refusals.
- `tests/unit/modules/levels_profile_test.py`: the stored profile (default left out, round trip, revision checks,
  idempotent retry), the two operations, and a turn's model chosen by the level.

## More Information

Built in slice 14 of the chat build: the schema, the resolver, the two operations, the turn's choice, and the removal
of the picker by the chat input. `ModelOffer.level` and `model_for_level` come with the 0.9.43 follow-ups. Later: the
Auto router (its own slice), the Heinzel's page that shows and sets the level and the exact model (slice 7), and the
level in the chat's time line.
