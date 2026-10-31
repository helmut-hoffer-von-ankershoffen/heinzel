# Inten

<!-- badges:start -->
[![CI](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/ci.yml/badge.svg)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/helmut-hoffer-von-ankershoffen/heinzel/python-coverage-comment-action-data/badge.svg)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/tree/python-coverage-comment-action-data)
[![CodeQL](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/codeql.yml/badge.svg)](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/helmut-hoffer-von-ankershoffen/heinzel/badge)](https://scorecard.dev/viewer/?uri=github.com/helmut-hoffer-von-ankershoffen/heinzel)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15863/badge)](https://www.bestpractices.dev/en/projects/15863/passing)
[![PyPI](https://img.shields.io/pypi/v/heinzel)](https://pypi.org/project/heinzel/)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue)](pyproject.toml)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=helmut-hoffer-von-ankershoffen_heinzel&metric=alert_status)](https://sonarcloud.io/summary/overall?id=helmut-hoffer-von-ankershoffen_heinzel)
[![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=helmut-hoffer-von-ankershoffen_heinzel&metric=security_rating)](https://sonarcloud.io/summary/overall?id=helmut-hoffer-von-ankershoffen_heinzel)
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
  sections — and ties them together. Heads are views on the same operations: the CLI, a web app, an MCP server, a REST API
  and a daemon.
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
11. **Perfect onboarding.** Joining is one line posted in chat; hz finds out who you are from the logins you already
    have, and the first thing it does for you is a win ([ADR 0080](docs/adr/0080-one-invite-line-and-a-reviewed-first-win.md)).

## Non-goals

- Heinzelmännchen is not a general-purpose chat bot, a ticket system or a document store; it drafts into the tools
  teams already use. Its chat is a view on its own operations: it reads with them, and changes something only once
  you confirm (ADR 0045).
- Heinzelmännchen does not depend on a single model provider or agent SDK.
- Heinzelmännchen does not publish anything on someone's behalf without that person's approval (or their explici
  auto-approval).

## Where we are

**Released.** Today you get:

- The kernel: modules, lets and dependency injection.
- Five heads on the same operations: CLI, web, MCP, REST and the daemon.
- Contracts that draft locally on a schedule, with a cost cap.
- Named helpers, each with its own chat.
- Team repositories and the contract graph.
- Plugins with their own releases.
- Themes and soundscapes, the terminal, and a paired phone view.

**Not built yet.**

- Event wakes: a contract running because an input changed.
- Writes into shared documents, and publishing.
- Approval in place, where the draft lives.
- Lessons from your edits feeding back into a contract.
- Auto-approval.
- The team sink.
- Helpers working together.
- An always-on runner.

No end-to-end journey has been proven with a live provider yet.

**Thin slice first.** The first slice is one contract: the weekly report draft, ready Friday 17:00. It reads real
inputs through the vendor CLIs (`gh`, `acli`, `gws`) and leaves a local draft that the manager edits and sends. I
needs no approvals, no publishing and no people model. A contract is an invariant about one asset; the overall
workflow emerges from the graph of contracts ([ADR 0021](docs/adr/0021-workflow-contracts.md)). The core knows nothing
about weekly reports: the slice is a playbook a plugin brings. Success test after three Fridays: time saved, and how
much of the draft was changed.

Decisions that shape the code are recorded as [ADRs](docs/adr/).
