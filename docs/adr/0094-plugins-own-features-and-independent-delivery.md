---
status: accepted
date: 2026-10-29
decision-makers: maintainer
amends: 0017, 0056, 0063, 0065, 0072, 0074, 0082
related: 0026, 0028, 0044, 0060, 0073, 0084, 0089
amended-by: 0095, 0096, 0110
implemented-by: 270
---

# 0094: Plugins own their features and independent delivery

## Context and Problem Statement

The Clock plugin declares actions, but its globe, footer clock, ticker, routing and data fetching still live in the
web head. The web head also suppresses Weather actions by module name and changes its CSP when Weather is present.
Root test dependencies, tool configuration and releases enumerate first-party plugins, coupling every new plugin
and delivery cycle to core changes. Navigation declarations (ADR 0072) and rich views (ADR 0065) need one complete,
provider-independent implementation across all heads.

**Release boundary**

The target core release is **0.9.0**. This is an explicitly approved breaking clean-install boundary: users may
uninstall the old installation, install the new standard app and configure it afresh. Do not add compatibility
aliases, old-source redirects, namespace mount machinery or old-state migration solely to preserve earlier releases.
Resource moves to the top-level `resource` plugin namespace. Other built-in modules retain their current identities.
New-version user choices still survive ordinary plugin/application updates.

## Decision Drivers

- Adding a plugin changes only its own directory or repository.
- Core builds, tests and releases without real plugins; a plugin changes without running the complete core suite.
- Existing feature behavior, accessibility, operation gates and notification policy remain available.

## Considered Options

- **Plugin-owned declarations, assets and versioned views with independent delivery** (chosen).
- Keep named renderers and catalogs in core: every new feature requires a coordinated core change.
- Replace specialist interfaces with text only: loses the requested interactive Clock and its representations.

## Decision Outcome

Chosen option: **plugin-owned declarations, assets and versioned views with independent delivery**, because feature
ownership must extend from source boundaries through design, build, test and release.

- Outside plugin directories, production code depends only on public contracts for plugin features. It never
  recognizes a plugin's
  identity, endpoint, route or contribution id to enable, suppress, position or implement its feature. Shared
  semantic types (time values, weather signals, themes, notification policy) remain provider-independent.
- No consumer imports or depends on a concrete plugin implementation. Plugins may implement public interfaces,
  capabilities and lets required by other plugins; requirements target those contracts and bind to installed
  interchangeable providers through the kernel. Missing required contracts disable only the dependent feature with
  a useful diagnostic. Core remains usable with zero plugin providers and never requires one to boot or test.
- Public contracts live in the SDK or implementation-free contract packages, never inside a concrete plugin implementation.
  Plugins can supply typed service interfaces and custom lets through existing public registration; capability
  providers implement the shared capability protocols. Shared contract packages must not register plugin entry points.
- Built-in modules prefer the same public extension points as external plugins. Private integration paths are
  allowed when they materially simplify the design; avoid them where practical rather than generalizing every
  internal service. Sound is a design target for movable module ownership: its player, controls, Settings and status
  should use public contributions where practical. Shared media infrastructure may retain justified private seams.
- Every representation declares its slot and order; lower order precedes higher order, with stable id tie-breaking.
  The generic host owns responsive layout and applies user visibility choices. Navigation/menu metadata, Settings,
  header actions, footer statuses, sidebar tickers, Today widgets and notifications need no named host branch.
- Clock owns its right workspace panel/globe, footer clock, header icon, left sidebar ticker, menu and Settings,
  operations, translations, dependencies and tests. CLI, MCP and API project the plugin's operations normally.
  Weather owns its provider access, status/widgets, actions and notifications. Themes own branding/assets; sharing
  adapters own vendor behavior. Disabling/removing a provider removes its executable contributions and assets.
- A checked view declaration binds an installed local ES module and assets to its owning module's read operation,
  stable panel id, accessible title and text fallback. A versioned SDK supplies one React runtime, host controls,
  query data and gated effects. No remote code, plugin-selected CSP exception or raw bearer is supplied. This is
  trusted installed code, not a sandbox. Invalid/incompatible views fail locally with a usable fallback.
- Common status and widget representations remain checked data, including bounded meters, live detail/segment values and ticker
  presentation. Status refreshes remain bounded at two seconds to preserve the resource sampler cadence; Today
  widgets retain their one-minute minimum. Custom code is for specialist panels. Plugins publish notifications through the existing generic
  Notifications service; delivery, deduplication, preferences and native adapters remain shared.
- Catalogs discover metadata from plugin pyproject files in a repository's plugins directory and from external
  repositories carrying the heinzel-plugin topic. Catalog discovery never executes plugin code. Installing the
  discovered spec uses existing reviewed source/provenance checks. A plugin requires no central name list, manual
  workspace dependency, type/coverage registry, test registry or release-list edit. Private metadata stays private.
- Plugins declare their own runtime and development dependencies and regenerate their own locks, without a core
  dependency change or sibling environment. Ordinary libraries and implementation-free shared contracts are allowed;
  concrete provider dependencies remain prohibited. An isolated-environment test installs an additional runtime
  dependency from a hashed wheel and executes plugin code using it to keep this supported.
- Core development dependencies and tests exclude real plugins. Core contract tests use synthetic arbitrary names.
  Each plugin builds its own wheel/view bundle, tests against a compatible core/SDK artifact and has its own version
  and release. Plugin-only CI selects affected plugins and never runs core/web/native build matrices. Core changes
  run core checks; public-contract changes add affected consumer compatibility checks. Broad compatibility runs may
  remain a separate gate; they are not the ordinary plugin edit loop.
- Durable checks introduce an unknown plugin into each supported source, list/install its built artifact, exercise
  navigation, panel, statuses, priorities, Today widget, notification and CLI/MCP/API operations, then disable/remove
  it. Core bytes stay unchanged. Measure representative scoped build/test runs and report timings and selected work
  so acceleration is demonstrated, without fragile machine-speed assertions.

**Migration roster and default installation**

The standard app profile includes and activates the plugins marked for that profile. The minimal core wheel has
none as dependencies. Default activation never overrides an explicit disable, removal or installation policy;
installing a discovered repository remains an explicit trust decision. A remote author cannot cause automatic
installation merely by setting its own default flag. Profile selection is restricted to the installer's selected,
verified catalog sources and artifact revisions. The app installer consumes the profile, not a hard-coded package
list. A plugin declares profile membership, dependencies, compatible core/SDK range and its version in its own
pyproject metadata. Default inclusion is independent of startup side effects: discovery/rendering never enables
network sharing, playback, vendor login or permission prompts.

| Current feature/module | Destination | Standard profile | Migration boundary |
| --- | --- | --- | --- |
| Clock (already a plugin) | heinzel-plugins/plugins/heinzel-clock | Included and active | All six representations, Settings, source operations and globe assets move together. |
| system.resource | heinzel-plugins/plugins/heinzel-resource | Included and active | CPU/memory/load sampler, psutil dependency, visualizer, health details and tests. |
| Tailscale (already a plugin) | heinzel-plugins/plugins/heinzel-tailscale | Included and active; sharing OFF | Vendor client, consent, mapping ownership and readiness stay entirely in the plugin. |
| Weather (contrib) | heinzel-plugins/plugins/heinzel-weather | Explicit installation | Remove named host rules; use shared navigation, widget, signal and notice contracts. |
| Acme theme (external) | Its existing repository | Explicit installation | Titles, branding, assets and optional soundscape declarations remain plugin-owned. |
| Private theme | Its existing private repository | Explicit installation | Same contracts and private CI; no private names/assets copied into public sources or fixtures. |
| Example (contrib) | heinzel-plugins/plugins/heinzel-example | Explicit installation | Demonstrates independently installable contributions and serves as a consumer compatibility proof. |

Only Resource is extracted from built-in modules in this release. Claude inventory, Directory, Report and Graph
stay in core; their inter-module dependencies need a separate audit before any later extraction. Move Resource's
owning tests, frontend dependencies and documentation with its implementation. A source-file move alone does not
count as extraction.

Sound and Theme stay built in for this pass: shared playback, theme selection, media assets and their relationship
make them poor first extractions. PluginManager, Config, Credentials, Doctor, notification delivery, lifecycle,
authentication, runner/session services and the kernel remain core infrastructure. Their public contracts may be
consumed by plugins; justified private integrations within core are allowed. Revisit extraction after measuring
the initial migrations rather than creating new extension abstractions for every internal detail.

**Repository consolidation**

The 0.9.0 release consolidates public plugins currently in the core and contrib repositories into
one public heinzel-plugins repository, with projects under plugins/<distribution>. Keep the core repository for
kernel, heads, shared infrastructure and SDK contracts. Keep private plugins in their existing private repositories;
external plugins remain equal consumers discovered by topic. Consolidation is a repository migration following the
functional extraction, not a prerequisite for independent builds and not a source-code move that changes semantics.

Use independent projects, lockfiles, environments, versions and tags per plugin. A shared uv workspace resolves all
members into one lockfile; that is useful for coordinated applications but creates unnecessary coupling for plugin
authors with different compatible core ranges ([uv workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/)).
Shared scripts/SDK are versioned tooling dependencies; adding a project never edits their source or a root registry.
A plugin-only change must finish without acquiring every plugin's dependencies or waiting for unrelated releases.

The repository migration targets 0.9.0's fresh installation. Distribution names of existing plugins stay stable
where useful, but old git installs and stored configuration need not be migrated. The installer establishes the new
verified sources. Release provenance, package permissions, licenses and source attribution travel with each plugin.
Default app profiles pin a coherent tested artifact set. A changed repository origin is never implicitly trusted
merely because a distribution name is unchanged.

CI always starts its generic planner and stable required aggregate check, then conditionally runs selected jobs;
never skip the entire required workflow through path filters, which leave required checks pending
([GitHub checks](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs)).

**Independent work and confidence**

- A plugin author works against a released or versioned candidate compatible core/SDK artifact, never the core source checkout. Core API
  tests use arbitrary provider fixtures. Consumer tests cover their declared contract boundary and own behavior.
- Ordinary plugin edits run that plugin's formatting/types/unit/component/integration tests and wheel build only.
  Affected consumers are selected through public contract requires/provides metadata, not a central allowlist or
  sibling-plugin package dependencies. Profiles select concrete compatible artifacts for installation; feature code
  consumes only public contracts and interchangeable providers. Core/web/native
  matrices are skipped for plugin-only changes. Documentation and packaging hooks are scoped too.
- Core contract changes produce a versioned artifact and run affected consumer compatibility tests against it.
  A fast core-wheel job provides this artifact independently of native packaging and the core test matrix. The
  complete core/native build and tests still gate core merge/release; consumer feedback never waits for them.
  Breaking changes require an explicit compatibility/version decision and migration before default profiles select
  them. The default app composition is a required focused release check; broad ecosystem sweeps are separate from routine
  edit loops. No required checks or coverage are weakened.
- Plugin versions, changelogs and release tags are independent. Releasing a plugin does not bump core, rebuild the
  app shell, publish unrelated plugins or require all plugin authors to merge at once. Shared API releases provide
  compatible migration windows. Default profile manifests pin a coherent tested set without synchronizing versions.
- CI emits a machine-readable work plan and elapsed timings. Positive and negative selection tests prove that a
  new plugin directory needs no parent-file edits and a plugin-only diff selects no core test/build/release work.
  Record cold and warm core-only and representative plugin-only timings before/after; compare selected work as
  well as wall time. Parallel contributors must be able to complete independently on different plugins.
- A migration is complete only when removing its plugin leaves core usable and its complete feature absent, then
  installing its released wheel restores every declared head/surface without changing or rebuilding core. Failure,
  missing/incompatible bundles, disabled modules and catalog outages have explicit visible fallback behavior.

### Consequences

- Good: new plugins and plugin-only fixes are independently discoverable, testable and releasable.
- Good: core works without optional plugins and no longer ships Clock's feature bundle.
- Bad: the SDK/manifest compatibility boundary and independent consumer releases need explicit maintenance.
- Bad: initial migration changes packaging, CI and existing tests as well as runtime implementation.

### Confirmation

Implementation must include kernel declaration/asset validation tests, arbitrary-provider web and cross-head
integration tests, catalog source tests, wheel-content checks and a scoped pipeline planner with positive and
negative selection tests. Existing notification, theme, navigation, operation authorization and origin tests remain
required. Each claimed acceptance criterion needs observed verification; this ADR alone does not implement it.

**Required service contracts**

Implementation-free injected interfaces derive from the public `ServiceContract` marker. Providers register an
implementation with `module.service`; consumers import only the interface. Without an active provider, direct and
transitive dependent operations are unavailable across CLI/MCP/API while unrelated operations remain usable.
Ordinary user data classes are never inferred to be services. Optional consumers explicitly check the kernel for a
provider. Tests cover present, absent, disabled and replacement implementations without concrete package imports.

**Observed delivery checks (2026-10-29)**

Local measurements compared `make check` at core commit `3a691c78` with the refactored core, then checked Resource
alone against the built 0.9.0 core wheel. Core runs used separate checkouts with dependencies installed before
measurement. Resource's first run created its own environment in a third repository containing only its directory;
both environments reused the machine's download cache. These are shared-host observations, not a controlled
performance guarantee or cold-network installation benchmark.

| Check scope | First run | Warm repeat | Selected work |
| --- | --- | --- | --- |
| Core before extraction | 183.8 s | 207.4 s | Existing combined checks |
| Refactored core | 122.3 s | 108.3 s | Core Python, web, contracts, docs and managed backend |
| Resource alone | 11.9 s | 6.0 s | Its lint, spelling, strict types and 19 tests; 100% coverage |

The Resource run installed a core wheel and created no sibling plugin environments. Planner tests select an unknown
plugin and its declared contract consumers while selecting no core build/test work. Actual PR and push diff tests
also prove that optional plugin source changes in the core repository skip its native/core matrices. An invalid
plugin source/README probe leaves core content checks green and stays out of core's source archive. Plugin checks
retain their own source/document checks; repository credential and confidentiality policies remain shared safeguards.

Installed-wheel checks cover Clock's mobile gesture, painted globe and live ticker; Resource's meters/Today widget;
Tailscale's settings with sharing off; and Weather/Example CLI, MCP and private web operations. A fresh standard
composition installs exactly its three locally enrolled defaults and resolves their contributed view bundles. Core's
README install/use/uninstall journey uses a core-only candidate profile; its mobile panel journey uses a synthetic
provider. A freshly scaffolded arbitrary plugin exercises the independent build and lifecycle journey.

**0.9.0 rollout**

1. Review the plugin-repository migration, core/SDK change and contrib retirement as separate pull requests.
   During review, `PLUGIN_COMPAT_REF` may pin the reviewed tooling commit and `CORE_CANDIDATE_RUN` may select the
   official core CI wheel. These bootstrap settings select artifacts, never source imports or all-core plugin checks.
2. Merge the plugin tooling/implementations first, then reset `PLUGIN_COMPAT_REF` to its default `main`. Merge core
   and contrib after their required checks pass. Keep production publishing explicit; these changes do not publish.
3. Release and attest core 0.9.0, enroll the moved distributions' trusted publishers for their new repository, and
   release each selected plugin at its own advanced version. Clear candidate-run overrides once released core
   artifacts are available. Existing tags/artifacts are never moved or overwritten.
4. Run the approved standard composition against the verified core release and publish `app-v0.9.0/standard.json`
   only after its fresh-install checks pass. Announce the standard installation after that profile is available.
   Installation is fresh; uninstall/reconfigure is allowed, with no requirement to migrate earlier stored state.

## More Information

**Plugin author experience**

The developer journey is a release acceptance test. Starting with only the installed 0.9 core/SDK artifact, scaffold
Example into a new arbitrary directory, preview its interface, run its scoped checks, build its wheel, install that
wheel and exercise every declared contribution. No core checkout, parent-file edits or unrelated environments are
needed. The generated project has typed SDK help, useful examples, keyboard/reduced-motion checks, a plain fallback
and short local commands. Build errors name the owning file and the next action. Checks show selected work and
elapsed time; cached unchanged assets are reused. A release command previews one plugin's version/artifacts and
publishes only that plugin when explicitly requested. The Example walkthrough must remain tested documentation,
not a bespoke special case in core.
