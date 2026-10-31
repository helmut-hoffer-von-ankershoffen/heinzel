---
status: proposed
date: 2026-10-30
decision-makers: maintainer
amends: 0094
related: 0097, 0098
amended-by: 0114
---

# 0110: One versionless launcher installs and opens the standard app

## Context and Problem Statement

The maintainer requires installation to be a breeze: no version numbers, plugin lists or conflicting instructions.
The released bootstrap SDK looked for an app profile with its own version, although SDK and app composition ship
independently. A newer SDK therefore broke the documented installer when its matching composition was absent.
The PyPI description also reused repository-relative artwork and links that do not resolve on PyPI.
The published installer later failed at anonymous GitHub tag discovery when the public API quota was exhausted;
that network failure was reported as an undifferentiated artifact-verification error.

## Considered Options

- **Use `uvx heinzel@latest` as one versionless install-and-open launcher** (chosen).
- Ask people to pin a complete app version or enumerate plugin dependencies: exposes release mechanics in onboarding.
- Couple every SDK release to rebuilding every plugin: contradicts independent delivery and slows development.

## Decision Outcome

Chosen option: **one versionless launcher over verified, independently published app compositions**, because people
should choose the product rather than coordinate its distributions.

- Bare `uvx heinzel@latest` installs the newest published standard app when a complete managed app is absent, then opens
  its foreground web workspace. Later launches use the installed app without registry or provider discovery.
  Ctrl-C stops it; no daemon, login item or sharing is enabled by installation.
- Runtime arguments are delegated to that same managed app so the UI and CLI see the same plugins. Informational
  help/version flags remain available without installation. `hz` remains the independently usable SDK/CLI entry point;
  `hz-install` remains the lower-level installer for automation and explicit candidate profiles.
  A namespace-independent entry shim re-executes the selected interpreter in isolated mode before importing Core.
  All three packaged commands preserve their roles while ignoring stale checkout paths; managed delegation also
  removes those Python path overrides from its descendants.
- The documented command uses `@latest` to refresh a stale bootstrap cache or bypass an older installed SDK.
  It names no release number and selects the same complete app profile; plain `uvx heinzel` remains supported
  after a current launcher is installed. This follows [uv's documented tool version behavior](https://docs.astral.sh/uv/concepts/tools/).
- An optional POSIX `curl`/`wget` installer supports macOS and Linux. It reuses existing uv or installs uv through
  Astral's official HTTPS installer, then invokes the same versionless launcher. It needs no administrator access
  or preinstalled Python. Windows may work independently but is unsupported. The script is served with the docs;
  README, PyPI and Pages share its command and link to its inspectable source.
- The installer resolves GitHub's governed latest release to a canonical stable `app-v` tag without the anonymous REST API.
  Every redirect is checked before following it. It fetches that immutable tag's profile and requires its existing
  `core_version` identity to match the app tag, independently of the running bootstrap SDK's version.
  The default profile's packages must belong to the same official immutable app release; their identities, sizes and hashes
  remain checked. GitHub release downloads may redirect only to the recognized GitHub release-asset origins.
  Explicit candidate profiles remain exact. No failure silently falls back to an older app or another authority.
  The serialized composition workflow is the sole latest-app writer: app publication explicitly designates latest,
  while plugin/tool publication explicitly opts out. An older composition retry cannot supersede a newer designated app;
  an unknown current app identity blocks promotion. The comparison is not an atomic fence against writers outside this protocol.
  This uses [GitHub's latest-release policy](https://docs.github.com/en/rest/releases/releases), which excludes drafts and prereleases.
  Core never infers plugin names from this mechanism. Safe errors distinguish discovery, download and hash verification,
  retaining HTTP status and quota exhaustion without exposing URLs, signed query strings, headers or credentials.
  Publication is the composition producer's completeness assertion. After a manifest is published, a missing or
  corrupt referenced wheel fails closed; it does not silently select an older app and hide damaged release state.
  App format 2 declares support for this startup-continuity lifecycle, independently of plugin ABI `sdk=1`.
  A designated legacy profile is refused before installation, just like an explicit legacy profile.
  The producer first asks the accepted SDK's existing installer to validate a format 2 profile without installing
  plugins. Before exposing a profile, it also installs the exact selected wheels in disposable uv tool directories
  and verifies offline launch, plugin add/remove/reinstall, and an update's actual runtime and startup checkpoint.
  This focused artifact acceptance reuses independently released providers and never runs Core's full suite.
  It never relabels an immutable legacy composition. A newer launcher may select an older compatible
  format 2 app, while an absent compatible app fails clearly instead of creating another legacy installation.
- A successful managed install records its verified composition privately. The launcher checks the current owned
  installation without importing plugins and replaces itself with the managed executable.
  Managed binaries lead the child PATH so a daemon or subprocess never retains the temporary bootstrap path.
  Only the verified standard installer may seed installation continuity (ADR 0108). Package mutations preserve an
  already matching checkpoint, including its current membership, so independent updates, removals and authorized
  provider drops survive relaunch. A plain SDK cannot become a standard app by combining a new generic checkpoint
  with another installation's stale app receipt. Missing or changed evidence refuses with scoped inspection;
  startup never endorses that state or reapplies an older composition automatically.
- Installation binds uv's verified store and executable directory and refuses foreign or unresolved command links.
  A failed uv refresh command reuses the kernel's receipt-bound recovery, probing the actual managed interpreter and
  preserving its base Python. A successful command followed by an artifact mismatch retains the unverified state
  with scoped inspection instructions; it cannot safely roll back a possible concurrent installation.
  Initial failures retain ambiguous partial state with explicit recovery instructions: a newly appearing
  receipt cannot prove that this operation owns the directory, so it must never trigger recursive deletion.
  Installer and metadata-probe subprocesses use the shared credential-filtered environment.
  The head's command-collision preflight inspects only Heinzel's names; kernel receipt and recovery checks remain
  authoritative for every managed refresh.
  uv records the installer's temporary verified wheel overrides in its receipt. Only the exact newly generated
  artifact selections may be normalized after their installation is independently verified, through the kernel's
  guarded receipt boundary and before recording continuity. The resulting receipt keeps index-following intent and
  installed wheel provenance. Existing unsupported resolver policy is refused before mutation; user constraints
  are preserved. Normalization must verify the exact receipt-only change or repeat checks bound to its resulting
  generation, so it cannot endorse unrelated concurrent changes.
- The plugin repository composes and verifies app profiles against released SDK artifacts independently. A scheduled
  check publishes missing compositions without rebuilding provider artifacts or running Core's test suite.
- README, Pages and PyPI take their primary command from one checked source. Public onboarding never asks people
  to supply versions or enumerate defaults. PyPI uses a dedicated portable description with absolute links and
  raster exports of the actual project artwork.
- A repository URL can select its single metadata-declared plugin project in a virtual workspace. Collection
  discovery reads bounded metadata at one immutable commit; GitHub-resolved links stay within that repository and
  commit, and ambiguous collections require a distribution name.
  Adding a plugin verifies fresh installed discovery before recording success, including when the package installer
  exits successfully without installing a plugin.
- The UI's explicit update action remains available when Core is current, because plugin releases are independent.
  Inspecting status never mutates packages; deliberately unsupported or pinned installations retain their refusal.
  Installing and removing plugins both stream progress and use the bounded package-mutation budget, because either
  operation can rebuild the environment. Readiness and restart requests retain their short transport deadlines.

### Consequences

- Good: first use is one command, defaults are automatic, and SDK/plugin development remains independent.
- Bad: app release discovery, a managed-install receipt and the launcher are additional generic boundaries to maintain.

### Confirmation

`tests/unit/heads/install_test.py` covers API-free discovery, canonical redirects, immutable profile/package authority,
explicit overrides and safe failed verification. The producer's `tests/publish_test.py` and `tests/workflow_test.py`
cover monotonic retry promotion, unknown identity refusal and the serialized writer's explicit latest policy.
`tests/unit/heads/bootstrap_test.py` covers first use, existing/offline use, incomplete SDK installs and CLI compatibility.
`tests/unit/pypi_description_test.py` checks the one-command contract and portable artwork.
`tests/integration/shell_installer_test.py` exercises prerequisite installation and failure cleanup without network.
The plugin repository's composition tests cover scheduled no-op and new SDK selection. Release acceptance exercises a fresh versionless
installation and subsequent launch with independently verified default plugin wheels.
