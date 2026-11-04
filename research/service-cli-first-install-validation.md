# Explicit service CLI and first-install validation

This is source preparation for the next release. Published artifacts are unchanged. No OS integration, default
profile or live service was changed during this work.

Bare web-group discovery is inert. Foreground serving and opening an existing owned service have explicit commands.
Owned launchers from the published format, published updater trial arguments and inherited-socket resumes retain
compatibility without accepting modified launcher bytes. The versionless launcher uses offline command discovery
for older verified standard apps and refuses unsupported new service choices before mutation.

Interactive setup offers three independent default-yes choices. Daemon installation does not imply notifier
installation; explicit notifier consent remains separate. Every selected operation runs in the verified persistent
application. Readiness, loaded identity, persistent target and changed-installation observations gate completion;
notifier installation, permission and visible delivery remain distinct outcomes.

Profile questions use the actual profile's missing-field list and ConfigService schema and writes. They ask for
missing name and team name as requested, plus genuinely required profile fields, preserving existing values and
optional credentials. Setup completion requires an actual managed doctor result of up.

## Focused evidence

Three regression contracts fail against the original production source: inert bare web help, web installation
without a preview setting and separate interactive choices. The unchanged original source was temporarily selected
inside this worktree, all local changes were restored, and the original failures were retained in private test output.
On the new source, 494 focused CLI, profile, service ownership, session, device, updater and daemon cases passed with
two workers in 15.12 seconds. The profile-specific tests write actual configuration through the normal CLI service
boundary, preserve existing values and reject non-green setup completion.

Three real installed-wheel cases passed in 25.80 seconds: both platform-adapter session/update/cold-restart journeys
and inert installed web-group discovery with retained selected-profile refusal. These are isolated service-manager
adapters, not OS acceptance. Both Chromium and WebKit mounted phone daemon-install/removal cases passed in 10.47 seconds, retaining the actual manager hold, physical confirmation, accessibility and no-horizontal-scroll assertions. Eighteen owned Electron CLI controller tests passed. No deadline or physical interaction
was substituted; the daemon browser's named-step count now excludes the unconsented notifier step.

The actual isolated profile-complete doctor report is still degraded on this source: default AI access and runner
login are absent, the two alternative AI credentials are reported missing, and the test interpreter is an unmanaged
source environment. Notification health is up with the genuine fresh default. No successful aggregate installation
or OS/provider acceptance is inferred from this report. The explicitly requested account-free AI intent and optional
credential health composition remains required before this can claim green first-install completion.
