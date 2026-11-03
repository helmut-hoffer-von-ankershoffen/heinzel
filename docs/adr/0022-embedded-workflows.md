---
status: proposed
date: 2026-10-23
implementation: planned
related: 0020, 0021
---

# 0022: Workflows embedded in documents are discovered by connectors and run only once registered

*Not implemented at 0.9.9; embedded workflows are not built.*

## Context and Problem Statement

Teams want to describe how a document is kept up to date inside the document itself: a "🧝 Heinzel" tab in a Google
Doc, a sheet in a Google Sheet, a block on a Confluence page, and later in Notion or Microsoft 365.
[ADR 0020](0020-daemon-head.md) §9 says such workflows are untrusted until registered.
[ADR 0021](0021-workflow-contracts.md) defines the contract text. What is still open is how hz finds them, extracts
them, detects changes and reports status back.

## Decision Drivers

- Every platform marks the block differently: Docs tabs (`tabProperties.title`, `iconEmoji`, nested `childTabs`),
  sheet titles, Confluence labels and ADF `expand`/`codeBlock` nodes, Notion blocks, Word content controls.
  Some platforms can't be searched by body text: Notion search matches titles only, and Graph cannot read Word
  content.
- Platform revision IDs are unfit as pins. A Docs `revisionId` is opaque, valid for 24 hours and per user. Drive has
  no `md5Checksum` for native files.
- Crawling Drive needs restricted scopes (`drive.readonly`). Restricted scopes need Google verification unless the
  OAuth client is internal to one organization.
- Writing status into the hashed text would overwrite users' edits and make jobs wake themselves.
- Laptops have no public endpoint. Every platform's webhooks need one.
- The embedded text is instructions by design, so it is a prompt-injection surface that anyone with edit access can
  change.

## Considered Options

- **Connector plugins that poll for marked blocks; registration by pull request; pins on canonical-text
  fingerprints** (chosen)
- **Run whatever is found (no registration):** anyone with edit access to any document could direct hz. Rejected by
  ADR 0020 §9 and tenet 4.
- **Pin platform revision IDs:** Docs `revisionId` expires after 24 hours and is per user. Confluence and Notion
  versions change on edits outside the block. That would pause too often or fail to pin at all.
- **Keep contracts only in the state repository, with documents linking to them:** safe, but loses authoring in
  context, which is the point. Registration already mirrors the text into the repository.
- **Write status into the contract block:** overwrites user edits and makes jobs wake themselves.
- **Webhooks or push channels:** need a public endpoint, channel renewal and app hosting. Kept as a later relay into
  Actions.
- **Google Workspace add-on or Apps Script sidebar:** a nicer editor, but needs Marketplace publishing and code
  running inside Google. Possible later as an authoring aid on top of this format.
- **`drive.file` only:** no restricted scopes, but no crawling. Kept as a fallback (`hz inventory add <url>`).
- **Domain-wide delegation:** finds everything, but impersonates any user and needs a Super Admin. Too broad for a
  default.

## Decision Outcome

Chosen option (proposed): **connectors discover embedded workflows by native markers, and they run only once
registered by pull request and pinned by fingerprint**, because teams author in context while nothing untrusted runs.

1. **Connectors are plugins.**
   - One plugin per platform (`heinzel-google`, `heinzel-atlassian`, later `heinzel-notion`, `heinzel-m365`), each
     implementing `DocumentConnector`: `discover(cursor)`, `fetch(ref)`, `write_status(ref, status)`.
   - A core `inventory` module owns canonicalization, fingerprinting, the cache and the operations
     `hz inventory crawl|show|register|unregister`, with `--json` everywhere.
2. **One authoring convention, native markers per platform.** The block is marked with the 🧝 emoji and the word
   `Heinzel`, and contains ADR 0021 contract text verbatim:
   - Docs: a top-level tab titled `Heinzel` with `iconEmoji` 🧝.
   - Sheets: a sheet `🧝 Heinzel` holding a key/value table.
   - Confluence: the label `heinzel` plus an expand titled `🧝 Heinzel` wrapping a code block.

   Connectors select blocks by these markers, never by matching body text.
3. **Discovery polls with cursors; it never runs anything.**
   - Each connector contributes a `WatchLet` with a cursor keyed by (connector, principal): Drive page token,
     Confluence CQL `lastmodified` high-water mark, Notion `last_edited_time`, Graph `deltaLink`.
   - Candidates are listed or proposed, never run.
4. **Registration is a pull request.**
   - `hz inventory register` adds `workflows/<id>.md` (the canonical text) and an entry in `workflows/registry.toml`:
     source URI, owner, fingerprint, canonicalization version, and the allowed `publishes` action types and targets.
   - The author must have edit rights on the source. A CODEOWNER of `workflows/` approves.
5. **Pins are fingerprints of the canonical text.**
   - Fingerprint = SHA-256 over `canonical-v1`: the extracted block only, NFC, normalized whitespace, invisible
     characters removed and reported, unaccepted suggestions excluded.
   - Platform version hints only decide whether to fetch again.
   - A registered `JobLet` whose fetched fingerprint differs from the pin does not render. Its disposition is
     `awaiting_approval` with cause `contract_changed`, and its proposal carries the diff. Approval is a re-pin pull
     request.
6. **Status is written beside the hashed text.**
   - Confluence content properties, Sheets developer metadata, Notion database properties, SharePoint columns.
   - In Docs: a `hz-status` named range that hashing ignores.
   - Writes fail rather than merge when the source changed concurrently (version checks). The connector records the
     version hint after its write as its own.
   - GitHub remains the primary place for status and approvals (ADR 0020 §7).
7. **Credentials and least privilege.**
   - Personal daemons use the person's own OAuth (Google, through an organization-internal client) or a scoped
     Atlassian token, kept in the keyring.
   - Team runners use a Google service account that reads only drives shared with it.
   - No domain-wide delegation by default. Write scopes only when write-back or publishing is enabled.
8. **Provenance.** Every receipt names the source URI, version hint, editor, connector and parser versions, contract
   fingerprint, and the registry commit that authorized the run.
9. **Offline.** When a source is unreachable, the tick writes a `skipped` receipt, leaves cursors untouched and backs
   off. It never runs a cached contract whose source can't be checked.

### Consequences

- Good: teams author where they already work. Reviewers approve plain text diffs in GitHub, not rendered documents.
- Bad: each organization must create an internal Google OAuth client. `hz system doctor` checks scopes, token
  expiry and cursor health per connector.
- Bad: discovery depends on whose credentials run it. Leases and cursors are per (connector, principal), and team
  scope needs a service account on shared drives.
- Bad: changing the canonicalization needs a version bump and an automatic re-pin rule: re-pin only when the source
  version hint is unchanged since approval.
- Bad: new work: the `inventory` module and its protocol, connector plugins (Google first, then Atlassian), registry
  schema fields, the pause-and-re-pin flow, golden-file tests for each platform's extraction.

### Confirmation

Not yet: proposed. When implemented: golden-file tests per platform's extraction and `doctor` checks per connector.

## More Information

- Builds on [ADR 0020](0020-daemon-head.md) and [ADR 0021](0021-workflow-contracts.md).
- Research and sources: [docs/research/embedded-workflows.md](../research/embedded-workflows.md).
- Open questions: is the connector protocol a kernel protocol or a new let kind? How do GitHub identities map to
  document identities? Is status write-back the default?
- Disagreements with the other proposals, to settle before any of them is accepted:
  - **One `inventory` module.** This ADR gives the core `inventory` module `crawl`, `show`, `register`,
    `unregister` (and `add <url>`); [ADR 0023](0023-inventory.md) gives it `compile`, `validate`, `list`, `show`,
    `diff`, `graph`, `discover`, `publish`. They are one module with the union of these operations; `crawl` here and
    `discover` there overlap and should become one operation.
  - **Connector protocol vs. let kind.** `DocumentConnector.discover` here and `InventoryLet(mode="discover")` in
    ADR 0023 both describe discovery by plugins; the open question above should be answered once for both.
  - **Two canonicalization rules.** `canonical-v1` here and §11 of ADR 0021 must be one function.
  - **Connectors are capability adapters.** [ADR 0028](0028-integrations-are-capability-adapters.md) proposes that
    connector plugins implement core capability protocols (documents, spreadsheets), which would make
    `DocumentConnector` part of the documents protocol.
