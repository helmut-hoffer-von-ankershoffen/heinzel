---
status: proposed
date: 2026-11-04
implementation: partial
decision-makers: maintainer and independent security reviewer
amends: 0028
amends-0028: distinguish explicitly deferred on-demand health from mandatory execution requirements
related: 0128, 0129
---

# 0130: On-demand health preserves runtime requirements

## Context and Problem Statement

A fresh installation can operate without an AI account. The person must explicitly defer AI setup instead of being
assigned a fake provider or having a selected broken provider hidden. Inventory and configuration commands do not
consume AI, while an actual ask, chat send or scheduled contract still requires its declared capability.

## Considered Options

- **Health-only on-demand metadata with explicit unbound intent** (chosen).
- Use optional execution requirements or disable whole modules, which would change execution and hide real failures.

## Decision Outcome

Chosen option: **health-only on-demand metadata with explicit unbound intent**, because deferring an on-demand
feature must not grant it execution authority.

- An operation may declare `health_on_demand=True`. Only its own unpinned requirements can then be neutral in health,
  and only while the binding is explicitly off. Module, job, compiled and pinned requirements retain their findings.
  The matrix keeps the unmet problem visible and labels the feature not configured.
- This declaration does not change runtime requirement validation, capability injection, modes, provenance,
  availability or confirmation. It never sets `optional=True`. An attempted call still fails before provider code.
- Inventory and configuration modules declare requirements on actual consuming operations, not their entire module.
  Selected missing credentials, broken providers, unknown bindings and unresolved hosts remain findings.
- First interactive setup offers an explicit **Set up AI later** choice. It persists supported literal `none` bindings
  through ConfigService and preserves existing explicit selections. The AI access settings view normalizes that
  supported value consistently with the kernel. No account, authentication proof or replacement provider is invented.
- Active compiled requirements remain degraded without their runner, even when all interactive operations are deferred.
  Corrupt contracts, unsafe stores and failed installation or service checks retain their existing failures.

### Consequences

- Good: an intentionally account-free installation can be healthy without weakening execution gates.
- Bad: operation declarations carry a separate health contract whose defaults and non-execution semantics need tests.

### Confirmation

Focused tests distinguish explicit deferral from absent, unknown, selected broken and active compiled requirements.
Execution tests retain mandatory failures before subprocess or network calls, and setup checks the real doctor result.


Module inventory grants are separate: `inspects=["ai-access:read"]` authorizes only the capability's explicit
`inspection_methods` allowlist through `Adapters` injection. For AI access that is `status`, never the paid `test`
method. A grant must be an unpinned read and the capability must declare inspection methods; capability declarations
reject unknown or write inspection methods. Bound capability injection never consults this authority.

Configuration and inventory services retain only these restricted handles. Live-test handles are injected into the consuming
operation under its own ordinary runtime requirement, passed for that call, and never cached on the module service.
