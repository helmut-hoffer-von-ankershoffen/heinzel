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
