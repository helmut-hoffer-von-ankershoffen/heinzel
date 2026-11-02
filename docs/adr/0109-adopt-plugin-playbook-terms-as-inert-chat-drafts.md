---
status: proposed
date: 2026-10-30
implementation: built
decision-makers: maintainer
related: 0037, 0106
implemented-by: 303, 307
---

# 0109: Adopt plugin playbook terms as inert chat drafts

## Context and Problem Statement

Plugin playbook templates can seed legacy workflow contracts, but those contracts do not bind a durable personal
session's account-scoped source or its independently reviewed schedule and notification permissions. A chat should be
able to offer installed plugin material without treating template text as authority or quietly switching execution paths.

## Considered Options

- **Offer exact plugin snapshots as inert chat drafts** (chosen).
- Run legacy workflow contracts directly: their permissions do not establish the durable chat's approvals.

## Decision Outcome

Chosen option: **offer exact plugin snapshots as inert chat drafts**, because reusable source material must remain
separate from permission to execute it.

The host may compile a bounded, exact snapshot of a qualified registered playbook template into an inert chat draft.
The result identifies the module and template, their exact content digest, the unmodified text and unresolved asset
placeholders. Preview and adoption are separate: adoption requires the digest just inspected and fails if the template
changed, disappeared or is ambiguous. Template text is untrusted source material, not an instruction from the owner,
publisher attestation or a grant. It must be presented with its provenance and cannot silently apply defaults, resolve
placeholders, read a provider, create a schedule, approve an action or send a notice.

The chat host may offer separate existing proposal controls after the owner has inspected or amended the draft. Those
controls retain their own owner/session, current source/account, schedule, notification and exact-review requirements;
no consent is bundled or inferred. Any later execution must use host-issued item identities and read bindings, never
model-generated IDs. A digest detects a changed snapshot but is not an approval proof or an authorization token.
An owner-bound ordinary chat turn can list and retrieve registered templates and propose an agent draft. The host
records the qualified name and digest of the retrieved template in the private turn journal, rechecks that exact
snapshot before the proposal, and labels the resulting model-authored contract with its origin. The journal records
retrieval by the model, not proof that the person saw or approved it. The existing proposed-contract view remains
the human inspection point. The proposed text can differ from the plugin template and has no permitted tools. No
route, activation, access grant, notification grant or source read is created by this bridge.

Plugins may separately declare `ChatDraftTemplate` values in a playbook's `chat_drafts` collection. These are
bounded untrusted text for the chat preview and proposal path only. They are never included in the legacy contract
template collection, legacy `new --template` lookup, or setup's workflow generation. Existing `ContractTemplate`
values retain their previous workflow and chat behavior; qualified names cannot collide between the collections.
The owner must separately review any source, schedule and notification permissions, and future runtime behavior is
not implied by the presence of a draft.

### Consequences

- Good: people can start from installed templates while retaining control of the resulting contract and permissions.
- Bad: adoption requires a separate proposal and approval journey; a preview alone cannot make an agent runnable.

### Confirmation

Users can review a reusable draft without a provider effect. Templates over the bounded chat proposal size are refused
rather than truncated. Legacy YAML is included as source text and is not silently executed or interpreted as durable
permissions. Tests cover qualified lookup, inert exact content, snapshot changes, ambiguity, malformed and oversized
input, unresolved placeholders, an owner-bound fake-model chat proposal and stale or cross-turn preview denial.
Independent approvals and actual scheduled execution require their own tests before claiming a complete journey.
