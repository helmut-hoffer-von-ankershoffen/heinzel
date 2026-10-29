# Further plugin extraction opportunities

Code audit, 2026-10-29. Resource remains the only additional built-in module extracted for 0.9.0. This is a ranked
backlog for subsequent work, ordered by useful independence gained against implementation effort and risk. The
Heinzelmännchen theme/content extraction at rank 3 subsequently joined the 0.9.0 scope in
[ADR 0095](../adr/0095-standard-heinzel-theme-owns-bundled-soundscapes.md); other rows remain future opportunities.

Plugins own their runtime and development dependencies and version locks. They can implement and consume public
interfaces, capabilities and lets. A dependency on another provider's capability should target its shared contract.
Core must remain buildable, testable and usable without concrete plugin implementations.

Themes and soundscapes are already extensible without core changes. A plugin can declare both with `module.theme`
and `module.soundscape`, ship their assets, and pair soundscapes with its theme names. Core discovers their public
lets rather than importing a concrete theme plugin or consulting a list of theme names. The content and engine
extractions below concern ownership of today's built-in features; adding a theme does not require them.

Most candidates below can become plugins. Their current blockers are named defaults, host-owned representations,
packaging assumptions or shared semantics. The audit found zero incoming production Python implementation imports
for Directory, Claude inventory, Report/Telemetry and Graph; their remaining integration paths still need attention.

| Rank | Module or feature slice | Why extraction is blocked today | Smallest enabling change and payoff |
| --- | --- | --- | --- |
| 1 | Directory fallback | Kernel bindings default to the concrete `one-on-ones` provider. | Existing `Directory`, `Calendar`, `Person` and `Relation` contracts suffice. Replace the named default with generic provider selection or explicit profile configuration. A small complete-module extraction. |
| 2 | Claude configuration inventory | Core Settings imports its component and calls its endpoint directly. | Move scanner, settings view, navigation and tests together using existing operations and ViewLet. No new service contract is needed. Vendor-specific changes become independently releasable. |
| 3 | Heinzelmännchen theme and soundscape content — included in 0.9.0 | Formerly core-owned definitions now belong to the standard-profile theme plugin. | The plugin owns the theme, logos and all three former soundscapes through existing public lets. Core keeps generic playback and fallback appearance. Installed-wheel and minimal-core tests enforce the boundary. |
| 4 | AI usage ledger | Usage operations/settings share the core AI module's namespace and registration. | No architectural blocker found: public `UsageLog` already exists and recording tolerates an absent provider. Separate the ledger's ownership. Keep spending authorization and budget enforcement in core. |
| 5 | Native notification delivery / Heinzel Notifier | Notification services accept private native implementations; daemon units know the bundle identity; core build and publishing compile/package the Swift app. | Add an optional native-delivery contract with availability, delivery, permissions and application identity. Move native assets, compilation and platform tests. This has the largest direct core release-path payoff. Keep notification policy and the durable ledger in core. |
| 6 | AI gateway and vendor runner adapters | Gateway already fits `AiAccess`. Claude/Codex/Command share concrete implementation helpers; provider names and special cases also appear in core defaults and consumers. | Gateway is a near-term extraction. Move shared process helpers to a neutral home, replace provider-name defaults with descriptors/profile selection, and move provider login/settings with each adapter. Reuse `AiAccess` and `AgentRunner`; retain orchestration and authorization in core. |
| 7 | Claude terminal-title enrichment | The web head recognizes Claude executables and reads Claude session files directly. | Add an optional, bounded terminal-metadata provider contract. Keep PTY transport/lifecycle in core. Different agent integrations could then evolve in parallel; removing a provider leaves ordinary terminal titles usable. |
| 8 | Report / Telemetry | Core State directly reads and prescribes `report.user`; core Settings names the module. | Retain public `TelemetryLog`, move implementation/UI/tests, and expose business roster identity through a neutral service, potentially using `org.handle`. Authenticated `CurrentUser` remains separate infrastructure. |
| 9 | Graph | Kernel defaults name `hz-graph`; routes, titles and GraphView name the concrete module/endpoint. Its schedule evaluator duplicates daemon semantics. | Move page, renderer dependencies and playbook into the plugin. Preserve implementation-free graph contracts/schema in the SDK. Use generic page metadata and shared schedule evaluation/conformance fixtures. Graph already tolerates absent promise/telemetry providers. |
| 10 | Audio visualizer | Shell owns its setting and mounts it at a specific workspace edge. | Add a generic workspace-edge view slot; move visualization, capture controls, setting and tests together. Capture starts from a user gesture. This is separate from Resource's CPU/memory/load display. |
| 11 | Coding-agent skill installation/export | Installer assets are assumed to live in the core package. Chat policy names its install/uninstall operations; the generic skill broker imports a concrete exporter. | Move agent-specific assets/install/export to integration plugins. Keep checked skill discovery/loading and `Skills` in core. Replace named safety-policy entries with generic operation-policy metadata where CLI-only restrictions are insufficient. |
| 12 | Meeting/calendar assistance | Generic meeting status data exists, but the footer reserves and manually renders `org:meeting`. Sound reads the same concrete agenda endpoint. | Remove special footer rendering using existing contributions; add a neutral meeting/activity context for consumers. Extract this slice independently of the entire Org module. |
| 13 | launchd/systemd supervision | Scheduling semantics, unit generation, filesystem writes and platform commands are mixed; daemon heads and UI select concrete operations. | Add a supervisor contract for install/status/start/stop/logs and service-version attestation. Move platform supervision; retain schedules, receipts and safety rules in core. |
| 14 | Sound playback/orchestration | Browser chat activity, dock, settings, player state and meeting behavior directly consume Sound's implementation. YouTube is a closed source/player protocol. | Define neutral activity signals, playback/media-provider contracts and generic dock/background/settings contributions. Keep audited origin/asset security in core. This requires more boundary work than moving its Python module. |
| 15 | Core theme selection and variant engine | Host token application, previews, branding and splash readiness call the built-in Theme service APIs. Theme plugins themselves already use public declarations. | To move the engine, introduce a presentation snapshot/change contract. Core keeps a zero-plugin fallback theme, safe CSS vocabulary, asset validation and first-paint support. Adding themes already requires no core edits. |
| 16 | Plugin documentation rendering/recording | Renderer imports private inventory models/helpers; core documentation generation imports the concrete renderer/service. | Expose a public installed-plugin/introspection snapshot. Independent developer tooling can consume it; an optional plugin can provide the CLI facade. Core docs generation must remain independent. |

## Evidence for the ranking

- Directory declares public capabilities in [its module](../../src/heinzel/modules/system/directory/__init__.py).
  Its named default is in [kernel bindings](../../src/heinzel/kernel/_bindings.py); roster drafting already consumes
  the public interface in [TeamService](../../src/heinzel/modules/team/_service.py).
- Claude inventory's concrete UI integration is in
  [FromClaudeSection](../../web/src/layout/FromClaudeSection.tsx),
  [SettingsDialog](../../web/src/layout/SettingsDialog.tsx) and
  [settingsSections](../../web/src/layout/settingsSections.ts).
- Soundscape declarations are registered by [Sound](../../src/heinzel/modules/system/sound/__init__.py).
  Optional usage logging already has its seam in [the AI contracts](../../src/heinzel/kernel/_ai.py).
- Native delivery is coupled through
  [NotifyService](../../src/heinzel/modules/system/notify/_service.py),
  [channel reporting](../../src/heinzel/modules/system/notify/_channels.py),
  [daemon units](../../src/heinzel/modules/system/daemon/_units.py), [the build hook](../../hatch_build.py) and
  [publication](../../.github/workflows/publish.yml).
- Runner implementation imports are visible in [Codex](../../src/heinzel/modules/system/runner/_codex.py) and
  [Command](../../src/heinzel/modules/system/runner/_command.py). Provider-specific assumptions also occur in
  [AI access settings](../../src/heinzel/modules/ai/access/_models.py),
  [AI access selection](../../src/heinzel/modules/ai/access/_service.py) and
  [Heinzel orchestration](../../src/heinzel/modules/heinzel/_service.py).
- Terminal enrichment is isolated in [agent title lookup](../../src/heinzel/heads/web/_agent_titles.py), called by
  [the terminal head](../../src/heinzel/heads/web/_terminal.py).
- Report identity coupling is in [StateService](../../src/heinzel/modules/system/state/_service.py).
  [GraphService](../../src/heinzel/modules/graph/_service.py) already handles absent public data providers.
  [Graph scheduling](../../src/heinzel/modules/graph/_schedule.py) documents the shared-semantics issue.
- The common page boundary is visible in [App routes](../../web/src/App.tsx),
  [TopBar metadata](../../web/src/layout/TopBar.tsx) and [GraphView](../../web/src/pages/graph/GraphView.tsx).
  The visualizer's mount and setting remain in [Shell](../../web/src/layout/Shell.tsx).
- Skill asset ownership is in [AgentService](../../src/heinzel/modules/system/agent/_service.py);
  [chat policy](../../src/heinzel/modules/system/runner/chat/_policy.py) names the operations and
  [SkillService](../../src/heinzel/modules/system/skill/_service.py) imports its exporter.
- Meeting data already uses generic contributions in [the agenda](../../src/heinzel/modules/org/_agenda.py), with
  special rendering in [Contributions](../../web/src/components/Contributions.tsx) and
  [StatusBar](../../web/src/layout/StatusBar.tsx).
- Sound's remaining browser links are in [Conversation](../../web/src/chat/Conversation.tsx),
  [SoundEngine](../../web/src/sound/SoundEngine.tsx), and the sidebar/settings/player components.
  Its Python service consumes optional `ThemeChoice` through the public contract in
  [SoundService](../../src/heinzel/modules/system/sound/_service.py).
- Supervision is mixed into [DaemonService](../../src/heinzel/modules/system/daemon/_service.py).
  Documentation rendering relies on private inventory through
  [DocsService](../../src/heinzel/modules/system/plugin/docs/_service.py) and
  [the generator](../../scripts/generate_docs.py).

## Work that should remain core

Keep discovery, configuration bootstrap, authenticated identity, authorization/approval policy, generic head
transports and the minimal install/repair coordinator available with zero plugins. They establish the environment
and trust boundary in which plugins load. Their optional backends, richer presentation and provider integrations
can use public contracts.

Changelog and updater presentation could be separated later, but the current coordinator is not a leaf extraction:
CLI, web queries and notification watching explicitly call it. Preserve core's ability to inspect and repair its
installation without optional providers. `ReleaseSource` already supplies one useful backend seam.

For every extraction, first replace its concrete host paths, then move all heads/assets/dependencies/tests, prove
core without the provider, and prove installation/removal using built artifacts. Update the standard profile
explicitly where default inclusion is desired; preserve an explicit user disable/removal choice.
