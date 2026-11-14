---
status: proposed
date: 2026-11-14
implementation: built
decision-makers: maintainer (decision), architect (shape)
amends: 0043, 0120
amends-0043: notices may also go to the owner's own chat messages, if the owner switches that on
amends-0120: a global owner switch may send hz's own notices and questions to the owner's own direct messages
related: 0028, 0045, 0049, 0068, 0094, 0159
---

# 0160: Chat is an extra place for notices and questions

## Context and Problem Statement

Some chat tools are reached only through an interface that talks to one person: the user's own direct messages. It
can send yourself a message, and it can ask yourself a question and wait for the answer, but it can't read or post in
channels. A company may wrap its chat that way for its own reasons. People still want hz to reach them there: a notice
when a draft is ready, and a Heinzel's question when they are away from the app.

The `chat` capability ([ADR 0028](0028-integrations-are-capability-adapters.md)) had only `messages`
and `post`, for channels. hz's notices ([ADR 0043](0043-desktop-notifications-one-policy-shown-once.md)) never left
the machine. The channel ports of [ADR 0120](0120-channels-carry-notices-and-approval-claims.md) carry a helper's
notices and draft approvals, but each posting needs its own human review, and "no global setting grants posting
rights".

## Decision Drivers

- It is an extra place, never a replacement: the app shows every notice and every question as before.
- One switch per use, off by default, offered only when the bound chat connector can carry it.
- A chat answer is the owner's answer, no more: it never approves a write.
- Nothing about a particular chat tool in Core; a plugin decides how to reach its chat.
- A slow or broken chat never delays, blocks or drops anything in the app, and never blocks hz's event loop.

## Considered Options

- **Two optional methods on the `chat` capability, called only by Core, behind two owner switches** (chosen).
- Reuse the `NotificationChannel` / `ApprovalChannel` ports of ADR 0120: they need a reviewed posting per message and
  a reviewed target, and approvals are their purpose; a global "also send everything" switch would contradict them.
- A new capability `self-chat`: the same adapter would be bound twice, and Settings would show a second chat.

## Decision Outcome

Chosen option: **two optional, core-only methods on `chat`**.

**The interface.** `Chat` gains `notify_self(text, *, thread=None, context=None) -> SelfMessage` and `ask_self(question,
*, options=(), timeout, thread=None, context=None, cancel=None) -> SelfAnswer`, both mode `write`. `SelfMessage` has
`id`, `thread` (an opaque handle to pass back) and `url`. `SelfAnswer` has `thread`, `text`, `option` (the offered
option chosen, or none for a typed reply) and `timed_out`; `answered_by_option` says which. `ask_self` blocks until the
owner answers, the timeout passes or `cancel` is set, and then returns `timed_out=True`; an adapter checks `cancel`
every few seconds.

Every method of a capability may be unsupported (ADR 0159's `unsupported` attribute and `NotSupportedError`), so an
adapter that can only message the owner declares `unsupported = ("messages", "post")`. The two new methods are
`optional`: an adapter written before them doesn't define them and still loads, as if it had declared them
unsupported. The conformance check (`heinzel.testing.assert_adapter`) skips what an adapter doesn't support and checks
the parameters of what it does.

The methods are `core_only`: no requester's handle offers them in any mode, so no operation, job or playbook can talk
to the owner in their own direct messages or read their answers, not even one with `chat:write`. Only hz's notify
service and question relay call them, through `Kernel.self_chat()`, a trusted view like `Kernel.ai_models`
([ADR 0159](0159-plugins-bring-their-own-ai-models-through-pydantic-ai.md)). That view bounds what goes out (text
cleaned of control and invisible characters and cut at 3000 characters, a one-line context of at most 60 characters,
at most 10 distinct options of at most 75 characters, a timeout from 1 second to an hour) and checks what comes back:
a chosen option must be one that was offered and the answer's text is then exactly that label; a typed reply is
cleaned and refused above the 32,000 characters a typed message may have. Adapters neutralise mention syntax as for
`post`.

**Notices also in chat.** `[system.notify] chat_notifications` (off by default). Every notice that pops up also goes to
`notify_self`: the same notice at the same time under the same policy, so quiet mode, working hours, the kinds switched
off and the rate limit apply. Recording a notice starts a background sender and never waits for it; the daemon's tick
sends too.

Each notice is claimed in a private ledger (`self-chat/notices.json` under the state directory, 0600) before
the adapter is called, so two processes never send it twice; a failed send is logged, not retried, and a claim left by a
process that ended mid-send is taken over by the next sender. Every call to the adapter waits at most 15 seconds; the
daemon's tick sends after its pop-ups, in the background. Only notices that became due in the last hour are sent, so
switching it on replays no backlog. A Heinzel's notices share one thread per
Heinzel chat; the context is the Heinzel's name, else the notice's kind.

**Questions also in chat.** `[system.notify] chat_questions` (off by default) and `chat_wait_minutes` (5 by default, 1
to 60: Heinzel's own choice, long enough to answer from a phone, short enough that a missed question stops waiting). A
question is a Heinzel turn that ends asking its owner something; its reply in the chat is marked with a `question.asked`
event. With the switch on, the question relay asks it with `ask_self` in a worker thread, with the Heinzel's name as
context and the Heinzel chat's thread. Whichever answer comes first counts:

- **In the app first.** Anything after the question in the chat's journal answers it. The relay sets `cancel` and the
  thread gets "Answered in Heinzel."; a chat answer that still arrives is dropped.
- **In chat first.** The answer becomes the owner's next message in that chat, exactly as if typed: the same
  validation, the same turn, the same model framing. It is accepted by one compare-and-set under the chat's control
  lock, so it and a typed answer never both answer one question. Its origin is `self-chat`, a call identity of its own
  that the relay takes for the owner; the app shows "Answered in Slack" (the connector's name) under it.
- **No answer in time.** The thread says the question is still open in Heinzel, where it stays, as it does in the app.
  A question in the app has no timeout, so the outcome and the record are the same as never answering there.
- **A refused answer** (an option that wasn't offered, too long) leaves the question open and says so.

The relay decides these from its own record (which chat, which question, whether it cancelled), never from the thread
or the `timed_out` flag the adapter returns. One ask is live per Heinzel chat (a newer question supersedes it, without
a note) and at most three per process; over the cap the question is only in the app. An adapter that ignores `cancel`
frees its place 30 seconds after the wait. Quiet mode keeps questions out of chat; working hours don't, as a question
follows the owner's own conversation or the schedule's own quiet hours. If the process ends while an ask waits, the
next ask says "still open" in that thread once.

A scheduled turn's question is relayed by a detached helper. The daemon's tick is a short-lived process
([ADR 0020](0020-daemon-head.md)) that would end a waiting ask with it, so the daemon job marks its turns, and there the relay
starts a private helper process (`python -m heinzel.modules.heinzel._self_chat_helper`, no command of its own) and
returns at once. The helper has the tick's environment (profile, home, state directory), a session of its own, no
inherited locks or descriptors, and writes what it prints to a log file; it runs the same relay with the same switch,
quiet mode, wait, caps and answer path. One ask is live per Heinzel chat across processes too: an ask whose chat another
process now asks in is superseded. A helper asks only while fewer than three asks are live across all processes, by the
ledger's live entries of running processes; over that cap the question is only in the app. A helper that crashes leaves
its live entry, and the next ask says "still open". The web app, a long-lived process, keeps its worker thread.

A Heinzel chat that no longer exists keeps nothing: before each question the relay drops its thread and any live ask
(cancelled without a note), in both ledgers, so a new chat never gets an old chat's thread.

**Approvals never go through chat.** `ask_self` asks questions; it never approves anything. Write approvals stay with
the CLI and paired web confirmation ([ADR 0068](0068-human-approval-proofs-and-head-parity.md)) and the reviewed channel
claims of ADR 0120, including a plugin's own reviewed Approve and Decline buttons. Every approval, publication and
head-only path accepts only head or daemon identities, so the `self-chat` identity is refused there; a test fails if any
other file names it. An answer like "yes" in chat is only a message in the chat; a write it leads to is proposed and
approved in the app like any other. Approving through `ask_self` would be a later extension with its own security
review.

**What leaves the machine, and only when switched on.** The bound chat connector receives: a notice's title, its text
(which may repeat what a plugin or a Heinzel wrote) and the web app's local link or a command; the whole reply that
asks a question, as the app shows it, up to 3000 characters, which may quote what the Heinzel read from connected
sources; the context label (a Heinzel's name or a notice's kind); and short follow-up notes. No file contents, tool output,
credentials or approval previews. The switch descriptions say the text goes to the connector.

### Consequences

- Good: a person away from the app still hears from hz, in the one place their company lets a tool reach them.
- Good: a chat connector that can only message its owner is a first-class chat adapter.
- Neutral: notice and question text leaves the machine once the owner switches it on.
- Bad: an answer typed in chat runs a turn like a typed one; a chat outage only costs the extra copy.

### Confirmation

Unit tests: the protocol shape and the optional and core-only methods, including a publish operation refused on both;
a self-only adapter; the bounds both ways; notices fan out once with a failing, unbuildable or unsupporting chat
costing nothing; the race (app first, chat first, both at once, late, superseded, timeout, refused, a hung adapter,
a restart); the caps; the thread kept per chat and forgotten with a deleted chat; no blocking of the caller; the settings status. An integration test
runs a turn that asks, and a turn for a chat answer under the `self-chat` identity only. Vitest covers the switches and
the "Answered in" line; a browser journey switches it on with a test plugin's chat connector.

## More Information

The public `heinzel-slack` plugin implements both methods over the owner's own Slack direct message, next to its
reviewed channel ports. `heinzel-acme-connectors`, the worked example in the `heinzel-acme` repository, has a chat
connector that implements only these two. Plugin authors: [docs/PLUGIN_AUTHORING.md](../PLUGIN_AUTHORING.md).
