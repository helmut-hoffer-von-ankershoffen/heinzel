---
status: proposed
date: 2026-10-30
decision-makers: maintainer
amends: 0044, 0095
related: 0094, 0110
implemented-by: 304
---

# 0111: Plugins recommend the default theme with a Core recovery fallback

## Context and Problem Statement

The standard theme was extracted from Core, so its palette and type scale match Core's recovery appearance.
The picker consequently showed two nearly identical themes and left the installed standard theme inactive.
The standard app should select its plugin-provided appearance automatically without a provider name in Core.

## Considered Options

- **Let themes opt into a generic default priority** (chosen).
- Hardcode the standard theme's name in Core: violates the plugin boundary.
- Let a provider take the reserved `default` identity: conflates provider assets and theme-scoped soundscapes with
  the recovery fallback and conflicts with existing identity guards.
- Change a person's saved theme during installation: loses explicit choices and couples setup to concrete providers.

## Decision Outcome

Chosen option: **optional `Theme.default_priority`**, because the provider can declare its intent while Core owns
only the selection policy and its emergency fallback.

- `None` opts out. Strict integer priorities from zero through one thousand opt in. Resolve active, non-conflicting
  ThemeLets first, then prefer higher priority; ties use qualified module name followed by theme name.
- An unset theme chooses the recommendation. Explicit available choices, including Core's reserved `default`, win.
  A missing or invalid choice uses the recommendation while preserving the configuration and explaining recovery.
- Core's fallback remains usable without plugins or when explicitly selected. When inactive and a provider recommends
  a default, it is omitted from the picker; when shown it is labeled "Built-in fallback" to distinguish its purpose.
- The standard theme plugin owns its preference and retains its own identity, assets and soundscape associations.
  Selection never starts playback, sharing, background work or any external operation.

### Consequences

- Good: a fresh standard app has the intended appearance, the duplicate card disappears, and Core stays independent.
- Bad: manifests using the new optional field need a compatible minimum SDK; release Core support before the provider.

### Confirmation

Core theme tests use synthetic providers to verify priorities, ties, disabled/conflicting providers, explicit choices,
missing selections and provider removal. The owning standard-theme acceptance verifies its chosen theme and assets
against the released SDK. No Core test imports or names that concrete provider.
