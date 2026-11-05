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

Generic channel posting now has exact per-helper/per-item human consent, captured transport source/destination
binding, a durable attempt-before-effect outbox, owner-linked reply verification and host-only captured-write outcome
consumption. The daemon identity is preserved; the original `CurrentUser.local("cli")` elevation remains excluded.
Complete serialized write terms must fit the channel preview; no content is silently truncated. Missing posting or
write acknowledgements and interrupted decision claims never trigger automatic replay. Settlement can retry closing
only the same posted request. Compatible transports must implement `delivery_target` and conditional `post_reviewed`
/ `deliver_reviewed`; existing implementations fail closed. No concrete plugin is imported or changed.

Remaining channel requirements include owner-facing selection/onboarding UI, schedule-bound standing notice consent,
provider implementations of the conditional posting contract, installed published SDK compatibility, real human review
and actual delivery acceptance. Exact per-item offline confirmation does not establish those outcomes.

The original web history/search interface, captured-write activity views, new-file provider acceptance, tracker
capability extensions and full connected helper onboarding remain outstanding. No concrete provider implementation
or credential scopes were added. The bounded release-draft journey now has a separate registered owner CLI
publication, five-minute daemon job,
local review notice and exact edited-revision link approval. Arbitrary captured writes retain their separate approval
path. These source changes do not establish full PR #325 or real provider delivery.

## Validation boundary

Isolated fixtures only: the release-draft slice is registered without preview flags and has been exercised through an
installed CLI and actual daemon tick with source-declared provider doubles. The other incomplete connected-history
and arbitrary-write paths retain their flags. No real provider account, sign-in, live Mini/Air change, public push,
PR, merge or release was performed. Focused test results and independent security review belong to the eventual PR body;
ordinary gate access yields to current CI and startup repair.

## Channel consumer regression evidence

An ordinary gated focused run passed 76 tests across channel contracts, public SDK exports, channel consent and
reply consumption, captured-write host checks and installed-source chat fixtures. These are offline transports and
providers. The authenticated-head fixture posts nothing before separate posting confirmation, writes nothing merely
because an approval request was posted, then records one captured mutation for the pinned owner reply and none for
replays. Source/actor/digest/request/time/destination/scope/revision drift, stale drafts, unknown effects, settlement
failures, interrupted claims, fresh owner-service reconstruction and concurrent consumers are covered.

Full source mypy and Pyright passed; generated command/API references and web schema were regenerated. The full
application/browser suite, hosted CI, publication/installation of this new SDK, independent security review and real
provider/human delivery acceptance were not run for this continuation. Original UI, tracker and Turing obligations
above remain open. This evidence is source preparation, never a claim of accepted release or live delivery.

## Registered release-draft source scenario

`tests/e2e/draft_workflow_test.py` installs the candidate wheel and a source-declared provider double into a private
uv tool profile. A genuine terminal publishes one natural-language prompt as Turing's first message. The supported
`hz system daemon tick` finds the registered five-minute job, appends one marked draft and queues an ordinary local
owner notice, with no link share before approval. A human-style document edit adds a dot. A further edit during the
trusted CLI preview invalidates consent; a fresh exact review retains that edited section and revision in the receipt.
New daemon and approval processes do not duplicate the append or share. Separate installed cases lose the append and
share acknowledgements and prove that later processes refuse replay. These are isolated provider doubles, not real
Google Docs, GitHub or Slack acceptance. No live service or owner installation is changed.
