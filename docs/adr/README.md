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
| [0033](0033-github-rest-api-over-urllib.md) | Read GitHub through its REST API with urllib; merged pull requests from the pulls list | superseded | 2026-10-24 |
| [0034](0034-atlassian-cloud-rest-apis-over-urllib.md) | Read Jira and Confluence Cloud through their REST APIs with urllib, Basic auth and epoch-millisecond JQL | superseded | 2026-10-24 |
| [0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md) | Integrations go through the vendor's CLI as an ordinary employee | accepted | 2026-10-24 |
| [0036](0036-the-build-installs-its-own-tools.md) | `make install` installs every tool the build needs, pinned and checksummed, on macOS and Linux | proposed | 2026-10-24 |
| [0037](0037-plugins-bring-playbooks.md) | Plugins bring playbooks: asset types with discovery hints, template contracts and recommended settings | proposed | 2026-10-24 |
| [0038](0038-drafts-are-approved-in-place.md) | Drafts are approved in place | proposed | 2026-10-24 |
| [0039](0039-a-web-head-a-react-app-served-by-the-api-head.md) | A web head: a React app served by the API head | proposed | 2026-10-24 |
| [0040](0040-layers-and-playbooks-are-plugins.md) | Layers and playbooks are plugins: a plugin may be a layer with a parent and a site, and layers stack general to specific | proposed | 2026-10-24 |
| [0041](0041-expert-mode-newcomers-see-the-everyday-commands.md) | Expert mode: newcomers see the everyday commands, `hz system expert on` shows all | proposed | 2026-10-24 |
| [0042](0042-google-workspace-through-the-gws-cli.md) | Read Google Workspace through the Google Workspace CLI (`gws`) | accepted | 2026-10-24 |
| [0043](0043-desktop-notifications-one-policy-shown-once.md) | Desktop notifications: one policy in the core, shown once, by hz's own macOS app | proposed | 2026-10-24 |
| [0044](0044-themes-are-data-a-plugin-ships.md) | Themes are data a plugin ships: `module.theme(...)` offers them, `hz system theme use` picks one | proposed | 2026-10-24 |
| [0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md) | A chat that knows where you are and acts through hz, with every change confirmed in code | proposed | 2026-10-24 |
| [0048](0048-a-terminal-in-the-web-head.md) | A terminal in the web head | proposed | 2026-10-24 |
| [0046](0046-an-installable-web-app-with-paired-devices.md) | An installable web app on a fixed port, with paired devices and notifications from its service worker | proposed | 2026-10-24 |
| [0047](0047-plugins-are-managed-from-the-web-app-after-a-confirmation.md) | Plugins are managed from the web app, and adding one shows what it installs before it is confirmed | proposed | 2026-10-24 |
| [0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md) | Heinzelmännchen are durable agents: one session each, owned by hz | proposed | 2026-10-24 |
| [0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md) | Pydantic AI runs a turn; a coding agent is a handoff | proposed | 2026-10-24 |
| [0051](0051-the-workflow-module-becomes-the-heinzel-module.md) | The workflow module becomes the heinzel module | accepted | 2026-10-24 |
| [0052](0052-the-graph-emerges-from-recorded-reads-and-writes.md) | The graph emerges from recorded reads and writes | proposed | 2026-10-24 |
| [0053](0053-the-team-graph-is-drawn-with-react-flow-and-elk.md) | The team graph is drawn with React Flow and laid out by elk; Mermaid is only an export | proposed | 2026-10-24 |
| [0054](0054-observability-is-whether-each-team-promise-is-kept.md) | Observability is whether each team promise is kept: telemetry in, a graph out | proposed | 2026-10-24 |
| [0055](0055-connectors-show-their-sign-in-test-live-on-demand-and-bind-themselves-when-alone.md) | Connectors show their sign-in, test live on demand, and bind themselves when they are alone | accepted | 2026-10-24 |
| [0056](0056-modules-contribute-status-items-actions-and-widgets-as-data.md) | Modules contribute status items, header actions and Today widgets as data, viewed from their own operations | proposed | 2026-10-24 |
| [0057](0057-ai-access-is-a-module-one-choice-like-the-theme.md) | AI access is a module: one choice like the theme, options are adapters of `ai-access` | proposed | 2026-10-24 |
| [0058](0058-soundscapes-are-data-a-plugin-ships-chosen-like-a-theme.md) | Soundscapes are data a plugin ships, chosen like a theme: `module.soundscape(...)`, `hz system sound use` | proposed | 2026-10-24 |
| [0059](0059-the-calendar-and-the-mail-write-through-hz-after-a-confirmation.md) | The calendar and the mail write through hz, after a confirmation | proposed | 2026-10-24 |
| [0060](0060-themes-follow-the-time-of-day-and-the-weather.md) | Themes follow the time of day and the weather: variants on a closed vocabulary of signals | proposed | 2026-10-24 |
| [0062](0062-heinzelmaennchen-find-the-mcp-servers-and-skills-you-gave-claude.md) | Heinzelmännchen find the MCP servers and skills you already gave Claude, read-only first | proposed | 2026-10-25 |
| [0061](0061-plugins-ship-skills-every-ai-path-can-use.md) | Plugins ship skills every AI path can use: `module.skill(...)`, read-only tools, an explicit export | proposed | 2026-10-24 |
| [0065](0065-plugins-may-ship-versioned-react-views.md) | Plugins may ship versioned React views alongside their Python declarations | proposed | 2026-10-25 |

Amendments: [0025](0025-plugin-distributions-are-named-heinzel-name.md) amends
[0016](0016-name-heinzelmaennchen.md) (plugin naming); [0013](0013-pull-request-based-development-and-releases.md) amends
[0008](0008-release-from-conventional-commits.md); [0020](0020-daemon-head.md) and
[0021](0021-workflow-contracts.md) amend [0010](0010-towards-a-reactive-workflow-engine.md);
[0032](0032-modules-declare-interactive-setup-steps.md) amends [0018](0018-one-administrative-namespace.md) (`hz setup`);
[0028](0028-integrations-are-capability-adapters.md) amends [0024](0024-people-and-org.md) (the directory is a
capability); [0038](0038-drafts-are-approved-in-place.md) amends [0010](0010-towards-a-reactive-workflow-engine.md) and
[0020](0020-daemon-head.md) (approval in place instead of an approval queue; `hz inbox` is a view);
[0039](0039-a-web-head-a-react-app-served-by-the-api-head.md) amends [0018](0018-one-administrative-namespace.md)
(`hz ui`) and [0019](0019-mcp-and-rest-heads.md) (a token-protected web head may expose what the unauthenticated REST
head doesn't); [0041](0041-expert-mode-newcomers-see-the-everyday-commands.md) amends
[0018](0018-one-administrative-namespace.md) (`hz system expert`; help lists the everyday commands) and
[0039](0039-a-web-head-a-react-app-served-by-the-api-head.md) (the navigation lists the same ones);
[0043](0043-desktop-notifications-one-policy-shown-once.md) amends [0020](0020-daemon-head.md) (every tick ends by
popping up due notices) and [0039](0039-a-web-head-a-react-app-served-by-the-api-head.md) (the notify operations are
web endpoints, not REST ones); [0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md) amends
[0018](0018-one-administrative-namespace.md) (`hz chat`), [0029](0029-agent-runners-are-capability-adapters.md)
(hz operations as agent tools through the MCP head) and [0039](0039-a-web-head-a-react-app-served-by-the-api-head.md)
(chat sessions instead of the stateless resend); [0048](0048-a-terminal-in-the-web-head.md) amends
[0039](0039-a-web-head-a-react-app-served-by-the-api-head.md) (a terminal over a WebSocket; inline styles allowed);
[0046](0046-an-installable-web-app-with-paired-devices.md) amends
[0039](0039-a-web-head-a-react-app-served-by-the-api-head.md) (a fixed port and paired devices) and
[0043](0043-desktop-notifications-one-policy-shown-once.md) (notifications in the web app);
[0047](0047-plugins-are-managed-from-the-web-app-after-a-confirmation.md) amends
[0006](0006-plugins-installed-through-the-uv-tool-receipt.md) (`add` shows first and installs on `--confirm`, from PyPI
or https git only) and [0039](0039-a-web-head-a-react-app-served-by-the-api-head.md) (plugin changes are web endpoints,
not REST ones; `POST /api/restart`);
[0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md) amends
[0010](0010-towards-a-reactive-workflow-engine.md) (a turn of a continuous session instead of a session per render),
[0020](0020-daemon-head.md) (the tick wakes Heinzelmännchen by key; team state on `hz/state` with leases),
[0021](0021-workflow-contracts.md) (one contract per Heinzelmännchen, in versions; records become turn bodies),
[0029](0029-agent-runners-are-capability-adapters.md) (the runner no longer owns the session),
[0038](0038-drafts-are-approved-in-place.md) (lessons; `hz drafts` and `hz approve`) and
[0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md) (the sidebar chat with a Heinzelmännchen is its
session), and in its durable-turns amendment [0028](0028-integrations-are-capability-adapters.md) (fingerprints per facet) and
[0052](0052-the-graph-emerges-from-recorded-reads-and-writes.md) (downstream wakes only when the approved output
moved); [0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md) amends
[0029](0029-agent-runners-are-capability-adapters.md) (Pydantic AI in the core as the turn engine; runners are the
handoff) and [0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md) (the chat on the same engine);
[0051](0051-the-workflow-module-becomes-the-heinzel-module.md) amends [0021](0021-workflow-contracts.md) (operation
names), [0038](0038-drafts-are-approved-in-place.md) (`hz inbox` becomes `hz drafts` and `hz approve`),
[0041](0041-expert-mode-newcomers-see-the-everyday-commands.md) (the everyday set) and
[0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md) (`heinzel.*` settings in the never-run policy);
[0052](0052-the-graph-emerges-from-recorded-reads-and-writes.md) amends [0021](0021-workflow-contracts.md) (observed
edges beside declared ones), [0023](0023-inventory.md) (no entity schema or single-writer rule for now),
[0038](0038-drafts-are-approved-in-place.md) (repeated nudges and the fallback, proposed) and
[0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md) (receipt-grade events are action
entries; the `report` tool); [0054](0054-observability-is-whether-each-team-promise-is-kept.md) amends
[0037](0037-plugins-bring-playbooks.md) (the core's graph module ships the graph keeper's playbook) and
[0052](0052-the-graph-emerges-from-recorded-reads-and-writes.md) (telemetry logs in the team repository before the
spine; `hz report`); [0055](0055-connectors-show-their-sign-in-test-live-on-demand-and-bind-themselves-when-alone.md)
amends [0028](0028-integrations-are-capability-adapters.md) (a sole installed adapter serves its capability
automatically);
[0057](0057-ai-access-is-a-module-one-choice-like-the-theme.md) amends
[0029](0029-agent-runners-are-capability-adapters.md) (an AI access option names the agent runner that serves it) and
[0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md) (`[heinzel] model` gives way to `[ai]` routing);
[0058](0058-soundscapes-are-data-a-plugin-ships-chosen-like-a-theme.md) amends
[0027](0027-nothing-is-sent-home.md), [0039](0039-a-web-head-a-react-app-served-by-the-api-head.md) and
[0046](0046-an-installable-web-app-with-paired-devices.md) (one allowlisted frame origin while a stream soundscape is in
effect, from Play onwards);
[0060](0060-themes-follow-the-time-of-day-and-the-weather.md) amends [0044](0044-themes-are-data-a-plugin-ships.md)
(variants that follow signals; a base theme is checked for contrast too);
[0059](0059-the-calendar-and-the-mail-write-through-hz-after-a-confirmation.md) amends
[0028](0028-integrations-are-capability-adapters.md) (a `write` mode and three reads for `calendar` and `mail`),
[0038](0038-drafts-are-approved-in-place.md) (no `place_draft` for them: a mail draft is placed in the mailbox, a
calendar write is confirmed) and [0042](0042-google-workspace-through-the-gws-cli.md) (writes and their scopes).
[0061](0061-plugins-ship-skills-every-ai-path-can-use.md) amends
[0014](0014-agent-skills-and-the-claude-code-plugin.md) (plugin skills reach Claude Code through `hz system skill
export`) and [0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md) (skills are `SkillLet`s; a turn gets
an index and loads bodies on demand).
ADRs 0021–0024 are
proposed together and disagree on a few points (data file format, the shared `inventory` module, canonicalization,
selector grammar); each lists them under More Information.

Supersessions: [0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md) supersedes
[0033](0033-github-rest-api-over-urllib.md) and [0034](0034-atlassian-cloud-rest-apis-over-urllib.md) (vendor CLIs
instead of REST over urllib with tokens).

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
