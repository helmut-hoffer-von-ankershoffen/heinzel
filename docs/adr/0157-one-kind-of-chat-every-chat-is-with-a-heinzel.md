---
status: accepted
date: 2026-11-14
implementation: built
decision-makers: maintainer (decision: no flag, no migration), architect (shape)
amends: 0045, 0049
amends-0045: the app's chat is always with a Heinzel; the general chat, its switcher and "Ask about it" leave the web app
amends-0049: no general chat beside the Heinzels' sessions; a default Heinzel, named at first start
related: 0039, 0043, 0058, 0115, 0122
---

# 0157: One kind of chat: every chat is with a Heinzel, and a default Heinzel is named at first start

## Context and Problem Statement

[ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md) gave the web app one chat, beside every
screen and on Home, about whatever the person was looking at. [ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md)
then made each Heinzelmännchen a durable agent with its own session, and kept the general chat "for everything that is
not about one Heinzelmännchen". The app thus had two kinds of chat in one panel: the general chat with its own
switcher (Workshop, recent chats, a model picker and costs), and a Heinzel's session, reached through that switcher
and left again with "Back to chat". People had to know which one they were in, and a first message to the general
chat had to be carried over into a new Heinzel.

The chat redesign (bead agqz, canvas "Chat sidebar redesign") makes every chat a chat with a Heinzel. It was built in
slices behind the flag `one_chat` ([ADR 0115](0115-merge-unfinished-work-dark-behind-named-feature-flags.md)). On 2026-11-14 the
maintainer decided: "We don't need this behind a feature flag", "And no need to migrate data", and 0.9.44 is the chat
UI release: a complete, coherent new chat. This record states what that chat is.

## Decision Drivers

- One kind of chat: the person always knows whom they are talking to.
- Nobody starts in an empty chat with no one in it: the first start names a Heinzel.
- No data is lost or rewritten: what is on disk stays, as it is.
- A release is a coherent product: no flag, no second path to keep working.

## Considered Options

- **Every chat is with a Heinzel; the general chat leaves the app; old general-chat sessions stay untouched and
  unused; no flag** (chosen by the maintainer).
- **Move the general chat's history into the default Heinzel** (plan slice 8): read-only history or a one-time import
  into its journal. Rejected by the maintainer: no migration.
- **Keep the general chat behind More → Earlier chats** (slice 1 while dark): two kinds of chat remain. Rejected.
- **Keep the flag until every slice ships**: the old chat stays the product and every slice keeps two paths working.
  Rejected: each slice deletes the old path it replaces.

## Decision Outcome

Chosen option: **every chat in the web app is with a Heinzel.** Checkable statements:

1. **No general chat.** The chat panel shows one Heinzel's session (`hz heinzel session`) and nothing else. There is
   no general composer, no Workshop or recent-chats switcher, no model picker or cost line of the general chat, no
   "Back to chat", no Earlier chats, and no "Ask about it" on Today's cards, Today's widgets or the team graph: each of
   those opened a general chat.
2. **The default Heinzel.** Without a `?chat=hz-…` link the panel opens the Heinzel the person talked to last in this
   browser, else the oldest one that is not retired. A retired or removed Heinzel is never the default. A link to an
   old general chat (`?chat=<id>` that is not a Heinzel, or `?chat-context=`) opens the default Heinzel and reads
   nothing of the old chat. The desktop menu's New Chat opens the chat on the default Heinzel.
3. **First start.** With no Heinzel yet the panel is "Meet your first Heinzelmännchen": a name, the look, and the first
   mission; Start creates it with `hz heinzel new-chat` and opens its chat. Step 7 of the setup is the same form, so
   there is one first start, not two. New Heinzels after the first come from the header's switcher (New Heinzel).
4. **The header.** The face and the name open the switcher (your Heinzels with their state word, then New Heinzel);
   More holds Search and Details; Close collapses the panel. The reviews, terms and schedules sit behind Details and
   show when More asks or a review link lands on them.
5. **Message states.** A message that did not go through waits in its bubble with "Not sent" or "Not answered" and
   Try again, which retries the same request. An uncertain turn never gets a plain retry; it keeps its own recovery
   (T2).
6. **No migration.** Old general-chat sessions (`hz chat`, `system.runner.chat` sessions kept by hz) stay on disk
   untouched and unused: not shown, not imported, not deleted. The operations that hold them stay in the core and the
   CLI; the web app no longer calls `system.runner.chat open` or `list`, nor `ai models` for the general composer's
   picker. The scoped review chats a Heinzel opens (terms, schedules, sources, notifications) keep their operations.
7. **No flag.** `one_chat` is not a flag; a profile that still names it gets the usual report of an unknown name and
   nothing else.

### Consequences

- Good: one kind of chat; a fresh install starts by naming a Heinzel; the code keeps one path.
- Good: nothing on disk changes, so going back to an older version still finds its general chats.
- Bad: earlier general conversations are no longer reachable in the web app (the CLI still lists them with
  `hz chat list`).
- Bad: "Ask about it" from Today and the team graph is gone until asking a Heinzel about a card is designed.
- Neutral: the general chat's operations stay, unused by the web app; removing them is a separate decision.

### Confirmation

`web/src/layout/OneChat.test.tsx` (first start, default Heinzel, switcher, More without Earlier chats, old general
links open the default Heinzel), `web/src/chat/MessageStates.test.tsx`, `web/src/layout/ChatPanel.test.tsx`,
`web/src/onboarding/OnboardingWizard.test.tsx` (step 7) and the journey `tests/e2e/web_one_chat_test.py`, which runs
without the `features` setting.

## More Information

This amends [ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md) (the app's one chat is now
always a Heinzel's) and [ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md) (the general chat no longer stays beside
the Heinzels' sessions). The intelligence level per Heinzel is ADR 0158.
