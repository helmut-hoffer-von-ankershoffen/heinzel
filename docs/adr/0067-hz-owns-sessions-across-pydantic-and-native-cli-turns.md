---
status: proposed
date: 2026-10-25
implementation: partial
decision-makers: maintainer (direction), architect (implementation)
amends: 0050
related: 0028, 0029, 0030, 0049, 0057, 0061
---

# 0067: hz owns sessions across Pydantic and native CLI turns

## Context and Problem Statement

[ADR 0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md) chooses Pydantic AI for the turn loop and
coding agents as handoff targets. The maintainer requires direct API access, Pydantic turns using an existing
Claude Code or Codex subscription, and native turns driven by either CLI. These are distinct execution choices;
changing one must preserve the hz-owned identity, visible history and pending decisions of
[ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md).

## Decision Drivers

- A person can start talking before approving a contract, then develop and activate that contract in the same chat.
- Every route must enforce the effective data, tool, approval, time and budget policy before model work starts.
- No process lives for a sleeping session, and no adapter owns authoritative history or approval state.

## Considered Options

- **A shared turn service with explicit Pydantic and native CLI drivers** (chosen).
- **Only direct API models with a coding-agent handoff**: excludes the requested subscription-based conversations.
- **Only native CLI harnesses**: gives up the requested Pydantic loop and its typed tools and outcomes.

## Decision Outcome

Chosen option: **one hz session and turn service with explicit driver and model-access choices**, because the user
can choose their paid access without delegating history, grants or approval authority to a provider.

1. The module owns portable visible messages, an idempotent inbox, versioned contracts, turn checkpoints, effects,
   pending questions and decisions. A new Heinzel immediately gets one stable `hz-` identity and a proposed contract;
   its chat uses existing user grants until explicit contract activation. A name is display text. Manual, scheduled
   and conversational turns use the same session and budget service.
2. A recorded backend binding distinguishes `api` (Pydantic loop and configured provider key), `cli-model`
   (Pydantic loop and a CLI subscription as one model-response primitive), and `native-cli` (a bounded native harness
   turn). Claude Code and Codex are separate adapters. Native vendor session ids are optional backend-epoch metadata.
   An adapter may instead project hz history into an ephemeral process. Hundreds of sleeping sessions require no
   idle CLI processes.
3. A CLI model bridge implements the pinned Pydantic `Model.request` contract. It sends the permitted visible
   history, typed tool/output schemas and supported settings, and parses a closed structured response into Pydantic
   message parts. The CLI executes no native tool: only the Pydantic loop can dispatch validated tool calls through
   hz. Tool ids, retry feedback, tool results and deferred decisions survive the translation. Initial bridges may
   return complete responses; they do not pretend to stream tokens.
4. Before either CLI route runs, its capability check must prove the requested tool/data boundary and disable
   ambient native plugins, hooks, MCP sources and unrelated filesystem or network authority. An unknown or
   unenforceable boundary refuses the binding with a fix. Disabling one shell feature alone is insufficient proof.
   Use documented CLI authentication, never extract or reuse raw subscription tokens. Subscription usage is not an
   API dollar charge; subscription plans use the provider's limits and unknown cost remains unknown. USD caps govern
   direct APIs; an explicitly requested hard USD cap on a subscription refuses the binding when unenforceable.
5. A binding change applies between turns, with a new recorded revision and backend epoch. It preserves the hz id
   and portable visible history, not hidden reasoning, native caches or unfinished native calls. Pending or uncertain
   effects require reconciliation before switching. A waiting question or approval is stored state, with no active
   model process. Answering it cannot widen a grant.
6. Every route shares the same typed Outcome, operation gate and effect ledger. A native harness report is validated
   before it can become a draft or proposal. Native tools cannot bypass hz's approval policy. Human proof issuance
   and scoped auto-approval are a separate approval-policy amendment; this decision grants neither by itself.
7. Pin `pydantic-ai-slim==2.61.0` for the first engine and its serialization fixtures. A reviewed upgrade must pass
   the Conversation round-trip, checkpoint, retry, deferred-call and model-bridge conformance fixtures. The kernel
   imports no agent SDK; private model bodies never enter the metadata index or telemetry.

### Consequences

- Good: subscriptions, provider keys and native harnesses can share one durable conversation and consistent grants.
- Good: a provider switch does not create a second chat or lose an unanswered question.
- Bad: model bridges and harnesses need separate conformance suites. A requested route remains unavailable until
  its effective grant boundary is demonstrated; documented switches alone do not establish it.
- Bad: projecting visible history can cost tokens and does not preserve vendor-internal state.

### Confirmation

Review only for this decision; implementation evidence must be added before claiming a route is supported.
The implementation gates are offline FunctionModel turns, per-node Conversation serialization goldens, idempotent
inbox/restart tests, unchanged identity across backend epochs, no idle child process, tool-catalog isolation, budget
and timeout refusal, deferred approval and uncertain-effect tests. CLI fixtures assert exact arguments and typed
translation without paid requests. Real account conformance runs require explicit authorization and credentials.

## More Information

Pydantic facts are checked against the installed pinned package. CLI capability facts must be recorded against the
installed version in adapter tests, with a fail-closed result for unsupported versions. This amends ADR 0050's
restriction of coding agents to handoff targets; its typed-tool, checkpoint and permission requirements remain.
