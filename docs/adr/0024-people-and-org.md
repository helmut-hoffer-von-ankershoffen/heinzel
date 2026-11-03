---
status: proposed
date: 2026-10-23
implementation: partial
related: 0005, 0012, 0018, 0020
amended-by: 0028, 0080
implemented-by: 45
---

# 0024: People and teams come from a directory of record, are referenced by role, and are minimised

## Context and Problem Statement

Every hz workflow is about people: who receives a status report, whom recognition is about, who approves, who owns
an auto-approval. Teams already keep this in directories: an IdP (Google Workspace, Entra), GitHub teams, Slack user
groups, Atlassian teams, sometimes an HRIS. A second, hand-kept org chart in hz would drift from them. hz must work
for a five-person team with nothing but GitHub, and for a company with an IdP (tenets 2 and 9). Approvals and
ownership already live in GitHub (ADR 0020), so approvers need a GitHub identity. Where do people and teams come
from, and what may hz keep about them?

## Decision Drivers

hz processes employee data, much of it in the EU and Germany:

- GDPR requires a lawful basis, purpose limitation, minimisation, transparency, data subject rights and retention
  limits.
- Under BetrVG §87(1) no. 6, any technical system objectively *able* to monitor behaviour or performance needs
  works-council co-determination, regardless of intent (BAG case law).
- The EU AI Act treats AI that monitors or evaluates workers' performance or behaviour as high-risk, and profiling
  always remains high-risk.
- Git history is hard to erase (filter-repo, forks, GitHub Support), so what the state repository holds must be
  decided now.

## Considered Options

- **Read people from existing directories through adapters, reference them by role, store only minimised
  identifiers** (chosen)
- **hz as the org chart (hand-maintained people database):** duplicates the IdP and drifts; makes hz an HR system
  of record, against "drafts into the tools teams already use".
- **HRIS as the default source:** richest hierarchy, but the most sensitive data and the broadest credentials, and
  usually a third-party processor. Kept as a plugin for companies whose IdP lacks managers.
- **Email as the primary ID:** emails change and have aliases, and GitHub hides them. Kept as the join key only.
- **GitHub login as the only ID:** excludes people without GitHub accounts (tenet 2: for all roles) and logins can
  be renamed. Required only for approvers and owners.
- **All attributes committed to the state repository:** simple and reviewable, but personal data in Git history is
  costly to erase and visible to every reader.
- **Encrypt everything with sops/age:** defeats PR review, which ADR 0020 uses for ownership; key distribution
  burden.
- **Running a SCIM server so IdPs push to hz:** needs a public endpoint and a server, against ADR 0020's no-server
  design. SCIM is used as a schema only.
- **Consent as the lawful basis:** consent is rarely free in employment; opt-in stays a design feature on top of
  Art. 6(1)(b)/(f).
- **Per-person productivity insights as a headline feature:** triggers §87(1) no. 6 co-determination, puts hz in the
  AI Act's high-risk category, and conflicts with tenet 1 (joyful, not managed).

## Decision Outcome

Chosen option (proposed): **people and teams come from a directory of record, are referenced by role, and are
minimised**, because hz then neither duplicates the org chart nor becomes a monitoring system.

1. **A new `hz org` module** (team work, so not under `hz system`, ADR 0018) provides a `Directory` service
   injected by type (ADR 0005). Operations:
   - `show`, `sync`, `validate`, `chart`, `coverage`;
   - `person list|show|resolve|me|prefs`, `team list|show`, `role list`;
   - `privacy notice|export|forget`.
2. **Sources are adapters.**
   - Core ships two sources: the declared files in the state repository and GitHub org teams.
   - Google Workspace, Entra/Graph, Slack, Atlassian and HRIS (direct or through a unified API) are plugins.
   - Precedence is configured per attribute (existence, email, manager, team, role, time zone). Conflicts are
     `validate` findings and are never merged silently.
   - `sync` is also a `WatchLet` (ADR 0020) with scope `team`, so membership changes become fingerprinted inputs.
3. **One normalised model.**
   - `Person` follows SCIM 2.0 Core User plus the Enterprise extension (RFC 7643).
   - `Team` (tree), `Role` (hz vocabulary, not job titles), `OrgUnit` (read-only mirror) and `Membership`
     (person, team, role, from, until) are hz's own.
   - Declared files use TOML (ADR 0012); SCIM JSON is accepted as an import format.
4. **Identity.**
   - Each person has a stable hz handle, never reused; ADR 0023 references people by this handle.
   - Email (primary and aliases) is used only to match records the first time. After that each source's immutable
     ID is the anchor.
   - Unmatched or ambiguous records are resolved by a declared mapping in a PR, never by fuzzy name matching.
   - Anyone selected as approver or owner must have a GitHub identity.
5. **Workflows reference people through selectors, never by name.**
   - Selectors: `team:`, `role:…@team:`, `orgunit:`, `manager-of:`, `reports-of:`, `person:`, `@subject`.
   - Contracts use them in `audience`, `subjects`, `approvers` and `owner`. The final grammar is decided with ADR
     0021.
   - The opt-out filter is always applied and cannot be removed by a contract.
   - A selector that resolves to nobody fails loudly.
6. **Minimised storage.**
   - The state repository holds handles, GitHub logins, source IDs, teams, roles, memberships with dates, and
     per-person preference files that each person owns through CODEOWNERS.
   - Names, emails, titles, managers and locations are fetched at run time and cached locally with mode 0600 and a
     TTL (default 24 h).
   - Teams with no directory may declare names and work emails in a private state repository.
   - sops/age encryption is optional and not the default.
7. **No monitoring by construction.**
   - No core operation computes, stores or outputs per-person counts, rates, rankings or scores. Team aggregates
     need a minimum group size.
   - Drafts are organised by work item.
   - Recognition starts from a human nomination.
   - Public mentions require the subject's preference.
   - Fleet heartbeats (ADR 0020) are opt-in and dated by day.
   - No command aggregates receipts per person.
   - hz never reads HR-sensitive fields (compensation, ratings, absence reasons, health).
8. **Transparency and rights.**
   - Each contract declares its purpose and the person attributes it reads.
   - `hz org privacy notice` generates the employee notice (DE/EN) from those declarations.
   - `export` serves access requests; `forget` purges caches, removes files by PR and tombstones the handle.
     Receipts keep the now-unresolvable handle for lineage.
   - Retention defaults: memberships 90 days after `until`; caches 24 h.
9. **Model calls get only the declared attributes**, with optional pseudonymisation (handles in the prompt, names
   put back after rendering).

### Consequences

- Good: a small team starts with zero new credentials (declared files plus GitHub). A company plugs in its IdP
  without changing a workflow.
- Good: org changes re-render affected drafts through the existing fingerprint mechanism (ADR 0020).
- Good: employers can describe hz to a works council and data protection officer from generated artefacts: notice,
  purposes, excluded features. hz still needs the employer's own legal review, DPIA decision and works agreement;
  hz only makes them predictable.
- Bad: some features users ask for (leaderboards, per-person activity digests, auto-picked kudos) are out of scope
  for the core. Plugins that add them must carry their own privacy and co-determination analysis.
- Bad: run-time fetching makes drafts depend on directory availability. The cache and the declared fallback soften
  this.
- Bad: ADR 0020 must change: heartbeat content, and no per-person receipt aggregation.
- Bad: new work:
  - the `org` module and `Directory` protocol;
  - the GitHub and declared adapters;
  - selector resolution;
  - `validate` checks;
  - Mermaid and DOT chart writers, with subtree defaults below Mermaid's 50,000-character and 500-edge limits;
  - the coverage report;
  - privacy notice and works-agreement annex templates;
  - plugin adapters for Google and Entra.

### Confirmation

Not yet: proposed. When implemented: `hz org validate` checks, and a review rule that no core operation outputs
per-person metrics.

## More Information

- Research and sources: [docs/research/people-and-org.md](../research/people-and-org.md).
- Amended by [ADR 0028](0028-integrations-are-capability-adapters.md):
  the `Directory` is the capability `directory`, not a service of `hz org`; a plugin reads a directory of
  record, and otherwise the core drafts reporting lines from recurring 1:1s for the user to confirm.
- Builds on [ADR 0005](0005-dependency-injection-by-type.md), [ADR 0012](0012-layered-configuration.md),
  [ADR 0018](0018-one-administrative-namespace.md) and [ADR 0020](0020-daemon-head.md). Aligns with
  [ADR 0021](0021-workflow-contracts.md) (workflow contracts), [ADR 0022](0022-embedded-workflows.md)
  (document-embedded workflows) and [ADR 0023](0023-inventory.md) (inventory).
- Disagreements with the other proposals, to settle before any of them is accepted:
  - **Data file format.** Declared people files are TOML here, citing ADR 0012; but ADR 0012 decides the format of
    configuration files, not of data in the state repository. ADR 0021 uses YAML frontmatter and ADR 0023 declares
    inventory files in YAML.
  - **Selector grammar.** Deferred here to ADR 0021, which does not define one yet (`owner`, `maintainers` are
    plain fields there).
  - **Changes to ADR 0020.** The consequence "ADR 0020 must change" (heartbeat content, no per-person receipt
    aggregation) is not recorded as an amendment; it needs one, in this ADR or in ADR 0020, before acceptance.
