---
status: proposed
date: 2026-10-23
implementation: built
related: 0020, 0021, 0028
amended-by: 0045, 0049, 0050, 0057
implemented-by: 9
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

Partly, for the part built in the first release (see the implementation status under More Information): the command line, the
isolation flags, the environment and the parsing of the Claude Code adapter with a recorded fake
(`tests/unit/modules/runner_claude_test.py`); the command adapter's fail-closed checks, placeholders, environment
allowlist and output handling (`tests/unit/modules/runner_command_test.py`) and its run against a fake agent CLI
written in Python (`tests/integration/runner_test.py`); process groups, timeouts and output caps with real processes
(`tests/integration/runner_process_test.py`); a hostile project `hz.toml` can't configure execution
(`tests/integration/capability_test.py`); the import-linter contract "The core imports no vendor client or agent SDK"
(`pyproject.toml`). No test runs a real agent. Still planned: a contract-test suite every `AgentRunner` adapter
passes (timeouts, budgets, read-only profile, result shape), and verifying the stream-json shape against a real
Claude Code run.

## More Information

- [ADR 0021](0021-workflow-contracts.md) lists an `agent` key under `hz:`, and its research
  ([docs/research/workflow-contracts.md](../research/workflow-contracts.md)) fixes `hz.agent.runner` to the enum
  `any | claude-code | codex | cursor`. Under this ADR the runner is not an enum in the contract format: which
  runner executes a job is configuration, and `hz.agent` keeps hints only (model class, effort, turn limit), with
  `runner` at most an optional hint naming a configured runner. ADR 0021 is proposed and should be aligned before
  either is accepted. Aligned for the first release: the workflow module maps `hz.agent.tools`,
  `hz.agent.max_usd` and `hz.timeout` to the `ToolProfile`, `Budget` and timeout of `AgentRunner.run`, and refuses
  `runner`, `model`, `effort` and `max_turns` until a runner can honour them, since the protocol has no parameter for
  them and a hint nobody reads would mislead.
- [ADR 0020](0020-daemon-head.md) receipts gain the runner, model, tool calls and token cost reported by the adapter.
- Integrations follow the same pattern: [ADR 0028](0028-integrations-are-capability-adapters.md).

**What the first release implements.** The status stays proposed: per-person and per-job runner choice, receipts carrying the
result, contract hints and the shared adapter contract-test suite are not built yet. Built, with the choices made
while building it:

- **Protocol** `AgentRunner.run(prompt, *, contract, tools, timeout, budget) -> AgentResult` in the kernel
  (`heinzel.kernel._agents`, public in `heinzel`), the capability `agent-runner` with one mode, `read`: a session with
  read-only tools. `ToolProfile` lists tools in the runner's own naming (empty: its read-only default) and the working
  directory, which must exist; `Budget` has `max_usd`. `AgentResult` carries runner, model, status (`completed`,
  `timeout`, `budget`, `failed`), draft, tool calls by name, turns, token usage and cost, duration, why it didn't
  complete, and the limits the runner could not enforce. A timeout or budget stop is a result, never an exception; so
  are a failing CLI and missing or empty output (status `failed`, with the exit code and the end of stderr). A
  misconfigured runner raises a `HeinzelError`.
- *Amended by [ADR 0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md): a chat's agent gets hz's
  operations as tools through the MCP head (`ToolProfile.mcp_servers`), reads only, with every other call held for
  the person's confirmation; such a Claude Code run uses `--setting-sources ""` instead of `--safe-mode`, and doctor
  also asks `claude auth status`.*
- **Tools in the first release: inputs are pre-fetched, the agent gets no hz tools.** This settles ADR 0021's tools question for
  the first release (option a): a render packet holds the inputs, fetched through capability handles by the job before the session
  starts, and the agent session only reads the working directory with the runner's read-only tools. `hz.tools`
  (hz operations and MCP servers as agent tools) is deferred, and `ToolProfile` stays in the runner's naming until it
  comes.
- **Runners are adapters like any other** (`@module.provides("agent-runner", ...)` in `system.runner`), bound with
  `[capabilities] agent_runner`, which defaults to `claude-code` because `command` needs a command line first.
  Requesters declare `agent-runner:read` like any capability. What runs (`claude_executable`, `read_only_tools`, every
  `command*` setting) comes from the site, user or `HZ_CONFIG` file or the environment, never from a project
  `hz.toml`; executables are resolved to absolute paths before the run changes directory.
- **Processes are bounded.** Both runners start the agent in its own session (`start_new_session`); past the timeout
  the whole process group gets SIGTERM, then SIGKILL two seconds later, and whatever the agent left running in its
  group is killed when it exits. Stdout and stderr are drained and capped (8 MiB each).
- **Claude Code adapter**: `claude -p` with the prompt on standard input and the contract in a file in a private
  temporary directory (`--append-system-prompt-file`), so neither shows in the process list; `--output-format
  stream-json --verbose` so tool calls can be counted. It does not load the user's or the project's Claude setup as
  far as this Claude Code offers (flags checked against `claude --help` of Claude Code 2.1, 2026-10-24):
  `--safe-mode` (no CLAUDE.md, skills, plugins, hooks, MCP servers, custom commands or agents), `--restricted` (no
  user, project or local settings files, so none of their hooks, `env` or `apiKeyHelper`; no tools that run commands
  or code unless named; file tools confined to the working directory), `--disable-slash-commands`,
  `--strict-mcp-config`, `--no-session-persistence`, and `--tools` and `--allowed-tools` set to the profile with
  `--permission-mode dontAsk`. Managed (policy) settings still apply, and Claude Code still reads its own login from
  the keychain: `--bare` would stop that too but allows only API keys, which breaks OAuth logins. The environment is
  Heinzelmännchen's without `HZ_CREDENTIAL_*`, plus `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` and
  `DISABLE_AUTOUPDATER=1`. `--model` comes from `[system.runner] model`, `--max-budget-usd` from the budget. This
  Claude Code has no turn limit flag, so the `max_turns` hint of ADR 0021 is not enforced. A profile may only name
  tools listed in `[system.runner] read_only_tools` (default `Read`, `Grep`, `Glob`). The stream-json event shape
  (a `system` event with the model, `assistant` events with `tool_use` blocks, a final `result` event with `result`,
  `subtype`, `is_error`, `num_turns`, `total_cost_usd` and `usage`) is parsed defensively, every field optional, and
  was not verified against a real run in this change (that would spend the maintainer's quota).
- **Generic command adapter, fail closed**: it refuses to run until the user sets `command_enforces_read_only = true`
  (the command restricts its agent itself; Heinzelmännchen can't check that), and passes only tools listed in
  `command_read_only_tools`. `[system.runner] command` is a list of arguments, run without a shell, with placeholders
  `{prompt_file}`, `{contract_file}`, `{output_file}`, `{working_dir}`, `{tools}`, `{model}`, `{timeout_seconds}` and
  `{max_usd}` filled inside single arguments (`{{` and `}}` are literal braces); unknown placeholders, format specs and
  conversions are refused. Prompt and contract go to files in a private temporary directory that is removed
  afterwards. The environment is an allowlist: PATH, HOME, LANG, LC_*, TMPDIR and the names in `command_env`, never
  `HZ_CREDENTIAL_*`. The result is read from stdout or `{output_file}` (opened without following symlinks, at most
  8 MiB), as text or as JSON (draft under a configured key, optional `usage` and `total_cost_usd`).
- **Doctor** checks the bound core runner only when some module requires `agent-runner`: Claude Code on PATH; for the
  command runner a configured, existing executable, and degraded while it refuses to run. Results list the limits a
  run could not enforce (a budget the command doesn't take).
- **Credentials**: neither runner needs a credential in the first release (Claude Code uses its own login; a command reads its own
  from a variable named in `command_env`). A command runner that needs a stored token is a follow-up through
  `Credentials` ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)).
