---
status: proposed
date: 2026-10-23
related: 0004, 0017, 0025
---

# 0026: The plugin API is versioned with SemVer, and discovery checks each plugin's compatible core range

## Context and Problem Statement

Plugins import Heinzelmännchen's public API and are discovered through the `heinzel.modules` entry point group
([ADR 0004](0004-discovery-through-packages-and-entry-points.md)). External plugins are released on their own
schedule, so a core update can break a plugin that was installed weeks earlier, and today the only symptom is an
import error or a wrong result at run time. What is the plugin API, how is it versioned, and what happens when a
plugin does not fit the installed core?

## Decision Drivers

- Plugin authors need to know what they may rely on and when it may change.
- Users must not be surprised: an incompatible plugin is named, with a fix, instead of failing in the middle of a job.
- Before 1.0 the API still moves; versioning must allow that without pretending to be stable.
- Discovery stays cheap and never fatal for broken plugins (ADR 0004).

## Considered Options

- **SemVer on the public API, a declared compatible core range per plugin, checked by discovery** (chosen)
- **Rely on the `heinzel` dependency specifier only**: uv resolves it at install time, but `hz system update` can
  move the core past it within the same tool environment, and nothing reports why a plugin broke.
- **A separate plugin API version number (like an ABI level)**: a second number to keep in sync with the release,
  for no gain while the API is the package's own exports.
- **No versioning until 1.0**: leaves plugin authors guessing for the period in which the API changes most.

## Decision Outcome

Chosen option (proposed): **the plugin API follows the core's SemVer version, every plugin declares the core range it
is compatible with, and discovery loads only compatible plugins**, because authors get a contract and users get a
clear report instead of a run-time failure.

- **Public API**: the names in `heinzel.__all__` and the let types they expose, with their documented fields and
  behaviour. Everything else (`heinzel.kernel.*` internals, heads, built-in modules) is private and may change in any
  release.
- **Versioning**: the core's version is the API version. From 1.0, a breaking API change needs a major release.
  In 0.x, a minor release may break the API, but only after the change was announced by a deprecation (a
  `DeprecationWarning` and a changelog entry) at least one minor release earlier.
- **Declaration**: a plugin declares its compatible core range as the version specifier of its `heinzel`
  dependency (for example `heinzel>=0.2,<0.3`); the template generates it from the core it was rendered for.
- **Check**: discovery compares the installed core version with each plugin's declared range before importing it.
  An incompatible plugin is not loaded; `hz system info` and `hz system doctor` report it with its range, the
  installed core version, and the fix (update the plugin or pin the core).

### Consequences

- Good: plugin authors know what is public; breaking changes arrive with a deprecation release in between.
- Good: an incompatible plugin is reported at boot, not discovered during a job.
- Bad: the release process must track deprecations and refuse to remove a deprecated name without one minor release
  in between.
- Bad: plugins with an open-ended range (`heinzel>=0.2`) are loaded and may still break; the template and review
  steer authors to an upper bound.

### Confirmation

Not yet: proposed. When implemented: unit tests for the range check in discovery, an integration test that an
incompatible plugin is reported by `hz system info --json` and `hz system doctor` and not loaded, and a test that
`heinzel.__all__` matches a committed API listing so changes to it are visible in review.

## More Information

- First-party plugins in `packages/` ([ADR 0017](0017-first-party-plugins-in-the-core-repository.md)) are released
  with the core; once this is implemented they will pin it exactly, so the check always passes for them.
- Plugin naming: [ADR 0025](0025-plugin-distributions-are-named-heinzel-name.md). The stable plugin API is the
  milestone for 1.0 in [INTENT.md](../../INTENT.md#where-we-are).
- Open question: whether to accept an explicit `[tool.heinzel] requires-core` key as an override for plugins whose
  dependency specifier must stay wider than their tested range.
