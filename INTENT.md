# Intent

<!-- badges:start -->
[![CI](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/ci.yml/badge.svg)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/helmut-hoffer-von-ankershoffen/heinzel/python-coverage-comment-action-data/badge.svg)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/tree/python-coverage-comment-action-data)
[![CodeQL](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/codeql.yml/badge.svg)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/helmut-hoffer-von-ankershoffen/heinzel/badge)](https://scorecard.dev/viewer/?uri=github.com/helmut-hoffer-von-ankershoffen/heinzel)
[![Release](https://img.shields.io/github/v/release/helmut-hoffer-von-ankershoffen/heinzel?sort=semver)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/releases)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue)](pyproject.toml)
[![Types: mypy + pyright strict](https://img.shields.io/badge/types-mypy%20%2B%20pyright%20strict-blue)](docs/CODING_STANDARDS.md#python)
[![Tooling: Ruff + uv](https://img.shields.io/badge/tooling-Ruff%20%2B%20uv-261230?logo=ruff&logoColor=d7ff64)](https://astral.sh)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
<!-- badges:end -->

## Why Heinzelmännchen exists

Product development teams don't drown in one big chore; they drown in the sum of small ones: status updates,
tracker hygiene, agendas, reports, newsletters, keeping documents consistent. Managers lose the hours they need for
people, engineers lose flow, and the people across a time zone or an org chart lose visibility.

Heinzelmännchen is an **open-source, AI-driven operating system for product development teams**. It drafts the busywork of
running a team — status, ticket updates, agendas, reports, recognition — and people approve it. It is built for
every role on a team: engineers, product managers, engineering managers and the leadership they report to.

## What Heinzelmännchen is

- **A CLI in the middle.** One `hz` command, installed on every machine, callable by people and by agents
  (Claude Code, Codex, Cursor), with `--json` on every command.
- **A modulith.** A small kernel discovers modules and the *lets* they advertise — operations, services, info
  sections — and ties them together. Heads are views on the same operations: the CLI today; an MCP server, a REST API
  and a proactive daemon next.
- **Extensible by anyone.** Teams add what they need as plugins — ordinary Python packages scaffolded from the Heinzelmännchen
  template — and good plugins get promoted to the core.
- **Proactive, eventually.** Heinzelmännchen grows toward a [Reactor](https://github.com/openprose/reactor)-style engine:
  Markdown contracts declare what a workflow requires and maintains; fingerprinted inputs decide deterministically
  when to run; an agent session decides what to do; every run leaves a receipt. See
  [ADR 0010](docs/adr/0010-towards-a-reactive-workflow-engine.md).

## Tenets

When in doubt, these decide.

1. **Joyful.** Everything we build makes work more joyful, not more managed.
2. **For all roles.** Nothing is built just for managers, just for engineers or just for product managers.
3. **Done, not nagged.** Heinzelmännchen does the work and asks for approval; it never just reminds.
4. **Humans own, with lineage.** Every artifact records its sources, its draft and its approver. Auto-approval is
   allowed; whoever switched it on owns what it approves.
5. **Composable.** Small pieces that combine, not a monolith. A kernel, modules, lets, heads.
6. **Extensible.** Teams add what they need through plugins and workflows, without forking the core.
7. **Everyone contributes.** Improving Heinzelmännchen is as easy as describing a friction point; agents turn it into a pull
   request.
8. **Open and independent.** Open source, model-provider agnostic, agent agnostic, no vendor lock-in.
9. **Consistent core, specifics as plugins.** Anything specific to a company, site or team is a plugin; the core
   stays generic and usable anywhere.
10. **Agent-ready by construction.** Typed operations, machine-readable output, documented intent and decisions, and
    standards that the repository itself enforces on every contributor, human or agent.

## Non-goals

- Heinzelmännchen is not a general-purpose chat bot, a ticket system or a document store; it drafts into the tools
  teams already use. Its chat is a view on its own operations: it reads with them, and changes something only once
  you confirm (ADR 0045).
- Heinzelmännchen does not depend on a single model provider or agent SDK.
- Heinzelmännchen does not publish anything on someone's behalf without that person's approval (or their explicit
  auto-approval).

## Where we are

0.1 is where we are; the later rows are the roadmap.

| Version | Theme | Scope |
| --- | --- | --- |
| 0.1 | Foundation | Kernel, module discovery, lets, dependency injection; CLI, MCP and REST heads; health checks and configuration; plugin lifecycle and template; first-party plugins; agent skills; fully automated releases |
| 0.2 | Works for me while I'm away | The daemon on my machine ([ADR 0020](docs/adr/0020-daemon-head.md)); personal workflow contracts ([ADR 0021](docs/adr/0021-workflow-contracts.md)); the `AgentRunner` with Claude Code and a generic command adapter ([ADR 0029](docs/adr/0029-agent-runners-are-capability-adapters.md)); capability protocols ([ADR 0028](docs/adr/0028-integrations-are-capability-adapters.md)) with first-party GitHub, Atlassian and Google Workspace adapters that go through the vendors' CLIs as an ordinary employee ([ADR 0035](docs/adr/0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md)) and a Slack adapter, meetings arriving as summary e-mails; credentials in the keychain for services without a CLI; releases on PyPI, an update notifier and team pinning; nothing is sent home ([ADR 0027](docs/adr/0027-nothing-is-sent-home.md)) |
| 0.3 | Works for my team | Shared state and approvals in GitHub, the GitHub Actions team runner ([ADR 0020](docs/adr/0020-daemon-head.md)); people and teams ([ADR 0024](docs/adr/0024-people-and-org.md)); publishing after approval; the first management rituals |
| 0.4 | Lives in our documents | Embedded workflows ([ADR 0022](docs/adr/0022-embedded-workflows.md)); the inventory and its graphs ([ADR 0023](docs/adr/0023-inventory.md)); knowledge extracted from meeting transcripts; workflows that improve themselves |
| 0.5 | Anywhere | A remote MCP connector for mobile and voice; sandboxing; a GitHub App; Microsoft 365 and Notion adapters |
| 1.0 | Stable | A stable plugin API ([ADR 0026](docs/adr/0026-plugin-api-versioned-with-semver.md)) |

**Thin slice first.** 0.2 is validated with one slice before anything else is built on it: the weekly report
draft. One contract (the draft is ready Friday 17:00), real inputs through the vendor CLIs (`gh`, `acli`, `gws`), and
a local draft the manager edits and sends themselves. No approvals, no publishing and no people model are needed for
it. A contract is an invariant about one asset; the overall workflow emerges from the graph of contracts
([ADR 0021](docs/adr/0021-workflow-contracts.md)). The core knows nothing about weekly reports: the slice is a
playbook a plugin brings. Success test after three Fridays: time saved, and how much of the draft was changed.

Decisions that shape the code are recorded as [ADRs](docs/adr/).
