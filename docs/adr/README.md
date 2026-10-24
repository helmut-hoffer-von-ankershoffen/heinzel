# Architecture decision records

One record per decision that shapes the code ([ADR 0001](0001-record-architecture-decisions.md)), in a trimmed
[MADR 4](https://adr.github.io/madr/) format: [0000-template.md](0000-template.md).

| ADR | Title | Status | Date |
| --- | --- | --- | --- |
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | accepted | 2026-10-23 |
| [0002](0002-modulith-with-kernel-modules-and-lets.md) | A modulith of a kernel, modules and lets | accepted | 2026-10-23 |
| [0003](0003-heads-are-views-on-operations.md) | Heads are views on head-agnostic operations | accepted | 2026-10-23 |
| [0004](0004-discovery-through-packages-and-entry-points.md) | Discovery through packages, entry points and pluggy | accepted | 2026-10-23 |
| [0005](0005-dependency-injection-by-type.md) | Dependency injection by type through ServiceLets | accepted | 2026-10-23 |
| [0006](0006-plugins-installed-through-the-uv-tool-receipt.md) | Plugins are installed through the uv tool receipt | accepted | 2026-10-23 |
| [0007](0007-plugins-scaffolded-with-copier.md) | Plugins are scaffolded with copier from this repository | accepted | 2026-10-23 |
| [0008](0008-release-from-conventional-commits.md) | Releases are derived from conventional commits | accepted | 2026-10-23 |
| [0009](0009-test-taxonomy.md) | Unit, integration and e2e tests, as in the Aignostics Python SDK | accepted | 2026-10-23 |
| [0010](0010-towards-a-reactive-workflow-engine.md) | Towards a reactive workflow engine | proposed | 2026-10-23 |
| [0011](0011-agentic-coding-standards-are-enforced-by-the-repository.md) | Agentic coding standards are enforced by the repository | accepted | 2026-10-23 |
| [0012](0012-layered-configuration.md) | Layered configuration with per-module settings | accepted | 2026-10-23 |
| [0013](0013-pull-request-based-development-and-releases.md) | Pull-request-based development and releases | accepted | 2026-10-23 |
| [0014](0014-agent-skills-and-the-claude-code-plugin.md) | Agent skills ship with Heinzelmännchen and as a Claude Code plugin | accepted | 2026-10-23 |
| [0015](0015-health-checks-as-lets.md) | Health checks are lets, aggregated by `hz system doctor` | accepted | 2026-10-23 |
| [0016](0016-name-heinzelmaennchen.md) | The project is called Heinzelmännchen; the command is `hz` | accepted | 2026-10-23 |
| [0017](0017-first-party-plugins-in-the-core-repository.md) | First-party plugins live in the core repository as workspace packages | accepted | 2026-10-23 |
| [0018](0018-one-administrative-namespace.md) | One administrative namespace: `hz system`; skills live in `agent-skills/` | accepted | 2026-10-23 |
| [0019](0019-mcp-and-rest-heads.md) | MCP and REST heads derive everything from operations | accepted | 2026-10-23 |
| [0020](0020-daemon-head.md) | Add a daemon head for background and reactive operation | proposed | 2026-10-23 |
| [0021](0021-workflow-contracts.md) | Workflows are Markdown contracts with YAML frontmatter | proposed | 2026-10-23 |
| [0022](0022-embedded-workflows.md) | Workflows embedded in documents are discovered by connectors and run only once registered | proposed | 2026-10-23 |
| [0023](0023-inventory.md) | The inventory is compiled from declared, discovered and plugin-contributed entries and published as graphs | proposed | 2026-10-23 |
| [0024](0024-people-and-org.md) | People and teams come from a directory of record, are referenced by role, and are minimised | proposed | 2026-10-23 |
| [0025](0025-plugin-distributions-are-named-heinzel-name.md) | Plugin distributions are named `heinzel-<name>` | accepted | 2026-10-23 |
| [0026](0026-plugin-api-versioned-with-semver.md) | The plugin API is versioned with SemVer, and discovery checks each plugin's compatible core range | proposed | 2026-10-23 |
| [0027](0027-nothing-is-sent-home.md) | Nothing is sent home: no telemetry, crash reports or usage pings | accepted | 2026-10-23 |
| [0028](0028-integrations-are-capability-adapters.md) | Integrations are capability adapters; workflows name capabilities, never vendors | proposed | 2026-10-23 |
| [0029](0029-agent-runners-are-capability-adapters.md) | Agent runners are capability adapters; core ships Claude Code and a generic command adapter | proposed | 2026-10-23 |

Amendments: [0025](0025-plugin-distributions-are-named-heinzel-name.md) amends
[0016](0016-name-heinzelmaennchen.md) (plugin naming); [0013](0013-pull-request-based-development-and-releases.md) amends
[0008](0008-release-from-conventional-commits.md); [0020](0020-daemon-head.md) and
[0021](0021-workflow-contracts.md) amend [0010](0010-towards-a-reactive-workflow-engine.md). ADRs 0021–0024 are
proposed together and disagree on a few points (data file format, the shared `inventory` module, canonicalization,
selector grammar); each lists them under More Information.

## Rules

- **Statuses:** `proposed` → `accepted` or `rejected`; later `deprecated` or `superseded` (with `superseded-by`).
- **Accepted records are immutable.** Only their front matter changes (`status`, `superseded-by`, `amended-by`); a
  changed decision is a new ADR that supersedes or amends the old one, with the links set on both.
- **Numbers** are sequential and never reused; files are `NNNN-title-in-kebab-case.md`.
- **This index** lists every record with the title, status and date from its file;
  `tests/unit/documentation_test.py` checks the template, this table and the links between records.
- New ADR: copy [0000-template.md](0000-template.md) or use the `write-adr` skill.

## 2026-10-23 retrofit

ADRs 0001–0020 were converted to this template on 2026-10-23. The conversion only restructured them: decisions,
statuses and dates are unchanged. "Alternatives considered" became Considered Options, "Forces" became Decision
Drivers, status annotations became `amends`/`amended-by` front matter, and each record gained a Confirmation that
names the checks that exist in the repository. Where no alternatives were written down, the record says so instead
of inventing them. Two factual corrections came with it: ADR 0004 names the pluggy hook `heinzel_lets` (renamed from
`joy_lets`), and ADR 0017, already implemented with the clock plugin (`packages/heinzel-clock`), was accepted.
