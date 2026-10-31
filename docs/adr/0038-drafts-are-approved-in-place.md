---
status: proposed
date: 2026-10-24
decision-makers: maintainer
amends: 0010, 0020
related: 0021, 0022, 0024, 0027, 0028, 0030, 0035, 0037
amended-by: 0049, 0051, 0052, 0059
---

# 0038: Drafts are approved in place

*Not implemented at 0.9.9; today drafts stay local.*

## Context and Problem Statement

Heinzelmännchen drafts, people approve (tenet 3), and every artifact records its sources, its draft and its approver
(tenet 4). The records so far put approval in a place of its own: [ADR 0010](0010-towards-a-reactive-workflow-engine.md)
queues every rendered draft for approval, and [ADR 0020](0020-daemon-head.md) §7 makes each proposal an issue or pull
request in the state repository with `hz inbox list|show|approve|reject` as the one inbox. That is a second place to
look, a second copy of the text, and a review of a draft outside the document it is meant for. The decision:
"Approval happens in place. hz writes the draft where it will live (e.g. this week's section in the
rolling weekly-report document, an engineer's row in the tracker), marked with a draft tag. The person edits it in
place and removes the tag to approve; a short chat nudge points there. hz records the approver from the document's
revision history (lineage) and diffs draft vs approved to learn. Only after approval do downstream steps run (chat
post, email, dependent contracts)." How is a draft marked, how is approval detected and attributed, what happens when
something else touches it, and what becomes of the inbox?

## Decision Drivers

- Tenets 1 and 3: approving should be the edit a person would make anyway, in the tool they already have open; never a
  queue to clear.
- Tenet 4: the approver and the difference between draft and approved text are lineage, recorded by hz, not asserted
  by hz.
- The vendor's revision history already says who changed what and when; as an ordinary employee through the vendor's
  CLI ([ADR 0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md)) hz can read it and write the
  draft, and nothing more is needed.
- Nothing downstream may happen on a draft ([ADR 0021](0021-workflow-contracts.md) §9: `publishes` are approved
  publish operations); the graph of contracts must see approved truth only.
- Minimisation ([ADR 0024](0024-people-and-org.md)): the approver is recorded as lineage of one artifact, never
  aggregated per person.

## Considered Options

- **The draft is written where it will live with a draft tag; removing the tag approves; the approver comes from the
  revision history; downstream waits for approval** (chosen)
- **An approval queue in shared state** ([ADR 0020](0020-daemon-head.md) §7: issues and pull requests, `hz inbox`): a
  second place and a second copy; reviewers read a diff instead of the document; the approved text still has to be
  written somewhere afterwards. Kept where the asset *is* a repository file: there the pull request is the in-place
  draft and merging is the approval.
- **Approve from the terminal only** (`hz inbox approve`): works for agents and for assets without an editor, but makes
  the person leave the document to say yes to what they just read. Kept as a way to remove the tag, not as the place.
- **A separate draft document per run** (a "drafts" folder, a Slack message with buttons): the text still has to move
  to its destination, and whoever moves it becomes the author of record.
- **Detecting approval by content comparison alone** (no tag): hz cannot tell an approving edit from an unrelated one,
  and a document nobody touched would count as approved by silence. Silence never approves.
- **Vendor comment threads or suggestion mode as the approval**: not every asset type has them (a tracker field, a
  spreadsheet cell), and resolving a comment is a weaker signal than the text itself being final.

## Decision Outcome

Chosen option (proposed): **hz writes a draft where it will live, marked with a draft tag the playbook and adapter
define for that asset type; a person edits it there and removes the tag to approve; hz learns the approver from the
revision history and records draft, approved text and their diff as lineage; downstream steps run only after
approval.** Checkable statements:

1. **The draft lives in its destination.** A publish operation ([ADR 0021](0021-workflow-contracts.md) §9) that
   produces an asset writes the draft into the asset's place, through the bound adapter's `write` mode: this week's
   section of the rolling report document, the engineer's row in the tracker, a cell range in the spreadsheet. The
   receipt's disposition becomes `awaiting_approval` ([ADR 0020](0020-daemon-head.md) §8) and names the place.
2. **The draft tag is a convention per asset type.** A playbook ([ADR 0037](0037-plugins-bring-playbooks.md)) names
   the convention for each asset type it defines, beside the template that keeps the asset (`kept_by`), and the adapter
   implements it: for a document section a marker line
   under the heading (text such as "🧝 draft: edit, then delete this line to approve", in the team's language, with
   the contract name and the run's receipt id); for a tracker item a label or a field value (`hz:draft`); for a
   spreadsheet a cell note on the range. The core knows only the operations: `place_draft(ref, text) -> Draft` and
   `draft_state(Draft) -> DraftState` on every capability that has a `write` mode, where `DraftState` is one of
   `pending`, `approved`, `rejected`, `conflict`, with the actor and time from the vendor's revision history and the
   current text of the place. The adapter decides how the tag looks and how to detect its removal; the contract names
   neither. Adding these methods to the `write` mode follows [ADR 0028](0028-integrations-are-capability-adapters.md)'s
   growth rule; no shipped adapter has a `write` mode yet.
3. **Removing the tag approves; the approver is who removed it.** The daemon polls `draft_state` on its tick (bounded
   by the contract's freshness, at least hourly) and the person may also run `hz inbox check`. When the tag is gone and
   the place still has text, the draft is approved by the actor the revision history names for the revision that
   removed it, matched to a person by the e-mail or login the vendor records ([ADR 0024](0024-people-and-org.md)'s
   join key). When the place was emptied or deleted along with the tag, the draft is `rejected` by that actor.
   Revision IDs are opaque and short-lived ([ADR 0022](0022-embedded-workflows.md)); hz keys on the revision's actor,
   time and the content's hash, never on the vendor's id.
4. **Lineage.** The receipt records the draft text as placed, the approved text as found, the diff between them, the
   approver (handle or e-mail, as ADR 0024 allows) and the approval time. The diff is what hz learns from: the next
   render of the same contract gets the last approved text and the diff in its packet, so the author's corrections
   carry forward. Diffs are indexed by contract and asset, never aggregated per person ([ADR 0024](0024-people-and-org.md)
   §7): "how much the draft was changed" is the success test of the validation slice (INTENT.md), measured per
   contract.
5. **One nudge, to the approver, where the draft is.** Placing a draft sends one short message to the approver (a chat
   direct message or an e-mail, as the person configured; never a channel) with the link to the place and the one
   sentence of what to do. It is part of the publish operation that placed the draft, not a downstream step, and it is
   the only unprompted message: no reminder follows unless the contract's freshness says the draft is stale, and then
   once more. Done, not nagged.
6. **Only after approval do downstream steps run.** The contract's remaining `publishes` (the channel post, the e-mail
   to the audience) and every dependent contract (an input of kind `contract:` sees published truth,
   [ADR 0021](0021-workflow-contracts.md) §7) wait for `approved`; a `rejected` draft runs nothing and the receipt says
   so. Auto-approval ([ADR 0020](0020-daemon-head.md) §7) stays a policy entry with an owner: under it the tag is
   never placed, the receipt names the policy as approver, and whoever switched it on owns the result (tenet 4).
7. **Conflicts: hz never fights an edit.** Once a human changed the place after hz wrote it, hz does not write there
   again for that run: a re-render (changed inputs) is offered as a diff in the nudge and in `hz inbox show`, not
   written over the person's words. If the tag is removed by someone the contract's `approvers` selector does not
   name, the state is `conflict`: downstream does not run, the receipt names the actor, and the nudge goes to the
   owner with "approved by someone not in approvers; approve as you, reject, or widen approvers". If the tag is
   removed and re-added, the latest state counts. If two drafts of one contract would land in one place (a catch-up
   run), only the newest is placed and the older receipt is `skipped`. If the place no longer exists (section or item
   deleted), the draft is `rejected` and the contract's owner is told once.
8. **The inbox is a view, not a place.** `hz inbox list|show` list the drafts awaiting approval wherever they live,
   with their places; `approve` and `reject` remove the tag or empty the place through the adapter on the person's
   behalf, for assets without an editor and for agents, and are recorded with that person as approver like any
   revision. No issue or pull request is created for a draft, except where the asset is a repository file: there the
   pull request *is* the placed draft, the merge is the approval and the merger the approver, which is what
   [ADR 0020](0020-daemon-head.md) §7 already described for file-like drafts.
9. **The validation slice may start locally.** The weekly report draft (INTENT.md, "Thin slice first") begins as a
   local draft the manager edits and sends themselves; its success test (time saved, how much was changed) is measured
   on the local file. It moves in place, into this week's section of the rolling report document with the tag, as soon
   as the documents adapter has `write` through the vendor CLI; the contract does not change, only its publish
   operation.

### Consequences

- Good: approving is the edit the person would make anyway, in the tool that is open; no queue, no second copy, no
  "where was that again".
- Good: lineage comes from the vendor's own revision history, which the approver cannot forget to update; the diff
  between draft and approved text is the learning signal and the success metric in one.
- Good: dependent contracts and outward steps can never run on a draft, because they wait on a state the adapter reads,
  not on a flag hz set.
- Bad: approval is detected by polling, so it lands with the tick's delay; `hz inbox check` and the nudge's link make
  it immediate for the person who wants that.
- Bad: every adapter with a `write` mode must implement the tag convention and read revision history; the vendor's
  history may be coarse (one revision for many edits, merged authors), and then the approver is the last actor the
  vendor names, which the receipt says.
- Bad: a tag someone deletes by accident approves; the nudge said so, the receipt names them, and `hz inbox` shows
  what was approved with its diff; rejecting afterwards re-places the draft with its tag.
- Bad: [ADR 0020](0020-daemon-head.md) §7 and the `Inbox` protocol change shape (a view over adapters rather than
  issues in the state repository); its `Ledger` and leases are untouched.
- Bad: new work: the two capability methods and their adapter implementations, the tag conventions in the reference
  company's playbook, the poll and the state machine, the nudge, the receipt fields, `hz inbox` as a view, and tests
  with test-only adapters.

### Confirmation

Not yet: proposed. When implemented: unit tests of the state machine with test-only adapters that replay revision
histories (placed, approved by an allowed actor, rejected by emptying, tag removed by someone not in `approvers`, removed
and re-added, place deleted, catch-up run), of the receipt's lineage fields and the diff, and of downstream gating;
that hz never writes a place a human changed
(`tests/unit/modules/heinzel_service_test.py`); the nudge going to the approver alone and once
(`tests/unit/kernel/capabilities_test.py` for the methods' modes, and through the
heads); no per-person aggregation of approvals or diffs in any operation (`tests/unit/repository_test.py`, the ADR 0024
review rule); each shipped adapter's `place_draft` and `draft_state` against recorded vendor CLI output
(in heinzel-plugins: `plugins/heinzel-<name>/tests`).

## More Information

- Amends [ADR 0010](0010-towards-a-reactive-workflow-engine.md) ("every rendered draft is queued for approval": the
  queue is the set of tagged places, shown by `hz inbox`) and [ADR 0020](0020-daemon-head.md) §7 (proposals are no
  longer issues or pull requests in the state repository, except for repository files; `hz inbox` is a view over
  adapters; auto-approval policies and receipts stay). Builds on [ADR 0021](0021-workflow-contracts.md) (publish
  operations, the graph seeing published truth), [ADR 0022](0022-embedded-workflows.md) (revision ids are unfit as
  pins), [ADR 0024](0024-people-and-org.md) (the approver as minimised lineage), [ADR 0027](0027-nothing-is-sent-home.md)
  (diffs stay with the team), [ADR 0028](0028-integrations-are-capability-adapters.md) (the methods are capability
  methods; adapters own the convention) and [ADR 0030](0030-credentials-live-in-the-os-keychain.md) (no credential for
  this: the vendor CLI's login writes and reads).
- Builds on [ADR 0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md) (the write and the
  revision-history read happen as the person, through `gws`, `acli` or `gh`) and [ADR 0037](0037-plugins-bring-playbooks.md)
  (the playbook names the tag convention per asset type; drafts of `kept_by` templates are what gets placed). Proposed
  with two records referenced by name until merged: "layers and playbooks are plugins" (which layer's playbook wins)
  and "perfect onboarding" (the first win is a local draft and needs no approval).
- Open: whether a vendor's revision history is readable as an ordinary employee for every asset type (a spreadsheet
  cell note's author; a tracker field's changelog), to be confirmed per adapter; the exact tag text and whether it is
  localised; whether `rejected` should re-place the draft once with the owner's comment.
