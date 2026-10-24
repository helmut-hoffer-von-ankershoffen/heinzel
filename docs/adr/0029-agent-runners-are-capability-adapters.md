---
status: proposed
date: 2026-10-23
related: 0020, 0021, 0028
---

# 0029: Agent runners are capability adapters; core ships Claude Code and a generic command adapter

## Context and Problem Statement

A workflow contract is compiled into a job that runs one bounded agent session with read-only tools
([ADR 0021](0021-workflow-contracts.md), [ADR 0020](0020-daemon-head.md)). People and companies use different coding
agents: Claude Code, Codex, Cursor, or a company's own wrapper CLI that dispatches to several agents. INTENT.md
promises that Heinzelmännchen is model-provider and agent agnostic, and lists depending on a single model provider
or agent SDK as a non-goal. How
does the kernel run an agent session without being tied to one agent?

## Decision Drivers

- Tenet 8: open and independent, model-provider and agent agnostic.
- Bounded runs: every session has a prompt, a contract, a read-only tool profile, a timeout and a budget, and reports
  its draft, its tool calls and its token cost for the receipt.
- Teams and people choose the agent they already pay for and trust; that choice is configuration, not code.
- No agent SDK in core: SDKs move fast, pull in dependencies and tie the core to one provider.

## Considered Options

- **A kernel `AgentRunner` protocol; core ships a Claude Code adapter and a generic command adapter; other agents as
  plugins** (chosen)
- **Embed one agent SDK in core**: fastest start, but ties the core to one provider and its release cadence.
- **Only a generic command adapter**: works for any CLI, but loses structured tool calls and cost reporting where an
  agent offers them.
- **A fixed enum of supported runners in the contract format**: every new agent needs a core release.

## Decision Outcome

Chosen option (proposed): **agent runners are adapters of a kernel `AgentRunner` protocol, selected by
configuration**, because the kernel gets bounded, reportable runs while the agent stays the team's choice.

- **Protocol**: `AgentRunner` takes a prompt and the contract, a read-only tool profile, a timeout and a budget, and
  returns the draft, the tool calls made and the token cost. A run that exceeds its timeout or budget is stopped and
  reported, never extended.
- **Claude Code adapter in core**: runs the `claude` CLI headless with the tool profile and parses its structured
  output.
- **Generic command adapter in core**: runs any agent CLI from a configured command line, with placeholders for the
  prompt file, the tool profile and the working directory, and a configured way to read the result. For example, a
  company's internal wrapper CLI with a sub-command per agent is configured this way.
- **Other agents as plugins**: Codex, Cursor and others are adapters in plugins (`heinzel-<name>`,
  [ADR 0025](0025-plugin-distributions-are-named-heinzel-name.md)).
- **Configuration**: the runner and the model are configuration per team, per person and per job, in the layered
  configuration; a contract may give hints, never a vendor requirement.
- **No agent SDK in core.** Adapters call CLIs; an adapter plugin may use an SDK as its own dependency.

### Consequences

- Good: any agent with a CLI works on day one through the command adapter; switching agents is a configuration
  change.
- Good: receipts report cost and tool calls in one shape whatever the agent.
- Bad: the command adapter can only enforce the tool profile and budget as far as the wrapped CLI lets it; `doctor`
  must say when a configured runner cannot enforce the read-only profile.
- Bad: new work: the protocol, two adapters, the runner configuration and its doctor checks.

### Confirmation

Not yet: proposed, planned for 0.2. When implemented: a contract-test suite every `AgentRunner` adapter passes
(timeouts, budgets, read-only profile, result shape), tests of the command adapter against a fake CLI, and an
import-linter contract that the core imports no agent SDK.

## More Information

- [ADR 0021](0021-workflow-contracts.md) lists an `agent` key under `hz:`, and its research
  ([docs/research/workflow-contracts.md](../research/workflow-contracts.md)) fixes `hz.agent.runner` to the enum
  `any | claude-code | codex | cursor`. Under this ADR the runner is not an enum in the contract format: which
  runner executes a job is configuration, and `hz.agent` keeps hints only (model class, effort, turn limit), with
  `runner` at most an optional hint naming a configured runner. ADR 0021 is proposed and should be aligned before
  either is accepted.
- [ADR 0020](0020-daemon-head.md) receipts gain the runner, model, tool calls and token cost reported by the adapter.
- Integrations follow the same pattern: [ADR 0028](0028-integrations-are-capability-adapters.md).

**What 0.2 implements.** The status stays proposed: per-person and per-job runner choice, receipts carrying the
result, contract hints and the shared adapter contract-test suite are not built yet. Built, with the choices made
while building it (director's decisions, 2026-10-24):

- **Protocol** `AgentRunner.run(prompt, *, contract, tools, timeout, budget) -> AgentResult` in the kernel
  (`heinzel.kernel._agents`, public in `heinzel`), the capability `agent-runner` with one mode, `read`: a session with
  read-only tools. `ToolProfile` lists tools in the runner's own naming (empty: its read-only default) and the working
  directory; `Budget` has `max_usd`. `AgentResult` carries runner, model, status (`completed`, `timeout`, `budget`,
  `failed`), draft, tool calls by name, turns, token usage and cost, duration, why it didn't complete, and the limits
  the runner could not enforce. A timeout or budget stop is a result, never an exception; a misconfigured runner (its
  CLI missing, a tool that isn't read-only) raises a `HeinzelError`.
- **Runners are adapters like any other** (`@module.provides("agent-runner", ...)` in `system.runner`), bound with
  `[capabilities] agent_runner`, which defaults to `claude-code`: both runners ship in core, so the default is a
  setting, not a discovery. Requesters declare `agent-runner:read` like any capability.
- **Claude Code adapter**: `claude -p` with the prompt on standard input (never in the arguments), the contract as
  `--append-system-prompt`, `--output-format stream-json --verbose` so tool calls can be counted, `--tools` and
  `--allowed-tools` set to the profile, `--permission-mode dontAsk`, `--strict-mcp-config` (no MCP servers),
  `--no-session-persistence`, `--model` from `[system.runner] model` and `--max-budget-usd` from the budget. The
  timeout is the subprocess timeout. Flags were checked against `claude --help` on 2026-10-24; this Claude Code has no
  turn limit flag, so the `max_turns` hint of ADR 0021 is not enforced. A profile may only name tools listed in
  `[system.runner] read_only_tools` (default `Read`, `Grep`, `Glob`). The stream-json event shape (a `system` event with
  the model, `assistant` events with `tool_use` blocks, a final `result` event with `result`, `subtype`, `is_error`,
  `num_turns`, `total_cost_usd` and `usage`) is parsed defensively, every field optional, and was not verified against
  a real run in this change (that would spend the maintainer's quota); verify it before accepting this ADR.
- **Generic command adapter**: `[system.runner] command` is a list of arguments, run without a shell, with
  placeholders `{prompt_file}`, `{contract_file}`, `{output_file}`, `{working_dir}`, `{tools}`, `{model}`,
  `{timeout_seconds}` and `{max_usd}` filled inside single arguments; an unknown placeholder is refused. Prompt and
  contract go to files in a private temporary directory that is removed afterwards. The result is read from stdout or
  `{output_file}`, as text or as JSON (draft under a configured key, optional `usage` and `total_cost_usd`).
- **Doctor** checks the bound core runner only when some module requires `agent-runner`: Claude Code on PATH; for the
  command runner a configured, existing executable, and degraded until `command_enforces_read_only = true` records
  that the command restricts its agent itself. Results list the limits a run could not enforce.
- **Credentials and environment**: neither runner needs a credential in 0.2 (Claude Code uses its own login); both
  start their CLI with `subprocess_environment()`, so no `HZ_CREDENTIAL_*` variable reaches the agent. A command runner
  that needs a token is a follow-up through `Credentials` ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)).
- **Tests** never run an agent CLI: the runners take an injected `run` callable in unit tests, and the integration
  test drives the command runner against a fake agent written in Python. The import-linter contract "The core imports
  no vendor client or agent SDK" covers this ADR's "no agent SDK in core".
