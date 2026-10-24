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
| [0030](0030-credentials-live-in-the-os-keychain.md) | Credentials live in the OS keychain behind a credentials service | accepted | 2026-10-24 |
| [0031](0031-update-check.md) | The CLI checks for a newer release hourly, anonymously and in the background, and says so on stderr | accepted | 2026-10-24 |
| [0032](0032-modules-declare-interactive-setup-steps.md) | Modules declare interactive setup steps; `hz setup` runs them all | proposed | 2026-10-24 |
| [0033](0033-github-rest-api-over-urllib.md) | Read GitHub through its REST API with urllib; merged pull requests from the pulls list | accepted | 2026-10-24 |
| [0034](0034-atlassian-cloud-rest-apis-over-urllib.md) | Read Jira and Confluence Cloud through their REST APIs with urllib, Basic auth and epoch-millisecond JQL | accepted | 2026-10-24 |
| [0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md) | Integrations go through the vendor's CLI as an ordinary employee | proposed | 2026-10-24 |
| [0036](0036-the-build-installs-its-own-tools.md) | `make install` installs every tool the build needs, pinned and checksummed, on macOS and Linux | proposed | 2026-10-24 |
| [0037](0037-plugins-bring-playbooks.md) | Plugins bring playbooks: asset types with discovery hints, template contracts and recommended settings | proposed | 2026-10-24 |
| [0038](0038-drafts-are-approved-in-place.md) | Drafts are approved in place | proposed | 2026-10-24 |
| [0040](0040-layers-and-playbooks-are-plugins.md) | Layers and playbooks are plugins: a plugin may be a layer with a parent and a site, and layers stack general to specific | proposed | 2026-10-24 |
| [0041](0041-perfect-onboarding-one-invite-line-joins-a-team.md) | Perfect onboarding: one invite line joins a team, identity is discovered, and joining ends with a first win | proposed | 2026-10-24 |

Amendments: [0025](0025-plugin-distributions-are-named-heinzel-name.md) amends
[0016](0016-name-heinzelmaennchen.md) (plugin naming); [0013](0013-pull-request-based-development-and-releases.md) amends
[0008](0008-release-from-conventional-commits.md); [0020](0020-daemon-head.md) and
[0021](0021-workflow-contracts.md) amend [0010](0010-towards-a-reactive-workflow-engine.md);
[0032](0032-modules-declare-interactive-setup-steps.md) amends [0018](0018-one-administrative-namespace.md) (`hz setup`);
[0028](0028-integrations-are-capability-adapters.md) amends [0024](0024-people-and-org.md) (the directory is a
capability); [0038](0038-drafts-are-approved-in-place.md) amends [0010](0010-towards-a-reactive-workflow-engine.md) and
[0020](0020-daemon-head.md) (approval in place instead of an approval queue; `hz inbox` is a view);
[0041](0041-perfect-onboarding-one-invite-line-joins-a-team.md) amends [0018](0018-one-administrative-namespace.md)
(`hz join`) and [0024](0024-people-and-org.md) (the roster hz drafts and the manager approves, matching by login, YAML
data files). ADRs 0021–0024 are
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
`joy_lets`), and ADR 0017, already implemented with the clock plugin (`plugins/heinzel-clock`), was accepted.
