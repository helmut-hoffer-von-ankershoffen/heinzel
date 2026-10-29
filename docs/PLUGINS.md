# Plugin extension guide

A plugin owns its operations, interface, assets, tests and release. The host renders typed data contributions and
versioned local views through public contracts, without imports or named branches for concrete plugins.
[ADR 0094](adr/0094-plugins-own-features-and-independent-delivery.md) defines the 0.9 boundary and independent delivery.

Start with `hz system plugin new --help`, then scaffold a plugin with the CLI. Use the generated repository's
`make setup`, `make preview`, `make check` and `make test-e2e`. These use an installed compatible core artifact. A distribution is named `heinzel-<name>`, its Python package
`heinzel_<name>`, and its entry point belongs to `heinzel.modules`. See [the architecture](ARCHITECTURE.md#plugins).

## Choose the smallest extension

| You need | Use today | Rich-view plan |
| --- | --- | --- |
| A command or a tool | `@module.operation`, with typed parameters and a serializable result | Same operation remains the source of data |
| A footer indicator | `@module.status`, a `StatusItem` with detail and actions | Keep the generic accessible icon and detail pane |
| A header action | `module.action` and a checked effect | Keep the generic button for ordinary actions |
| Settings or sidebar navigation | `module.nav`, checked icons and existing effects | Rich custom views remain separate |
| A Today card | `@module.widget` and a supported widget shape | Keep data shapes for common views |
| A colour palette, logo or fonts | `module.theme` | Optional theme figure sets, with core fallbacks |
| Background work | `@module.job` | Persisted triggers and wake scheduling need their own implementation |
| A specialist interactive page | `module.view(..., abi=1, slot="page", route="/plugins/<module>/<view>")` | Local versioned bundle with fallback |
| An interactive answer in chat | Text or structured tool output | A declared renderer, with text fallback and the same approval gate |

The public Python API is exported from `heinzel` and `heinzel.kernel`. Plugins do not import private kernel files,
heads, built-in modules or another plugin implementation. Shared interfaces belong in the public SDK or an implementation-free contract package. [Import contracts](CODING_STANDARDS.md#architecture-rules) enforce this.

## Navigation and Settings

A plugin declares its title and semantic icon, and groups only its own Settings sections. Sidebar links use the
existing checked effects; a declaration does not add a renderer or make an unknown route exist.

```python
from heinzel import NavSettings, Navigate

module.nav(
    title="Harbor",
    icon="cloud-sun",
    settings=NavSettings(sections=("harbor",), group="customize"),
    sidebar=Navigate(path="/?settings=harbor"),
)
```

`hz system ui navigation --json` lists the same metadata the authenticated app uses. Disabled/uninstalled providers
are absent. An invalid declaration has a diagnostic and no executable target. Explicit valid entries replace the
generic Settings compatibility entry, so the app needs no plugin-name map. [ADR 0072](adr/0072-plugins-declare-navigation-and-settings-as-data.md)
records the ownership rules. Declare the core range that supplies this additive API when publishing a plugin.

Repository badges describe the installed source: the official core and plugins repositories are **First-party**, the contrib repository is
**Community**, and other repositories are **Custom** when public or **Private** when private. Package names and
catalog routes do not establish provenance. When visibility was not reported, the app says so rather than guessing;
repository visibility is recorded during installation or returned by the catalog, without a network request for each
badge. Catalog metadata discovers configured repository directories and external repositories with the `heinzel-plugin` topic. No central plugin list is required.

## Python, CLI, MCP and REST

| Surface | Status | Declaration and limits |
| --- | --- | --- |
| CLI command, argument, option, help and JSON | Available | `@module.operation`; public parameter types and `Field(description=...)`; `audience` controls discovery, not authorization |
| CLI presentation | Available | `render=` supplies text presentation; operations return results and never print |
| MCP tools | Available | The head exposes operations using their declarations and `heads=` restrictions |
| MCP prompts and resources for skills | Available | `module.skill`; stdio MCP serves checked content outside chat |
| Arbitrary MCP prompts and resources | Proposed | No general plugin declaration; do not claim every MCP feature is an extension point |
| REST endpoints | Available | Operation exposure and read/write rules apply; inspect the generated reference for each operation's heads |
| Setup and onboarding steps | Available | `@module.setup`, `Prompter`, `ConfigEditor` and `Credentials`; web/chat onboarding adapters remain proposed |
| Health and doctor | Available | `@module.health`, including explicit live checks; failures say what to do next |
| System information | Available | `@module.info`; publish useful metadata, never credential values |
| Settings, defaults, schema and environment variables | Available | `@module.settings` and `ModuleSettings`; follow [SETTINGS_UX](SETTINGS_UX.md) |
| Credentials | Available | `module.credential`; injected `Credentials`; never put a secret into a setting or result |
| Capabilities and adapters | Available | `@module.provides` and declared requirements; bindings select adapters and mode-gated handles restrict writes |
| Agent runners and AI access | Available | Public capability/service contracts; see ADRs 0029, 0050 and 0057 |
| Services and dependency injection | Available | `module.service`; dependencies supplied by type, without imports between implementations |
| Company, organisation, site and team layers | Available | `module.layer`; general-to-specific recommendations, accepted by the person |
| Playbooks and artifact types | Available | `module.playbook`; types, discovery hints and contract templates; [PLAYBOOKS](PLAYBOOKS.md) |
| Artifact document templates | Proposed | A document starting point is distinct from a contract template; no public template API yet |

An operation is not automatically safe to run from every surface. Declare its heads, capabilities and write status;
test the refusal paths as well as the successful call. Never put a private operation behind an unauthenticated head.

## App layout and interaction

| Surface | Status | What a plugin can contribute |
| --- | --- | --- |
| Left sidebar top icons | Proposed | No general plugin slot; Home and Search are host controls |
| Left navigation pages | Available | `module.nav` links to an owned page route or registered panel |
| Left sidebar bottom stack | Available, limited | Status contributions may name the `sidebar` slot; player and account layout remain host-owned |
| Left sidebar account row | Host-owned | No plugin injection; identity and account actions stay consistent |
| Header actions | Available | `ActionLet`, checked icons and effects; no independent left/right placement contract |
| Header left and right placement | Proposed | Explicit slots, with responsive priorities and overflow; never position with arbitrary CSS |
| Footer status icons and panes | Available | `StatusLet`; state uses colour plus shape, details are keyboard reachable, actions use the host gate |
| Footer left and right placement | Proposed | Current ordering is not a stable left/right extension API |
| Right sidebar panels | Available | `module.view(..., slot="panel")`; open `/?panel=<module>:<view>` |
| Full pages | Available | `slot="page"` with the owned `/plugins/<module>/<view>` route |
| Today | Available | Card, list, metric, progress and graphic widget shapes; ids identify contributions and avoid duplicates |
| Settings fields | Available | JSON Schema chooses the control; slot switches belong with the contributing module |
| Settings and sidebar metadata | Available | `module.nav`, `NavSettings`, checked `Icon` and existing effects; only active owned Settings sections |
| Custom Settings panes | Available | `slot="settings"`, claiming only the owning module section |
| Connectors | Available, limited | Capability providers appear through the host's connector management; no arbitrary connector-card renderer |
| Search | Available, limited | Host navigation, settings, contracts and artifact types are searched; generic operation search and plugin result providers are proposed |
| Share and export | Proposed | Pages declare export data; design must exclude tokens, device links, private paths and secrets |
| Chat slash commands and mentions | Proposed | Data declarations name an operation or skill; no command string evaluated as code |
| Interactive chat results | Proposed | Tool result selects a registered view with validated data and a plain-text fallback |

Existing UI contribution models, limits and exposure rules are specified in
[ADR 0056](adr/0056-modules-contribute-status-items-actions-and-widgets-as-data.md). A richer view must not silently
replace the generic footer, hide a confirmation or make a read operation write. Registered bundles load through
the versioned SDK and the owning declaration; plugin browser code shares the trusted host realm.

## Theme, sound and character

| Surface | Status | Contract |
| --- | --- | --- |
| Themes, variants, backdrop, fonts and logo | Available | `module.theme`; validated assets and a checked palette; [THEMES](THEMES.md) |
| Time-of-day and weather signals | Available | `@module.signals`; closed signal vocabulary; plugins supply weather |
| Soundscapes and earcons | Available | `module.soundscape`; checked assets or an allowed stream provider; [ADR 0058](adr/0058-soundscapes-are-data-a-plugin-ships-chosen-like-a-theme.md) |
| Avatar accessories in graphic widgets | Available | Checked accessory data; the host draws the character |
| Dressing the user's own avatar | Proposed | A policy for selecting accessories across plugins, separate from a widget's own avatar |
| Avatar palette and seasonal decorations | Available | Optional checked theme data, for example snow, retaining the person's look choices |
| Original avatar SVG layers | Available | Optional per-age hat/outfit files, checked like logos; missing slots fall back to core; [ADR 0073](adr/0073-themes-supply-safe-avatar-layers.md) |
| Full avatar and face icon sets | Proposed | Per-state assets need an explicit supported-look/fixed-choice contract |
| App, PWA and notification icon sets | Proposed | Validated source assets and generated required sizes; no remote asset URLs |
| Splash artwork and dance | Proposed | A declared theme asset with reduced-motion fallback; no arbitrary startup JavaScript |
| Terminal artwork | Host-owned today | Generated core art; a plugin artwork API needs a separate contract |

A fictional winter theme can add a snow cap, falling snow and a scarf. It must preserve a readable face and accessible
state labels. Supported personal choices remain age, beard, hat colour, shirt colour and build; a fixed artwork set
must say which choices it cannot represent. Themes cannot supply executable SVG, scripts or uncontrolled CSS.
The full asset-set contract remains proposed in ADR 0065. Semantic avatar layers are available through ADR 0073.

## Background work, observability and content

| Surface | Status | Contract |
| --- | --- | --- |
| Scheduled jobs | Available | `@module.job`; daemon bounds, receipts and catch-up rules apply |
| Compiled contract jobs | Available | `CompiledJobs`; contracts are recompiled as jobs are listed |
| Event and polling triggers | Proposed | Persistent trigger data, staging and a bounded wake loop; not arbitrary background threads |
| Notifications | Available | `Notifications` policy decides delivery, deduplication and history; a plugin never bypasses it |
| Telemetry records | Available, limited | Read checked entries through `TelemetryLog`; writes use report operations; consent and sink policies still apply |
| Arbitrary telemetry streams | Proposed | A versioned metadata schema, retention, redaction and explicit consent before a team sink |
| Graph nodes and relationships | Available, limited | Team promises and recorded reads/writes feed the host graph; arbitrary plugin node renderers are proposed |
| Artifact discovery | Available | Playbook hints and declared capabilities; reads obey the contract's permissions |
| Skills and skill files | Available | Checked `module.skill` content; explicit exports and MCP representations; engine use is being built |
| Skill scripts and coding handoffs | Proposed | Bounded runner handoff, separate from loading reference text |
| Translations | Proposed | No general plugin translation-catalog API yet; supply plain labels and accessible fallback text |
| Generated plugin documentation pages | Available | `hz system plugin docs build`; the starter carries scoped page/site tooling |
| Documentation snippets | Available | Own scenes and recordings, built with the generated project’s documentation commands |

## Local view packaging and independent work

The generated starter has a small static view, SDK types, status, navigation, a Today widget and an explicit
notification operation. It needs no JavaScript bundler. A richer plugin supplies its own frontend build and packages
the resulting ES module and assets in its wheel. Export current SDK definitions with
`hz system plugin sdk <new-directory>`. The host supplies one React runtime; bundles use the exported React bridges
and must not bundle another copy.

```python
from pathlib import Path

module.view(
    Path(__file__).parent / "views",
    abi=1,
    name="inspector",
    source="read",
    title="Harbor",
    fallback="Run hz harbor read.",
    slot="panel",
    order=500,
)
```

The source is an owned read-only operation requiring no user arguments. Slots include `panel`, `page`, `sidebar`,
`settings` and `background`; a page owns `/plugins/harbor/inspector`, and a Settings view claims only its own module.
Common representations use status, action and widget declarations with explicit priorities. Views receive source
data, loading/visibility state and declared named actions through the host's ordinary confirmation gate. Missing or
incompatible bundles show the declared fallback while Python operations remain usable. No remote bundles or eval
are permitted. This is an interface boundary; installed JavaScript is trusted code in the host realm.

`make preview` uses isolated configuration/state. Restart its backend after rebuilding already-loaded view assets.
`make check` tests this plugin against an installed compatible core; `make build` packages only this plugin. The
[public plugins repository](https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins) adds per-plugin
hash-enforced locks, selected consumer checks, installed-wheel browser tests and independent release tags/provenance.
Its release command previews artifacts; publishing is explicit through the protected release workflow.

## Shared interfaces without provider dependencies

A required injectable interface subclasses the public `ServiceContract` marker in the SDK or a neutral contract
package. A provider implements it and registers `module.service(ReadingContract, Reading)`. A consumer imports only
`ReadingContract`, then declares a parameter of that type. If no implementation is installed or it is disabled, only
operations requiring it become unavailable, including dependencies through service factories. CLI/MCP/API still
build and unrelated operations work. Ordinary user data models are never guessed to be injected services.

For optional integration, inject `Kernel`, test `kernel.provides(ReadingContract)` and resolve it only when available.
Existing capability protocols use `module.provides` and declared requirements/bindings. Declare versioned
`requires`/`provides` identifiers in the owning project metadata to select affected consumer compatibility checks.

## Quality checklist for a change

1. Verify the declaration and public imports against the installed core version.
2. Give every setting one obvious home, with plain labels and help; keep CLI, MCP, REST and app wording consistent.
3. Test capability refusal, missing credentials, offline operation and a failing contribution without exposing secrets.
4. Test keyboard access, reduced motion, narrow layouts and meaningful loading and empty states.
5. Run the generated references and required checks; review the actual output in every affected head.
6. Keep private plugins and their names out of public examples, documentation, commits and reviews.

The generated [module catalog](MODULES.md), [CLI reference](CLI_REFERENCE.md) and [configuration reference](CONFIGURATION.md)
are the authority for the operations and fields present in a particular version. A proposed extension in this guide
does not make an unavailable operation callable.
