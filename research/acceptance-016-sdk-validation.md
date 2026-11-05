# Canonical 0.9.16 acceptance preparation

The canonical foreground command was exercised against the genuine published 0.9.16 SDK. This preparation is not
public-path acceptance of the composed app. That still needs its governed profile and both hosted operating systems.

## Scope of the change

The harness reads Core distribution metadata with the installed prefix's isolated Python. The version must exactly
match the case's expected version. Recognized 0.9.8 through 0.9.15 installations use `hz ui`; 0.9.16 uses `hz ui serve`.
Malformed, mismatched, missing and unrecognized versions fail before starting the foreground process. Supporting
0.9.17 requires a deliberate follow-up after checking that release's command contract.

Every private case receives explicit fictional name, handle, role, time zone and team configuration. The fixture is
recorded in its notes. It supplies noninteractive setup input without inheriting identity or credentials.

The six installed-row JavaScript predicates run in six fresh VM contexts inside one Node process. The child process
retains its aggregate five-second deadline. The batched test uses the normal five-second unit deadline, so the former
collection hook granting ten seconds to each separate row case is no longer needed. A real collection test checks the
batch and adjacent tests' effective deadlines.

## Published SDK proof

PyPI's 0.9.16 wheel metadata and the verified wheel agreed on 2,775,623 bytes and SHA-256
`b45e5a2709f6f230c0e3bde8b9305193744b04b1cb3f8c018675b33d48bc68b9`.

A private project locked `heinzel==0.9.16` from the PyPI registry. Its lock included that wheel digest. A frozen,
noneditable sync and a separate constrained `uv tool install` both installed the published SDK. All 411 runtime files
matched the verified wheel in each environment. Installed metadata, the uv tool receipt and the private managed
Python location were checked; no edited SDK or alternate compatibility build was used.

The installed tool read the five explicit profile fields. A supported configuration write saved working hours in the
private configuration directory. The harness's actual metadata-selected `hz ui serve` then served that profile and
configuration on a reserved ephemeral loopback port. Authenticated identity and profile requests succeeded;
unauthenticated requests were refused. Every referenced JavaScript and CSS asset matched the published wheel.
The foreground process stopped normally and the harness verified its cleanup.

The SDK reported no installed daemon or web service in the private fixture. No service-manager mutation was attempted.
The standard installer separately refused the host's shared live daemon domain as not quiescent. That refusal was
preserved; no manager evidence, ownership check or installation receipt was fabricated to continue it. The installed
SDK also refused managed setup without a verified standard-app receipt. Configuration remained unchanged after that
negative control.

## Remaining acceptance

This SDK proof does not establish installation of the governed standard app, plugin lifecycle success, interactive
onboarding, Linux behavior or public-path acceptance. The original published 0.9.15 failures remain failures. Literal
public installation commands, provenance and identity checks, authentication, service assertions, cleanup, deadlines
and verdict rules remain in the harness for the real composed-app runs.

## Explicit 0.9.17 contract preparation

The foreground-command allowlist now names 0.9.17 explicitly alongside 0.9.16. Unknown 0.9.18, development and release
candidate versions still refuse startup. Installed metadata must still equal the case's expected version, including
when one side is 0.9.17 and the other is 0.9.16. Every acceptance case, deadline and verdict is unchanged.

The GitHub comparison from `v0.9.16` to SDK fix PR #515 source
`e93d2d14695db11963ac59190b34d31a4b389524` lists only `src/heinzel/kernel/_installed_metadata.py` and its owning test.
There are no CLI-head or UI-module changes; the canonical command remains `hz ui serve`. There is no published
`v0.9.17` tag at this inspection, so this is source-contract preparation, not proof of installed 0.9.17 artifacts.

The actual workflow has three entry points: path-filtered `pull_request`, explicit `workflow_dispatch` and
`workflow_call`. PR runs take both versions as `published`; the selected latest app profile must select the same
Core as PyPI latest. It has no push, release or schedule trigger. A push updating this PR's changed harness paths
will request the public dry run again. Existing run `42013402847` failed on both operating systems before cases:
`PREFLIGHT_FAILED: The profile selects a different Core: a stale app is a failure`. Those failures remain required
evidence; local structural checks do not replace acceptance.

With public Core 0.9.16 and no app 0.9.16, neither a harness change nor a retry repairs that public mismatch. First
finish and review the SDK fix and this explicit support change without treating failed acceptance as passed. The
release coordinator must decide how the required failed-check policy is resolved before merging; this work grants
no exception. If proceeding to SDK 0.9.17, use the reviewed governed release source and its matching verified composed
app profile. Candidate acceptance binds the held release wheel URL, digest and matching profile and proves only those
bytes. It cannot establish the public installation path. After coordinated publication/designation, run explicit
public acceptance with matching Core/app versions and pinned digests on both operating systems. A pre-0.9.17 repair
of the public 0.9.16 app is a separate coordinated release decision. Do not substitute an arbitrary harness ref,
change defaults to hide the mismatch, waive failures or weaken acceptance gates to break the sequencing dependency.

Validation: the acceptance verdict/command tests, CI workflow structural tests, real collection deadline guard and
CLI/UI integration tests passed together: 392 tests, two workers, 11.44 seconds. Ruff lint/format and diff whitespace
checks passed. No installed 0.9.17 runtime, composed app or both-OS public acceptance was exercised; no dispatch,
rerun, push, merge, release or live service mutation was performed.
