---
status: accepted
date: 2026-10-29
decision-makers: maintainer
amends: 0094, 0095
related: 0008, 0013, 0079
implemented-by: 270
---

# 0096: Release the plugin boundary at the first unused version

## Context and Problem Statement

The approved plugin boundary targeted 0.9.0. Before its pull request merged, another release workflow published
[core 0.9.0](https://github.com/helmut-hoffer-von-ankershoffen/heinzel/releases/tag/v0.9.0)
from the existing architecture, including six legacy plugin artifact sets. The maintainer approved continuing
with 0.9.1. Published identities must remain immutable, and plugins must reject a core without the new contracts.

## Considered Options

- **Advance the affected artifact identities to 0.9.1** (chosen).
- Reuse 0.9.0: would give one version incompatible payloads and cannot replace an existing PyPI upload.
- Hold the release: the maintainer authorized completing the approved scope.

## Decision Outcome

Chosen option: **core 0.9.1 is the first release of the approved plugin boundary**, because 0.9.0 is already public.

- The release boundary and rollout references to 0.9.0 in ADRs 0094 and 0095 now target 0.9.1. Their recorded
  measurements remain historical observations of the candidate used at that time.
- No additional module extraction is included. Resource remains the only extracted built-in module. The
  standard composition includes Clock, Resource, Tailscale and the Heinzelmännchen theme with its soundscapes.
- Every migrated plugin requires `heinzel>=0.9.1,<0.10`. Clock, Tailscale, Atlassian, GitHub, Google and Zoom advance
  their own artifact identities to 0.9.1. The other projects retain their independent, unpublished versions.
- Official version tooling stages the reviewed candidate. Automatic release finalizes that candidate when its
  metadata version exceeds the highest canonical reachable release tag; otherwise conventional commits select the next
  version. A forced bump still advances the current metadata version. Existing tags and artifacts remain intact.
- Plugin checks install the candidate wheel, then the released wheel once available. Each plugin retains its own
  dependency lock, selected checks and release. Bootstrap compatibility pins are removed after publication.
- One shared `heinzel-plugins/release.yml` workflow publishes through the main-only `pypi` environment. Each PyPI
  project grants that identity access separately. First-time registration may be serial where PyPI requires it;
  this does not couple subsequent plugin releases. No aggregate Python package or persistent publishing token
  replaces these independent artifacts.
- Publish `app-v0.9.1/standard.json` only after its provenance and fresh-install checks pass. Installation remains
  the approved clean-install boundary; ordinary subsequent updates preserve explicit plugin choices.

### Consequences

- Good: consumers cannot silently install the legacy 0.9.0 implementation for the refactored plugin contracts.
- Good: independent plugin delivery and the reduced local feedback loop are retained.
- Bad: release notes must distinguish the new architecture from the immediately preceding 0.9.0 release.

### Confirmation

`tests/unit/release_version_test.py` checks candidate finalization, ordinary version advancement and invalid
identities. The release-script integration checks exercise actual version-tool and tag behavior in disposable
repositories. Each owning plugin check validates its dependency lock and rejects an incompatible core artifact.
Planner tests select an arbitrary changed plugin and its declared contract consumers while selecting no core
build or test work. The composition workflow checks the four metadata-enrolled defaults in a fresh installation.
PyPI publisher grants and published release identities require observed verification during release review.
