---
status: proposed
date: 2026-11-04
implementation: planned
decision-makers: maintainer
related: 0003, 0006, 0020, 0117
---

# 0132: Retire legacy daemon definitions only through an explicit bound recovery plan

## Context and Problem Statement

Released daemon units and install records did not preserve which configuration profile was selected. Exact released
bytes, an executable path and a dead PID therefore cannot prove historical profile ownership or authenticate a
loaded job sharing the fixed label. Ordinary installation and removal must preserve unknown legacy definitions,
but that refusal leaves existing installations without a supported path to the new profile-bound unit format.
The observed default-profile refusal and installed-SDK checks are recorded in the
[daemon ownership research](../../research/daemon-profile-ownership.md).

## Considered Options

- **Explicit digest-confirmed retirement, then ordinary installation** (chosen).
- **Automatic retirement during installation or update**: would treat historical profile uncertainty as authority.
- **Adopt the old record into the new schema**: would imply profile evidence that the old SDK never captured and
  couple old-code recovery to activation of a new service.
- **Delegate cleanup to the old CLI or edit its record**: would reuse the unsafe ownership policy or manufacture
  evidence instead of verifying it.

## Decision Outcome

Chosen option: **explicit digest-confirmed retirement, then ordinary installation**, because the person can
approve removal of precisely observed legacy integration without asserting historical profile ownership.

1. **Independent candidate execution.** The documented `uvx --from <candidate> hz-daemon-recovery plan|retire|restore` console works before updating
   the persistent SDK, with ordinary typed parsing over one narrow recovery port. It runs candidate code with plugin discovery disabled, independently resolves the normal persistent
   standard-app target, and verifies its receipt, owned links, interpreter/source generation and continuity without
   executing that old SDK. There is no arbitrary target, generic force flag, old-CLI delegation or implicit call
   from installation, update, onboarding or ordinary removal.
2. **Read-only plan.** Planning creates no recovery state, backup, unit or manager mutation. It requires exact
   recognized released unit bytes, a safely owned compatible legacy marker, the verified persistent target, the
   fixed manager and user session, and current default-profile routing. Explicit or different routing, foreign or
   changed definitions, missing targets, active ticks/jobs, pending lifecycle work and ambiguous runtime evidence
   refuse. Loaded path, program, complete argv and effective environment must agree; inherited configuration is
   observed rather than inferred from a template or executable name.
3. **Honest intent.** The plan says that historical profile ownership is **unknown**. It names the exact target,
   unit and marker, manager registration/activation, current default routing, preserved backup and effects. Its
   digest binds those facts, exact file bytes and physical identities, and the target generation. Confirmation
   authorizes only retirement of that integration; it does not grant adoption, new activation or data deletion.
4. **Fenced execution.** Explicit confirmation reacquires lifecycle and tick authority and recomputes the plan.
   Any drift refuses before effects. A durable private transaction and ordinary lifecycle gate precede the first
   manager request or move. Only the verified loaded identity may be stopped, and exact original unit/marker bytes
   remain in a private preserved backup. Pause, receipts, schedules, configuration, credentials and other data stay.
5. **Truthful failure.** Completion requires verified manager absence and exact file retirement. A failure restores
   original files and registration/activation only when the before-state can be verified; otherwise the durable
   gate and journal remain, and no success or successful rollback is claimed. Interrupted or uncertain requests
   are not retried automatically. Explicit recovery may restore only the journal-bound original state after all
   current authority checks pass; foreign state is never overwritten to make recovery finish.
6. **Terminal separation and replay.** A completed retirement has its own durable terminal receipt, not an
   `install.json` or a new source-continuity checkpoint. It releases only its own ordinary blocking gate so the
   genuine installed SDK's supported updater can run. Repeating the same confirmed digest returns the historical
   completed receipt without retiring newly installed files or requiring their generation to remain the old one.
   A different digest is a new plan and consent. Failure/recovery receipts never impersonate successful retirement.
7. **Ordinary installation remains ordinary.** After retirement, the installed old SDK's normal updater performs
   the package update, then the new SDK's ordinary installer creates its own profile-bound record and separately
   requested activation. No package update, activation or provider action is hidden inside retirement.

### Consequences

- Good: the historical authority gap is explicit, exact legacy bytes are preserved, and candidate recovery does not
  depend on importing old plugins or starting the old SDK to obtain authority.
- Bad: unknown manager/environment evidence and unsupported profiles continue to refuse. Explicit recovery adds a
  separate consent and durable transaction; it must not be presented as automatic migration or OS acceptance.

### Confirmation

Review only while planned. Completion requires installed-artifact tests of genuine published legacy creation,
explicit retirement, the actual old SDK's normal updater and ordinary new installation, plus stale/foreign,
replay-after-new-install and partial-failure cases; protocol doubles and real manager evidence remain distinct.
