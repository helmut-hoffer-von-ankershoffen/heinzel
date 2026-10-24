---
status: proposed
date: 2026-10-24
decision-makers: maintainer (direction), architect (evaluation and shape)
amends: 0029, 0045
related: 0014, 0021, 0027, 0028, 0030, 0037, 0040, 0049, 0051, 0052
amended-by: 0057
---

# 0050: Pydantic AI runs a turn; a coding agent is a handoff

## Context and Problem Statement

[ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md) makes hz the owner of a
Heinzelmännchen's session: hz builds every turn's context from the spine and must get back typed results (a draft with
its contract version, lessons, an amendment, a question). Today a render is one Claude Code headless run
([ADR 0029](0029-agent-runners-are-capability-adapters.md)): the prompt goes in, text comes out, Claude Code keeps the
transcript, and the only typed boundary is a `result` event parsed defensively. The chat
([ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md)) resends its transcript each message because
no runner's session is ours, and reaches hz's operations only through a stdio MCP server started by the agent. The
maintainer leans towards Pydantic AI running each turn with typed tools backed by our services and structured outputs,
model-agnostic, and named Pi Durable as an alternative. ADR 0029 says "no agent SDK in core". What runs a turn, and
what becomes of Claude Code, Codex and the other coding agents?

## Decision Drivers

- The session is hz's (ADR 0049): the engine must take a context hz built and give back typed messages hz can append,
  prune and replay; a runner that keeps its own transcript can't.
- Typed boundaries (tenet 10): tools are our services with pydantic signatures; the outcome is a pydantic model; a
  draft that doesn't validate is a failed turn, not a surprise.
- Tenet 8, model-provider agnostic: one engine, every provider and any OpenAI- or Anthropic-compatible company
  gateway, by configuration; no vendor's loop owns what a Heinzelmännchen does.
- Permissions live in code: a turn may *request* a write, only hz *grants* it ([ADR 0028](0028-integrations-are-capability-adapters.md),
  the `ActionGate` of ADR 0045); the engine must not bring its own tool permissions.
- Testable without a model: the suite runs no real agent today ([ADR 0021](0021-workflow-contracts.md) Confirmation)
  and must keep that.
- Honesty about what a coding agent gives that a library doesn't: the user's subscription login, a shell, files, the
  user's own skills and plugins, and a mature harness for heavy work.

## Considered Options

- **Pydantic AI runs every turn in process with typed tools and structured outputs; coding agents stay `agent-runner`
  adapters, reached by a `handoff` tool for heavy work** (chosen)
- **Claude Code headless as the turn engine** (the status quo of ADR 0029, extended with `--resume`): it keeps the
  transcript under `~/.claude` in its own format, so hz would mirror what it can parse from `stream-json` and lose the
  rest; its sessions are per machine and per vendor; its tools, permissions and context management are Claude Code's,
  tuned for coding, not for a Heinzelmännchen that reads a tracker and places a section; typed tools reach it only
  through an MCP server hz must start for every turn (ADR 0045), and structured outputs only as text to parse. Its real
  advantages (the user's subscription login, a shell and files, the user's skills and plugins) are exactly what heavy
  work needs, so it is kept as the handoff.
- **Pi Durable** (Earendil, TypeScript, three weeks old): a harness that owns conversations, documents and tasks in
  SQLite with checkpoints and idempotent submissions. It is a runtime, hz would run *inside* it, in another language,
  against an API that "changes without notice". Its rules are borrowed in ADR 0049; a `heinzel-pi` adapter can make the
  pi agent a handoff target through the command runner.
- **A hand-written loop over a provider SDK** (`anthropic` or `openai`): it is what Pydantic AI already is, with
  provider-specific tool-call shapes, retries and streaming to maintain ourselves, and it ties the core to one provider
  unless we write the abstraction Pydantic AI has.
- **The Claude Agent SDK**: a strong harness, one provider, Claude Code's tool and permission model in process; against
  tenet 8 and the same ownership problem as headless.
- **Another Python agent framework** (LangGraph, the OpenAI Agents SDK, smolagents): LangGraph brings a graph runtime
  and a large dependency tree for a loop whose shape ADR 0049 fixes anyway; the OpenAI Agents SDK is one provider's (it
  reaches others only through compatible endpoints); smolagents is built around code-executing agents. Pydantic AI is
  the one built on the models we already use everywhere (pydantic), provider-agnostic by design and small.

## Decision Outcome

Chosen option (proposed): **Pydantic AI is the turn engine, in the core, with typed tools that are hz's services and a
structured outcome hz acts on; coding agents remain `agent-runner` adapters that a turn reaches through one `handoff`
tool for heavy, file-bound work, and what comes back is their report, not their session.** The maintainer's lean is
confirmed; the costs are named under Consequences. Checkable statements:

1. **The dependency.** `pydantic-ai-slim` is a dependency of the core; provider packages are extras
   (`heinzel[anthropic]`, `heinzel[openai]`, `heinzel[google]`, ...), installed with the core the way plugins are
   ([ADR 0006](0006-plugins-installed-through-the-uv-tool-receipt.md): `uv tool install --with`), and `hz system doctor`
   names the missing extra for the configured model. The import-linter contract "the core imports no vendor client or
   agent SDK" keeps forbidding `anthropic`, `openai` and `claude_agent_sdk` in `heinzel`'s own code and allows
   `pydantic_ai`: our code talks to one model-agnostic library, the library talks to the vendors. This reverses ADR
   0029's "no agent SDK in core" for the turn engine alone, because a session hz owns needs an engine hz calls, and one
   provider-agnostic library is the smallest way to stay provider-agnostic. Facts about Pydantic AI below (typed tools
   with `RunContext` dependencies, `output_type` structured outputs, `message_history` continuation with JSON
   (de)serialisation of messages, `UsageLimits`, `Agent.iter()` for stepping, `TestModel` and `FunctionModel`) are
   from its documentation as read on 2026-10-24 and are verified against the pinned version when step 3 of ADR 0049's
   plan is built; a feature that isn't there is replaced by our own code, never by a prompt.
2. **One `Agent` per turn, built by hz.** `heinzel.modules.heinzel._engine` builds a Pydantic AI `Agent` for each turn
   with: the instructions and messages ADR 0049 §8 fixes (the message history is hz's spine rendered into Pydantic AI's
   message models, which serialise to JSON and so into the turn body), dependencies typed as `TurnDeps` (the
   Heinzelmännchen's capability handles, its session, the cause and inputs), the tools of (3) and `output_type =
   Outcome` (4). Steps are driven with `Agent.iter()`, so hz appends a checkpoint after every model request and every
   tool call (ADR 0049 §5) and stops the run between steps when a cap is reached; the engine never runs a turn hz can't
   checkpoint.
3. **Tools are hz's services, read-only, typed.** A turn's tools are generated from its contract: one tool per declared
   input (`read_<name>()`: the input as fetched for this wake, from the body, never fetched again), `search_<capability>(...)`
   for the window-taking methods of the capabilities the contract `requires` (through the Heinzelmännchen's handle,
   requester kind `heinzel`, mode `read`, [ADR 0028](0028-integrations-are-capability-adapters.md)), `last_approved()`,
   `lessons()`, `previous_turns(n)`, `handoff(task, files)` (6) and `report(verb, target, description)`, which appends
   to the Heinzelmännchen's own action log and nothing else ([ADR 0052](0052-the-graph-emerges-from-recorded-reads-and-writes.md)).
   Every tool's signature is a pydantic model, so a bad argument is a validation retry inside Pydantic AI, not an
   exception out of the turn. **No tool writes to the world**: there is
   no `place_draft` tool, no `send`, no `post`; the model asks for an effect in its outcome and hz performs it in code
   after the turn, under the contract's `publishes`, ADR 0038's tag and ADR 0049's idempotency key. Strings tool results
   carry pass `clean_text` and `redact_emails` as every packet does. In the chat with a Heinzelmännchen (cause `asked`)
   the same tools apply, plus hz's read operations and the `ActionGate` of ADR 0045 as in-process tools: a read runs, a
   write becomes a proposal the user confirms; the gate's policy (what never runs from a chat) is unchanged.
4. **The outcome is structured.** `Outcome` is one pydantic model the turn must return: `report` (one line for the
   spine), `draft: Draft | None` (text, the output name, the contract version it was made with, the `### Done when`
   items with the model's check of each), `lessons: list[Lesson]` (sentence, hunks), `amendment: Amendment | None`
   (the proposed contract text and why), `question: str | None`, `summary: str | None` (ADR 0049 §8) and
   `nothing_to_do: bool` with its reason. An outcome that fails validation after Pydantic AI's retries is a `failed`
   turn with the validation error in the report. A draft whose `### Done when` check lists an unmet item is not placed;
   the turn ends `skipped` with the item, so a postcondition gates the write (OpenProse's rule, borrowed).
5. **Model and budget are configuration.** `[heinzel] model` (a Pydantic AI model string, `anthropic:...`,
   `openai:...`, or a compatible gateway with `[heinzel] base_url`) and `[heinzel] model_fast` for `approval` and
   summary turns; a contract may hint a class (`agent.model_class: fast | standard | best`, mapped in configuration),
   never a vendor ([ADR 0029](0029-agent-runners-are-capability-adapters.md)). The API key is a `CredentialLet`
   (`heinzel/model-api-key`, [ADR 0030](0030-credentials-live-in-the-os-keychain.md)) or the gateway's own login; a
   project `hz.toml` can set none of these (`ignored_in_project`). Budget: Pydantic AI reports token usage per request;
   hz prices it with `[heinzel.prices]` (defaults shipped for the models hz names, overridable; a model without a price
   runs only when the user set one) and stops the run between steps once the turn's cap would be crossed, with a
   `UsageLimits` token ceiling derived from the cap as the hard stop inside a step. A cost hz can't compute counts at
   the cap, as the chat does today.
6. **A coding agent is a handoff.** `handoff(task: str, files: dict[str, str], budget_share: float)` runs the bound
   `agent-runner` ([ADR 0029](0029-agent-runners-are-capability-adapters.md): Claude Code, the command adapter, a Codex
   or pi adapter from a plugin) once, read-only (mode `read`), in a private working directory holding `files` (inputs
   the turn chose, never credentials), with the task as its prompt, the contract's body as its standing instructions, a
   share of the turn's remaining cap and timeout, and returns the runner's report as the tool result. It is for work
   that needs a shell, many files or a mature coding harness: reconciling a release-notes page against a repository,
   checking a document against a code base, drafting from a repository's history. The coding agent writes nothing
   outside its directory and places nothing; the turn decides what to do with its report. What is kept: the spine's
   `step.handoff` holds the runner, model, status, tool-call counts, cost, duration and the hashes of task and report;
   the turn body holds the task, the report and the runner's transcript as the adapter returns it (`stream-json` for
   Claude Code), expiring with the body (ADR 0049 §3). The coding agent's own session files are its own and hz never
   depends on them. With no runner bound, `handoff` is absent from the tools and the instructions say so.
7. **Skills feed the turn.** Advice for *how* is Markdown in the Agent Skills format
   ([ADR 0014](0014-agent-skills-and-the-claude-code-plugin.md)): `skills/<name>/SKILL.md` shipped by a plugin and
   listed by its playbook (`PlaybookLet.skills`, additive, [ADR 0037](0037-plugins-bring-playbooks.md)), in the team
   repository's `skills/`, or in the user's own directory. A contract names the skills it uses (`hz.uses`, a key ADR
   0021 lists as deferred and this record defines); a skill may also declare where it applies (`hz.applies`: asset
   types, roles, capabilities) and is then loaded for every matching turn. They are rendered into the instructions after
   the contract and before the lessons, general to specific along the layer stack
   ([ADR 0040](0040-layers-and-playbooks-are-plugins.md)); each one's fingerprint is recorded in `turn.started`. A skill
   is advice: it can't add an input, a tool, a target or a cap (`HZC051` when its frontmatter tries). The same files
   work as plain skills in Claude Code, Codex and Cursor, which is the point of the format.
8. **The chat engine.** ADR 0045's chat sessions run on the same engine: hz's operations as in-process tools (reads run,
   writes are proposals through the `ActionGate`), the same caps, the same streaming through `Progress.emitter()`. The
   stdio MCP server of ADR 0045 is no longer needed for the chat itself and stays for coding agents: a handoff's tool
   profile may name it so a coding agent can read through hz (and still can't write). `hz system runner ask` stays for
   one-off questions.

### Consequences

- Good: typed tools and outcomes, a session hz can append and replay, every provider and gateway by configuration, and
  a suite that runs with `TestModel` and `FunctionModel` instead of a recorded CLI.
- Good: the chat loses a process boundary (no stdio server per message) and gains the same engine, caps and gate.
- Good: heavy work keeps the best tool for it, the user's coding agent with their subscription, skills and shell, and
  the Heinzelmännchen keeps the decision and the record.
- Bad: **logins.** A Pydantic AI turn needs an API key or a gateway; a Claude subscription's OAuth login works only in
  Claude Code. Users without a key can still run every Heinzelmännchen whose turns are handoffs, and a plugin may offer a
  Pydantic AI `Model` that drives `claude -p` for the model step alone (text in, text out, tools emulated); that is
  slower and lossy, which is why it is a plugin and not the default. `hz system doctor` says which of the two a
  machine has.
- Bad: **we own what a harness gave us**: context building, summaries, retries and pricing. ADR 0049 §8 and (5) make
  them deterministic and tested; they are still code to keep.
- Bad: a new dependency with its own release cadence in the core, against ADR 0029's letter; pinned, audited and
  behind one module (`_engine`) so a replacement is one module's work.
- Bad: skills in the turn are a new prompt-injection surface, like contract bodies today; they are files a person
  installed or merged, pinned by fingerprint, and never grant anything (7).
- Neutral: the `agent-runner` capability and both core adapters stay as they are; ADR 0029's planned adapter contract
  tests now also cover "returns a transcript the handoff can keep".

### Confirmation

Not yet: proposed. When implemented:

- `tests/unit/modules/heinzel_engine_test.py`: a turn with `FunctionModel` produces a valid `Outcome`; an invalid one
  ends `failed` with the error; an unmet `### Done when` item ends `skipped` and places nothing; the cap stops a run
  between steps and the token ceiling inside one; a checkpoint follows every step; `message_history` round-trips through
  the turn body.
- `tests/unit/modules/heinzel_tools_test.py`: the generated tools match the contract's inputs and `requires`; every
  tool is read-only (a write-mode method is never exposed, checked against the capability registry); tool results pass
  `clean_text` and carry no email address; `handoff` is absent when no runner is bound.
- `tests/unit/modules/heinzel_handoff_test.py`: a handoff runs the bound runner once with the files, share and
  instructions, keeps hashes and cost in the spine and the transcript in the body, and writes nothing outside its
  directory (with the fake runner of `tests/integration/runner_test.py`).
- `tests/unit/modules/heinzel_skills_test.py`: skills resolve along the layer stack, render in order, are fingerprinted,
  and `HZC051` refuses a skill that tries to grant.
- `tests/unit/modules/runner_chat_test.py` (adapted): the chat's reads run and writes are proposals under the same
  policy; `tests/integration/chat_test.py` without a stdio server.
- The import-linter contract in `pyproject.toml` lists `pydantic_ai` as allowed and the provider packages as forbidden;
  `tests/unit/repository_test.py` checks the extras exist for every model prefix the defaults name. No test calls a
  real model.

## More Information

- The maintainer's proposed ADR title "hz owns the session, Pydantic AI runs the turn" is split: ownership is
  [ADR 0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md), the engine is this record. The
  maintainer's lean towards Pydantic AI is confirmed after weighing Claude Code headless and Pi Durable above.
- **Amends** [ADR 0029](0029-agent-runners-are-capability-adapters.md) ("no agent SDK in core" no longer holds for the
  turn engine; the `AgentRunner` protocol, its adapters and its configuration are unchanged and become the handoff
  seam) and [ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md) (the chat runs on the same
  engine with in-process tools; its gate, policy, sessions, caps and streaming stay; the stdio MCP server serves
  handoffs).
- Builds on [ADR 0014](0014-agent-skills-and-the-claude-code-plugin.md) (the skills format), [ADR 0037](0037-plugins-bring-playbooks.md)
  and [ADR 0040](0040-layers-and-playbooks-are-plugins.md) (where skills come from and in what order), [ADR 0028](0028-integrations-are-capability-adapters.md)
  (handles are the tools) and [ADR 0030](0030-credentials-live-in-the-os-keychain.md) (the key).
- **Open:** whether to ship a `claude-code` Pydantic AI `Model` adapter as a first-party plugin for users without an API
  key (recommendation: yes, after the thin slice works with a key; it is the subscription story for many users); whether
  `handoff` should be able to hand *files back* (a drafted document) rather than a report only (recommendation: a
  report plus files hz reads from the directory, both into the body, nothing placed by the agent); the default
  `model_fast` for lessons and summaries.
