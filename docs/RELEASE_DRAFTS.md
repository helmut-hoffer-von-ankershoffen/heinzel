# Release poems in one rolling document

Create a named Heinzel with one natural-language prompt through the ordinary CLI. Install and configure a
source-declared code-host provider, a conditional draft-append documents provider and exactly one reviewed-link
channel first. The review notice uses the existing local notification policy; it does not send an external message.

```sh
hz heinzel start-release-workflow my-release-poems \
  'Create a Heinzel named Turing. Every five minutes watch https://github.com/example/project/blob/main/CHANGELOG.md and draft a poem for each new release in https://docs.google.com/document/d/your-document/edit. Notify me to review, and share the document link only after I edit and approve the draft.'
```

The CLI shows the exact account, supplied links, append-only section, initial release baseline, bounds and share
destination. Explicitly approve that initial publication in the terminal. The prompt becomes Turing's first chat
message.

Publication permits bounded marked draft appends and local review notices. It grants no arbitrary document
writes, ACL changes or link shares. Releases already present at the initial baseline are not backfilled.

The normal daemon polls every five minutes. Each new stable provider release identity gets one marked poem section
in the same rolling document; earlier sections remain. The local notice opens Turing's chat. Edit the draft in that
document, then use its chat identity and draft key from `hz heinzel release-workflow SESSION --json`:

```sh
hz heinzel approve-release-draft SESSION DRAFT_KEY
```

The CLI displays the complete edited section, exact document revision and complete link destination. Approve that
review to permit one journaled link attempt. A new edit or changed account, scope or destination invalidates the
review. The receipt retains the approved edited section and revision in the same chat. The shared link still points
to a mutable rolling document; sharing does not freeze later document content.

```sh
hz heinzel revoke-release-workflow SESSION my-release-poems
```

Revocation waits for cooperating document work to quiesce and prevents future effects. A request already accepted by
a provider cannot be undone. Missing acknowledgements and interrupted effects stop automatic retries, including
across process restarts. Inspect an uncertain effect before reconciling it; a later read cannot manufacture a
missing append or share acknowledgement. Queued local notices do not prove native notification display.

These operations require no preview feature flag. Installing this source or passing provider-double scenarios does
not establish that real provider credentials, conditional append, owner review or external delivery have passed
acceptance. Each installed adapter and the actual accounts require separate validation.
