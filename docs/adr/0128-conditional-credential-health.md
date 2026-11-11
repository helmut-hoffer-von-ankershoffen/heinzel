---
status: proposed
date: 2026-11-04
implementation: partial
decision-makers: maintainer (delegated)
amends: 0030
amends-0030: conditional credential health and unused fallback
related: 0057
---

# 0128: Optional credentials do not degrade unused features

## Context and Problem Statement

The credential inventory currently marks both AI authentication alternatives missing even when a
different signed-in provider is selected. Normal installation must not require unused credentials.

## Considered Options

- Explicit optional declaration with health owned by the selected feature (chosen).
- Inferring requirements from credential names couples core to particular providers and plugins.

## Decision Outcome

Chosen option: **explicit optional declarations**, because feature health knows the active authentication path.

A credential declaration has `required=True` by default, preserving existing plugin contracts.
A module may set `required=False` when its own health check evaluates conditional authentication,
including the selected provider and valid alternative sign-in methods. An absent optional credential
is reported as not stored, without degrading system health. This is inventory, not a claim that a
provider is authenticated. AI access checks the selected provider; its API key and ChatGPT sign-in
are alternatives and are not independently mandatory.

This amends [ADR 0030](0030-credentials-live-in-the-os-keychain.md): missing mandatory secrets still
degrade health. Shared secrets remain mandatory when any declaration requires them. Disabled modules
do not contribute declarations. Plugins use this generic declaration contract; core does not know
specific plugins or infer feature intent from credential names.

All declared credentials are still read through the credential service, so backend errors remain
visible. Colliding variable names and explicitly unsafe keyring backends remain degraded. An unused
environment fallback with no mandatory or stored secrets is neutral; once a secret is present or
required, its existing security warning remains. No storage, redaction, access or installation guard
changes. The declaring feature must not mark a credential optional without reporting its actual
selected authentication requirements.

### Consequences

Existing declarations remain mandatory. Authors of optional authentication must supply accurate
selected-feature checks; marking a credential optional is a health contract, not an authentication grant.

### Confirmation

Regression tests cover unused alternatives, shared mandatory declarations, selected provider health,
backend failures and storage safety. First-install onboarding separately completes required profile
fields before reporting setup complete. Published installation and both-OS acceptance remain required;
focused tests do not establish release acceptance.
