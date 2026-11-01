---
status: proposed
date: 2026-10-30
implementation: built
related: 0078, 0099
implemented-by: 295
---

# 0106: Bind connected providers to source-only loading

## Context and Problem Statement

Connected-read reviews bind a selected provider and source, but ordinary discovery imports plugin entry points before
recording their origin. An in-place update can therefore make disk contents differ from code already executing. Python's
normal timestamp bytecode cache can also load older code after a same-size, same-timestamp source replacement. A plugin
cannot establish this boundary by checking its own source after its initializer has already run.

## Considered Options

- **Host-controlled source-only provider loading with a closed graph** (proposed).
- **Plugin self-reported file hashes**: the verifier itself may already be stale.
- **Assume installed files or version metadata identify loaded code**: neither proves which code was executed.
- **Rely on managed immutable deployments**: desirable, but not yet enforced for ordinary installed plugins.

## Decision Outcome

Chosen option: **host-controlled source-only provider loading with a closed graph**, because a trusted host must capture
and compile the bytes before provider code runs. Ordinary plugin discovery remains unchanged. A connected provider is
eligible only through a separately reviewed admission boundary that binds the exact owner-selected provider, source
graph, account and operation. Installed `heinzel.connected_sources` metadata may request capture only when it matches
the same distribution's ordinary module entry point; it does not assert trust, grant access, or select additional runtime
dependencies. The host checks the installed manifest and owns the trusted runtime baseline and the process-lifetime
capture catalog. Ordinary boot enables the guard; providers lacking a verifiable declaration remain available to ordinary
heads but cannot use the guarded connected-read path. Unknown dependencies, preloaded code, native extensions, unsupported loaders, graph
changes and ambiguous import ownership fail closed. The host rechecks the graph and grant before each operation and
fences later in-flight calls when changes are observed. A running request cannot be retroactively undone.

Captured plugin code may to import only already-loaded host runtime modules and attributes sealed
after builtin discovery. It checks standard-library origins and loaders, the installed Pydantic manifest, and the
trusted running core's source root. A scoped import function returns the exact sealed objects or loads the plugin's
captured package; missing runtime dependencies are never loaded on the plugin's behalf. Sealed objects and specs are
rechecked before use. An ineligible plugin can still use ordinary discovery, but cannot use the source-guarded
connected-read path. An embedder constructing `Kernel` without a source catalog retains responsibility for provider
trust and does not obtain this guard.

The public `captured_source_active()` predicate reports whether its exact calling module is executing under the host's
source loader with a verified snapshot and sealed runtime. It is false outside that initialization context and does
not grant permission or establish publisher trust. A plugin may use it to omit its captured-read service on older
cores or ordinary loading; current source binding and human approval remain separate requirements.

The fixed host initialization imports `email.policy` for general MIME parsing before external plugin discovery. That
bootstrap and the Python interpreter's own imports are part of the trusted host runtime, not verified plugin code;
the source guard does not claim to prevent shadowing before that trusted initialization. No plugin declaration can
extend this bootstrap. A missing runtime module or attribute makes the plugin ineligible for guarded reads.

A source digest establishes local continuity, not publisher authentication, immunity to same-user interference, or
provider account authority. Isolated provider processes or verified immutable deployments may be needed to preserve
ordinary module identity while applying this boundary. Do not reload or replace live plugin modules to obtain a digest.

### Consequences

This adds startup, compatibility and dependency-inventory work. Existing connected providers must add a matching
metadata declaration and use a verifiable non-editable installed distribution before guarded connected reads work;
unsupported plugins continue to work through ordinary heads but cannot receive this authority. Default boot enables
source capture for matching installed declarations; manually constructed kernels without the catalog remain an explicit
embedding trust boundary. The loader supports bounded pure-Python package trees and an already-loaded host runtime.
Native plugin extensions and unloaded runtime dependencies remain ineligible for guarded reads.

This is a continuity mechanism for owner-selected plugin code, not a Python sandbox or publisher attestation. Static
import inspection and the scoped importer bind ordinary imports to captured bytes or sealed runtime objects. Python
reflection and dynamic imports remain capabilities of the already trusted installed code; the regression demonstrating
a dynamic helper import records that limit. Do not present the digest or capture predicate as proof that arbitrary
plugin code is safe, immutable against a same-user adversary, or authorized to use an account. Exact human-approved
account, operation and source grants remain necessary before a connected read.

### Confirmation

Focused tests exercise source-only compilation despite stale bytecode, refusal of already loaded packages and detection
of source changes. Independent review must establish closed dependency graphs, verified provider identity, current
binding checks and cancellation semantics before any real provider is admitted.
