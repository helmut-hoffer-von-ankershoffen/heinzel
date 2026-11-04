# Connected helper source continuation

Base: f7f094024. Foundation: a3e934104d1e3b01c67fe991211e9e2a9bc61928, integrated as 97b719517.
Original author source: PR #325, feat/durable-connected, 7b82dec4d28bb164d2f3619ad45ad0534161558c.

Preserved original implementation: bounded journal paging and raw-line index checks in SessionStore; private
owner/storage/query-bound search and disposable FTS caches; anchored owner-catalog traversal; conversation
navigation cursors; document draft/create tool schemas and draft identity. Adapted these to current main rather
than replacing current files. Generated API and command documentation are regenerated from this combined source.

Added current safeguards: history routes default off behind connected_history; source-bound captured-write service
remains behind captured_writes; draft tools require the active owner/session writer and accepted turn; approved
document references remain host-selected; repeat draft identities must match exact immutable terms. No draft
callback contacts any channel. Model turns retain current activity projection and do not gain history-search tools.

## Remaining original requirements

Generic channel delivery, exact transport posting consent, owner-linked actor verification, durable settlement and
reply consumption are outstanding. The original channel consumer used CurrentUser.local("cli") around automatic
confirm; that identity elevation was deliberately not imported. No channel reply can issue approval here. A safe
consumer must carry the original exact per-write trusted approval boundary and cannot treat plugin/model text as
a trusted decision. Per-helper channel consent and transport source continuity still require implementation.

The original web history/search interface, captured-write activity views, new-file provider acceptance, tracker
capability extensions and full connected helper onboarding remain outstanding. No concrete provider implementation
or credential scopes were added. Standing rolling-document append authority is a separate strongest-reviewed
design, not implemented by this continuation. This is source preparation, not full PR #325 or that journey's delivery.

## Validation boundary

Offline fixtures only. No real provider, sign-in, live installation, public push, PR, merge or release. Focused test
results and independent security review belong to the eventual PR body; ordinary gate access yields to current CI
and startup repair.
