# Architecture decision records

One record per decision that shapes the code ([ADR 0001](0001-record-architecture-decisions.md)), in a trimmed
[MADR 4](https://adr.github.io/madr/) format: [0000-template.md](0000-template.md).

Status says whether we agreed. The second line of the Status column says whether it runs: built, partial or planned.

<!-- adr-index:start -->
<div class="adr-index" markdown="1">

| ADR | Status | Pull requests |
| --- | --- | --- |
| [0001](0001-record-architecture-decisions.md) Record architecture decisions | accepted · 23 Oct<br>built |  |
| [0002](0002-modulith-with-kernel-modules-and-lets.md) A modulith of a kernel, modules and lets | accepted · 23 Oct<br>built |  |
| [0003](0003-heads-are-views-on-operations.md) Heads are views on head-agnostic operations | accepted · 23 Oct<br>built |  |
| [0004](0004-discovery-through-packages-and-entry-points.md) Discovery through packages, entry points and pluggy | accepted · 23 Oct<br>built |  |
| [0005](0005-dependency-injection-by-type.md) Dependency injection by type through ServiceLets | accepted · 23 Oct<br>built |  |
| [0006](0006-plugins-installed-through-the-uv-tool-receipt.md) Plugins are installed through the uv tool receipt | accepted · 23 Oct<br>built |  |
| [0007](0007-plugins-scaffolded-with-copier.md) Plugins are scaffolded with copier from this repository | accepted · 23 Oct<br>built |  |
| [0008](0008-release-from-conventional-commits.md) Releases are derived from conventional commits | accepted · 23 Oct<br>built |  |
| [0009](0009-test-taxonomy.md) Unit, integration and e2e tests, following a public SDK's test taxonomy | accepted · 23 Oct<br>built |  |
| [0010](0010-towards-a-reactive-workflow-engine.md) Towards a reactive workflow engine | proposed · 23 Oct<br>partial |  |
| [0011](0011-agentic-coding-standards-are-enforced-by-the-repository.md) Agentic coding standards are enforced by the repository | accepted · 23 Oct<br>built |  |
| [0012](0012-layered-configuration.md) Layered configuration with per-module settings | accepted · 23 Oct<br>built |  |
| [0013](0013-pull-request-based-development-and-releases.md) Pull-request-based development and releases | accepted · 23 Oct<br>built |  |
| [0014](0014-agent-skills-and-the-claude-code-plugin.md) Agent skills ship with Heinzelmännchen and as a Claude Code plugin | accepted · 23 Oct<br>built |  |
| [0015](0015-health-checks-as-lets.md) Health checks are lets, aggregated by `hz system doctor` | accepted · 23 Oct<br>built |  |
| [0016](0016-name-heinzelmaennchen.md) The project is called Heinzelmännchen; the command is `hz` | accepted · 23 Oct<br>built |  |
| [0017](0017-first-party-plugins-in-the-core-repository.md) First-party plugins live in the core repository as workspace packages | accepted · 23 Oct<br>partial | [#35](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/35) |
| [0018](0018-one-administrative-namespace.md) One administrative namespace: `hz system`; skills live in `agent-skills/` | accepted · 23 Oct<br>built |  |
| [0019](0019-mcp-and-rest-heads.md) MCP and REST heads derive everything from operations | accepted · 23 Oct<br>built |  |
| [0020](0020-daemon-head.md) Add a daemon head for background and reactive operation | proposed · 23 Oct<br>partial | [#7](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/7) |
| [0021](0021-workflow-contracts.md) Workflows are Markdown contracts with YAML frontmatter | proposed · 23 Oct<br>partial | [#11](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/11) |
| [0022](0022-embedded-workflows.md) Workflows embedded in documents are discovered by connectors and run only once registered | proposed · 23 Oct<br>planned |  |
| [0023](0023-inventory.md) The inventory is compiled from declared, discovered and plugin-contributed entries and published as graphs | proposed · 23 Oct<br>planned |  |
| [0024](0024-people-and-org.md) People and teams come from a directory of record, are referenced by role, and are minimised | proposed · 23 Oct<br>partial | [#45](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/45) |
| [0025](0025-plugin-distributions-are-named-heinzel-name.md) Plugin distributions are named `heinzel-<name>` | accepted · 23 Oct<br>built |  |
| [0026](0026-plugin-api-versioned-with-semver.md) The plugin API is versioned with SemVer, and discovery checks each plugin's compatible core range | proposed · 23 Oct<br>partial |  |
| [0027](0027-nothing-is-sent-home.md) Nothing is sent home: no telemetry, crash reports or usage pings | accepted · 23 Oct<br>built |  |
| [0028](0028-integrations-are-capability-adapters.md) Integrations are capability adapters; workflows name capabilities, never vendors | proposed · 23 Oct<br>built | [#9](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/9) |
| [0029](0029-agent-runners-are-capability-adapters.md) Agent runners are capability adapters; core ships Claude Code and a generic command adapter | proposed · 23 Oct<br>built | [#9](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/9) |
| [0030](0030-credentials-live-in-the-os-keychain.md) Credentials live in the OS keychain behind a credentials service | accepted · 24 Oct<br>built | [#6](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/6) |
| [0031](0031-update-check.md) The CLI checks for a newer release hourly, anonymously and in the background, and says so on stderr | accepted · 24 Oct<br>built | [#8](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/8), [#21](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/21) |
| [0032](0032-modules-declare-interactive-setup-steps.md) Modules declare interactive setup steps; `hz setup` runs them all | proposed · 24 Oct<br>built | [#26](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/26) |
| [0033](0033-github-rest-api-over-urllib.md) Read GitHub through its REST API with urllib; merged pull requests from the pulls list | superseded · 24 Oct<br>built | [#35](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/35) |
| [0034](0034-atlassian-cloud-rest-apis-over-urllib.md) Read Jira and Confluence Cloud through their REST APIs with urllib, Basic auth and epoch-millisecond JQL | superseded · 24 Oct<br>built | [#35](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/35) |
| [0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md) Integrations go through the vendor's CLI as an ordinary employee | accepted · 24 Oct<br>built | [#40](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/40), [#49](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/49) |
| [0036](0036-the-build-installs-its-own-tools.md) `make install` installs every tool the build needs, pinned and checksummed, on macOS and Linux | proposed · 24 Oct<br>built | [#43](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/43) |
| [0037](0037-plugins-bring-playbooks.md) Plugins bring playbooks: asset types with discovery hints, template contracts and recommended settings | proposed · 24 Oct<br>built | [#38](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/38) |
| [0038](0038-drafts-are-approved-in-place.md) Drafts are approved in place | proposed · 24 Oct<br>planned |  |
| [0039](0039-a-web-head-a-react-app-served-by-the-api-head.md) A web head: a React app served by the API head | proposed · 24 Oct<br>built | [#60](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/60) |
| [0040](0040-layers-and-playbooks-are-plugins.md) Layers and playbooks are plugins: a plugin may be a layer with a parent and a site, and layers stack general to specific | proposed · 24 Oct<br>partial | [#61](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/61) |
| [0041](0041-expert-mode-newcomers-see-the-everyday-commands.md) Expert mode: newcomers see the everyday commands, `hz system expert on` shows all | proposed · 24 Oct<br>built | [#65](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/65) |
| [0042](0042-google-workspace-through-the-gws-cli.md) Read Google Workspace through the Google Workspace CLI (`gws`) | accepted · 24 Oct<br>built | [#47](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/47) |
| [0043](0043-desktop-notifications-one-policy-shown-once.md) Desktop notifications: one policy in the core, shown once, by hz's own macOS app | proposed · 24 Oct<br>built | [#67](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/67) |
| [0044](0044-themes-are-data-a-plugin-ships.md) Themes are data a plugin ships: `module.theme(...)` offers them, `hz system theme use` picks one | proposed · 24 Oct<br>built | [#70](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/70) |
| [0045](0045-a-chat-that-acts-through-hz-with-confirmation-in-code.md) A chat that knows where you are and acts through hz, with every change confirmed in code | proposed · 24 Oct<br>built | [#71](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/71) |
| [0046](0046-an-installable-web-app-with-paired-devices.md) An installable web app on a fixed port, with paired devices and notifications from its service worker | proposed · 24 Oct<br>built | [#72](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/72) |
| [0047](0047-plugins-are-managed-from-the-web-app-after-a-confirmation.md) Plugins are managed from the web app, and adding one shows what it installs before it is confirmed | proposed · 24 Oct<br>built | [#74](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/74) |
| [0048](0048-a-terminal-in-the-web-head.md) A terminal in the web head | proposed · 24 Oct<br>built | [#68](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/68) |
| [0049](0049-heinzelmaennchen-are-durable-agents-one-session-each-owned-by-hz.md) Heinzelmännchen are durable agents: one session each, owned by hz | proposed · 24 Oct<br>partial | [#165](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/165), [#167](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/167), [#263](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/263) |
| [0050](0050-pydantic-ai-runs-a-turn-a-coding-agent-is-a-handoff.md) Pydantic AI runs a turn; a coding agent is a handoff | proposed · 24 Oct<br>built | [#167](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/167) |
| [0051](0051-the-workflow-module-becomes-the-heinzel-module.md) The workflow module becomes the heinzel module | accepted · 24 Oct<br>built | [#94](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/94) |
| [0052](0052-the-graph-emerges-from-recorded-reads-and-writes.md) The graph emerges from recorded reads and writes | proposed · 24 Oct<br>partial | [#80](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/80) |
| [0053](0053-the-team-graph-is-drawn-with-react-flow-and-elk.md) The team graph is drawn with React Flow and laid out by elk; Mermaid is only an export | proposed · 24 Oct<br>built | [#77](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/77) |
| [0054](0054-observability-is-whether-each-team-promise-is-kept.md) Observability is whether each team promise is kept: telemetry in, a graph out | proposed · 24 Oct<br>built | [#80](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/80) |
| [0055](0055-connectors-show-their-sign-in-test-live-on-demand-and-bind-themselves-when-alone.md) Connectors show their sign-in, test live on demand, and bind themselves when they are alone | accepted · 24 Oct<br>built | [#84](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/84), [#91](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/91) |
| [0056](0056-modules-contribute-status-items-actions-and-widgets-as-data.md) Modules contribute status items, header actions and Today widgets as data, viewed from their own operations | proposed · 24 Oct<br>built | [#140](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/140), [#147](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/147) |
| [0057](0057-ai-access-is-a-module-one-choice-like-the-theme.md) AI access is a module: one choice like the theme, options are adapters of `ai-access` | proposed · 24 Oct<br>built | [#92](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/92) |
| [0058](0058-soundscapes-are-data-a-plugin-ships-chosen-like-a-theme.md) Soundscapes are data a plugin ships, chosen like a theme: `module.soundscape(...)`, `hz system sound use` | proposed · 24 Oct<br>built | [#140](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/140) |
| [0059](0059-the-calendar-and-the-mail-write-through-hz-after-a-confirmation.md) The calendar and the mail write through hz, after a confirmation | proposed · 24 Oct<br>built | [#140](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/140) |
| [0060](0060-themes-follow-the-time-of-day-and-the-weather.md) Themes follow the time of day and the weather: variants on a closed vocabulary of signals | proposed · 24 Oct<br>built | [#120](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/120) |
| [0061](0061-plugins-ship-skills-every-ai-path-can-use.md) Plugins ship skills every AI path can use: `module.skill(...)`, read-only tools, an explicit export | proposed · 24 Oct<br>partial | [#140](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/140) |
| [0062](0062-heinzelmaennchen-find-the-mcp-servers-and-skills-you-gave-claude.md) Heinzelmännchen find the MCP servers and skills you already gave Claude, read-only first | proposed · 25 Oct<br>partial | [#132](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/132) |
| [0063](0063-world-clock-is-a-local-interactive-globe.md) World Clock is a local interactive globe in the right panel | proposed · 25 Oct<br>built | [#162](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/162) |
| [0064](0064-themes-can-dress-the-core-heinzelmaennchen.md) Themes can dress the core Heinzelmännchen with a palette and snow | proposed · 25 Oct<br>built | [#163](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/163) |
| [0065](0065-plugins-may-ship-versioned-react-views.md) Plugins may ship versioned React views alongside their Python declarations | proposed · 25 Oct<br>partial |  |
| [0067](0067-hz-owns-sessions-across-pydantic-and-native-cli-turns.md) hz owns sessions across Pydantic and native CLI turns | proposed · 25 Oct<br>partial |  |
| [0068](0068-human-approval-proofs-and-head-parity.md) Human approval is a scoped proof carriable by every authorized head | proposed · 25 Oct<br>partial | [#168](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/168) |
| [0071](0071-text-branding-uses-the-name-and-original-artwork.md) Text branding uses the name and original artwork | proposed · 25 Oct<br>built | [#170](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/170) |
| [0072](0072-plugins-declare-navigation-and-settings-as-data.md) Plugins declare navigation and Settings metadata as checked data | proposed · 25 Oct<br>built | [#173](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/173) |
| [0073](0073-themes-supply-safe-avatar-layers.md) Themes supply safe SVG layers for personal avatars | proposed · 25 Oct<br>built | [#175](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/175) |
| [0074](0074-machine-resource-meters-use-bounded-local-sampling.md) Machine resource meters use bounded local sampling | proposed · 25 Oct<br>built | [#188](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/188) |
| [0078](0078-native-desktop-uses-the-shared-lifecycle-and-daemon.md) Native desktop uses the shared lifecycle service and existing daemon | proposed · 25 Oct<br>partial | [#208](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/208), [#211](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/211), [#284](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/284) |
| [0079](0079-core-pypi-trusted-publishing-is-isolated-from-builds.md) Core PyPI trusted publishing is isolated from builds | proposed · 25 Oct<br>built | [#188](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/188) |
| [0080](0080-one-invite-line-and-a-reviewed-first-win.md) One invite line and a reviewed first win | proposed · 25 Oct<br>partial |  |
| [0081](0081-state-repositories-one-writer-per-branch-the-daemon-pushes.md) State repositories: one writer per branch, append-only segments, the daemon pushes | proposed · 25 Oct<br>built | [#134](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/134) |
| [0082](0082-plugin-documentation-is-generated-from-the-kernel-and-recorded-by-shared-tooling.md) Plugin documentation is generated from the kernel and recorded by the tooling every plugin shares | proposed · 24 Oct<br>built | [#135](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/135) |
| [0083](0083-restart-and-apply-verifies-the-replacement-server.md) Restart and apply verifies the replacement server | proposed · 25 Oct<br>built | [#188](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/188) |
| [0084](0084-explicit-https-origins-for-private-web-sharing.md) Private web sharing admits exact HTTPS origins and keeps device authentication | proposed · 25 Oct<br>built | [#188](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/188) |
| [0085](0085-web-updates-verify-the-reloaded-frontend.md) Web updates verify the reloaded frontend | proposed · 26 Oct<br>built | [#188](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/188) |
| [0087](0087-managed-backend-ownership-is-independent-of-the-window.md) Managed backend ownership is independent of the native window | proposed · 27 Oct<br>built | [#221](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/221) |
| [0088](0088-collapsed-sidebar-keeps-sound-controls.md) The collapsed sidebar keeps sound controls | proposed · 27 Oct<br>built | [#240](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/240) |
| [0089](0089-one-selected-workspace-panel.md) One selected workspace panel on every screen size | proposed · 27 Oct<br>built | [#240](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/240) |
| [0090](0090-installed-apps-share-their-authenticated-url.md) Installed apps share their authenticated URL | proposed · 28 Oct<br>built | [#247](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/247) |
| [0091](0091-plugin-add-uses-server-owned-operation-receipts.md) Confirmed plugin adds use server-owned operation receipts | proposed · 28 Oct<br>built | [#248](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/248) |
| [0092](0092-youtube-sign-in-is-an-explicit-player-choice.md) YouTube sign-in is an explicit player choice | proposed · 28 Oct<br>built | [#250](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/250) |
| [0093](0093-touch-update-pane-remains-inspectable.md) Touch users inspect updates before installing | proposed · 28 Oct<br>built | [#256](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/256) |
| [0094](0094-plugins-own-features-and-independent-delivery.md) Plugins own their features and independent delivery | accepted · 29 Oct<br>built | [#270](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/270) |
| [0095](0095-standard-heinzel-theme-owns-bundled-soundscapes.md) The standard Heinzelmännchen theme owns the bundled soundscapes | accepted · 29 Oct<br>built | [#270](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/270) |
| [0096](0096-release-plugin-boundary-at-the-first-unused-version.md) Release the plugin boundary at the first unused version | accepted · 29 Oct<br>built | [#270](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/270) |
| [0097](0097-plugin-authors-use-one-small-artifact-based-toolchain.md) Plugin authors use one small artifact-based toolchain | proposed · 29 Oct<br>built | [#270](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/270) |
| [0098](0098-pages-showcases-the-product-and-discovers-public-plugin-metadata.md) Pages showcases the product and discovers public plugin metadata | proposed · 30 Oct<br>built | [#278](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/278) |
| [0099](0099-reviewed-connected-reads-and-local-agent-notifications.md) Review connected reads and local agent notifications separately | proposed · 30 Oct<br>built | [#282](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/282) |
| [0104](0104-reviewed-item-evidence-for-local-notifications.md) Bind notification deduplication to observed source items | proposed · 30 Oct<br>built | [#291](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/291) |
| [0105](0105-status-detail-designations-and-state-icons.md) Status detail rows carry provider designations and work state icons | proposed · 30 Oct<br>built | [#292](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/292) |
| [0106](0106-source-bound-connected-provider-loading.md) Bind connected providers to source-only loading | proposed · 30 Oct<br>built | [#295](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/295) |
| [0107](0107-propose-bounded-connected-read-sets.md) Propose bounded connected read sets separately | proposed · 30 Oct<br>built | [#299](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/299) |
| [0108](0108-record-owned-installation-continuity-after-verified-changes.md) Record owned installation continuity after verified changes | proposed · 30 Oct<br>built | [#300](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/300) |
| [0109](0109-adopt-plugin-playbook-terms-as-inert-chat-drafts.md) Adopt plugin playbook terms as inert chat drafts | proposed · 30 Oct<br>built | [#303](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/303), [#307](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/307) |
| [0110](0110-one-versionless-launcher-installs-and-opens-the-standard-app.md) One versionless launcher installs and opens the standard app | proposed · 30 Oct<br>built | [#304](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/304), [#310](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/310) |
| [0111](0111-plugins-recommend-the-default-theme-with-a-core-recovery-fallback.md) Plugins recommend the default theme with a Core recovery fallback | proposed · 30 Oct<br>built | [#304](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/304) |
| [0114](0114-green-main-releases-to-edge-and-acceptance-promotes-to-stable.md) Green main releases to edge, and acceptance promotes to stable | proposed · 31 Oct<br>partial |  |
| [0115](0115-merge-unfinished-work-dark-behind-named-feature-flags.md) Merge unfinished work dark behind named feature flags | accepted · 1 Nov<br>built | [#354](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/354) |
| [0116](0116-automatic-updates-are-scheduled-by-the-running-app-server.md) Automatic updates are scheduled by the running app server | proposed · 2 Nov<br>built | [#452](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/452) |
| [0117](0117-persistent-web-access-has-an-owned-os-controller.md) Persistent web access has an owned OS controller | proposed · 3 Nov<br>partial | [#485](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/485) |
| [0118](0118-contribution-declared-footer-retention.md) Contributions declare footer retention priority | proposed · 3 Nov<br>built | [#488](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/488) |
| [0119](0119-interactive-cli-reads-local-overview-records.md) Interactive CLI reads local overview records | proposed · 3 Nov<br>partial | [#492](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/pull/492) |
| [0120](0120-channels-carry-notices-and-approval-claims.md) Channels carry notices and approval claims | proposed · 4 Nov<br>partial |  |
| [0121](0121-confirm-each-captured-write-before-one-attempt.md) Confirm each captured write before one attempt | proposed · 4 Nov<br>partial |  |
| [0122](0122-preserve-authoritative-chat-history.md) Preserve authoritative chat history independently of model context | proposed · 30 Oct<br>partial |  |
| [0125](0125-prepare-locked-isolated-build-dependencies.md) Prepare locked dependencies before isolated local builds | proposed · 4 Nov<br>built |  |
| [0127](0127-publish-a-bounded-standing-draft-append-grant.md) Publish a bounded standing grant for draft append | proposed · 4 Nov<br>partial |  |
| [0128](0128-conditional-credential-health.md) Optional credentials do not degrade unused features | proposed · 4 Nov<br>partial |  |
| [0129](0129-explicit-web-serving-and-separate-service-consent.md) Explicit web serving and separate service consent | proposed · 4 Nov<br>partial |  |
| [0130](0130-on-demand-health-preserves-runtime-requirements.md) On-demand health preserves runtime requirements | proposed · 4 Nov<br>partial |  |

</div>
<!-- adr-index:end -->

The pull requests are the ones that built the record, empty where none is known (the founding records came with the
first commit).

ADRs 0021–0024 are
proposed together and disagree on a few points (data file format, the shared `inventory` module, canonicalization,
selector grammar); each lists them under More Information.

## Rules

- **Statuses:** `proposed` → `accepted` or `rejected`; later `deprecated` or `superseded` (with `superseded-by`).
- **Accepted records are immutable.** Only their front matter changes (`status`, `superseded-by`, `amended-by`); a
  changed decision is a new ADR that supersedes or amends the old one, with the links set on both.
- **Numbers** are sequential and never reused; files are `NNNN-title-in-kebab-case.md`.
- **This index** is generated from the records' front matter by `make docs` (`scripts/generate_docs.py`): title, status,
  date and `implemented-by` (the pull requests that built it, by number, never the one that only wrote the record).
  `tests/unit/documentation_test.py` checks the template, this table and the links between records.
- New ADR: copy [0000-template.md](0000-template.md) or use the `write-adr` skill.
