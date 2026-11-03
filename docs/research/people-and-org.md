# Research report: people and the organisation in Heinzelmännchen

> Historical design research, not current documentation. The linked decision record and the README describe what is
> available today.

- Date: 2026-10-23
- Scope: research only. Decision record: [ADR 0024](../adr/0024-people-and-org.md) (proposed). Not legal advice; the controller (the employer) must
  get its own legal review, data protection officer sign-off and works-council agreement.
- Read first: `INTENT.md` (tenets 2, 4, 8, 9), ADR 0005, 0010, 0012, 0018, 0020 and `docs/research/daemon-head.md`.
  Align with: ADR 0021 (workflow contracts: who-selectors), ADR 0022 (document-embedded workflows), ADR 0023
  (inventory: references people and teams by ID).

## TL;DR

1. **hz does not own the org chart; it reads it.** People and structure come from a directory that already exists,
   through adapter plugins (tenet 9). The core ships two sources: the declared file in the state repo (always
   available) and GitHub org teams (no new credentials). Google Workspace, Entra/Graph, Slack, Atlassian and HRIS
   (direct or Merge/Finch) are plugins.
2. **One normalised model.** The internal model is SCIM 2.0 Core User + Enterprise User extension (RFC 7643), plus
   hz-specific Team, Role and Membership with dates. A person has a stable hz handle, and each source has its own
   identity under `identities`.
3. **Workflows name roles and teams, never people.** A `Directory` service, injected by type (ADR 0005), resolves
   selectors such as `team:checkout`, `role:engineering-manager@team:checkout` and `manager-of:@subject`. The
   result is then filtered by opt-out.
4. **Minimised, as little as possible committed.** The state repo holds handles, GitHub logins, roles,
   memberships and opt-in flags. Names, emails and time zones are fetched at run time or held in a local cache with
   a TTL. Working hours and preferences are self-declared and owned by the person (CODEOWNERS).
5. **No monitoring by construction.** No per-person metrics, counts, rankings or scores. Summaries go by work item,
   recognition starts from a human nomination, heartbeats are opt-in and coarse. `hz org privacy notice` builds the
   employee-facing notice from the workflow contracts. That keeps hz clear of AI Act profiling and makes the
   agreement required under BetrVG §87(1) no. 6 short and predictable. It does not make that agreement unnecessary.

## 1. Sources of truth

| Source | People | Hierarchy (manager) | Teams/groups | Location/time zone | Access needed | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| **Declared file** (state repo) | yes | optional | yes | self-declared | repo write via PR | Always available; reviewed by PR; the fallback and the place for hz-only facts (roles, opt-ins) |
| **GitHub** org members, teams, CODEOWNERS | logins only; email only if the user made it public | no | teams, nested teams, team maintainers | no | `read:org` (token or App) | Already required by ADR 0020; teams are the natural engineering membership source; SAML orgs expose the IdP `nameId` through GraphQL `externalIdentities`, a reliable email join (verify per org) |
| **Google Workspace Directory API** | `id`, `primaryEmail`, `aliases`, `suspended`, `archived` | `relations[].type = manager` (also `dotted_line_manager`), value = email | groups and members; `orgUnitPath` | `locations[]` (building, floor), not a time zone | `admin.directory.user.readonly` (admin or domain-wide delegation); `viewType=domain_public` returns only domain-visible fields (non-admin access: verify against the "Retrieve a user as a non-administrator" guide) | Strong default for Google shops; org units are an admin construct, not teams |
| **Microsoft Entra / Graph** | users, `mail`, `userPrincipalName` | `/users/{id}/manager`, `directReports`, `$expand=manager($levels=max)` on one user (needs `ConsistencyLevel: eventual`) | groups | `officeLocation`, `usageLocation` | delegated `User.Read.All` (least privileged; application permission not supported for `/manager`) | Strong default for Microsoft shops |
| **Slack** | `users.list` (`tz`, `real_name`); email needs `users:read.email` | no | user groups (`usergroups.list`, `usergroups.users.list`) | `tz` (IANA) | bot token with `users:read`, `usergroups:read` | Good for recipient handles and time zones; user groups often mirror teams |
| **Atlassian** | account IDs; email depends on profile visibility | no | Teams public REST API: `/public/teams/v1/org/{orgId}/teams/{teamId}/members` (POST, cursor); site-scoped teams need the site ID | no | org API token; Forge/OAuth2 apps cannot call the members endpoint | Useful when Jira "Team" fields drive work; join by account ID via mapping |
| **HRIS direct** (Personio, BambooHR, Workday, HiBob) | yes | yes | departments | office, sometimes time zone | HR API keys, usually broad and highly sensitive | Most sensitive data (salary, absence, contracts); read directory fields only, if at all |
| **Merge.dev / Finch** (unified HRIS) | Merge `Employee`; Finch `directory` individuals | Finch `IndividualInDirectory.manager` (id); Merge exposes `manager` (verify field) | Merge `Group` (its `Team` model was retired in mid-2024) | varies | a third-party SaaS processor plus the HR connection | One adapter for many HRIS, but adds a processor that sees HR data; a company decision, not a default |
| **SCIM 2.0** | Core User | Enterprise `manager.value` | Group | `timezone`, `locale` | none, if used as a file format | Not a source; the **schema** hz normalises to; hz accepts SCIM JSON exports; hz runs no SCIM server (no server, per ADR 0020) |

**Defaults**

- **Small team (one GitHub org, under ~50 people):** declared file plus GitHub teams. Managers and roles live in the
  declared file. Optionally add Slack for time zones and recipient handles.
- **Company:** the IdP plugin (Google or Entra) is authoritative for who exists, email and manager. GitHub teams
  are authoritative for engineering team membership. The declared file holds only hz roles, opt-ins and
  overrides. Add an HRIS (direct or Merge/Finch) only if the IdP has no manager data.
- **Precedence per attribute, not per source**, declared in config, for example
  `existence = idp`, `manager = idp`, `team = github`, `role = declared`, `tz = slack > declared`. Conflicts are
  reported by `hz org validate`, never silently merged.
- **Leavers:** a person suspended or deleted in the authoritative source is inactive at the next sync. Nothing is
  addressed to them, and the cache entry is dropped.

## 2. Data model

| Entity | Fields | Notes |
| --- | --- | --- |
| `Person` | `id` (hz handle, stable, never reused), `display_name`, `emails[]` (primary + aliases), `identities{source: id}`, `title`, `manager` (person id), `location`, `timezone` (IANA), `working_hours`, `locale`, `active`, `preferences` | SCIM Core + Enterprise names where they exist; `display_name`, `emails`, `title`, `location` are fetched, not committed (section 5) |
| `Team` | `id`, `name`, `parent` (team id), `kind` (`stream`, `platform`, `enabling`, `leadership`), `sources{github: org/slug, slack: usergroup}` | Teams form a tree (or a DAG for matrix orgs); kept separate from manager lines |
| `Role` | `id` (`engineer`, `product-manager`, `engineering-manager`, `lead`), `label` | Roles are hz vocabulary, not HR job titles; a person can hold several roles |
| `OrgUnit` | `id`, `path`, `source` | Read-only mirror of IdP org units, used only as a selector |
| `Membership` | `person`, `team`, `role`, `from`, `until` | Dates make the history explicit; expired memberships are pruned after the retention period |
| `Preferences` | `opt_out[]` (workflow or category), `channels`, `working_hours`, `recognition: public/private/none`, `pseudonymise_in_prompts` | Owned by the person; default is the minimum allowed, not maximum reach |

**Example** (made-up people). YAML is shown as requested; ADR 0012 makes TOML the configuration format, so the same
shape would ship as `org/*.toml` (open question 1).

```yaml
# state repo: org/org.yaml — reviewed by pull request; no names or emails here when a directory source is set
sources:
  - kind: google-workspace          # plugin; read-only; authoritative for existence, email, manager
    customer: my_customer
  - kind: github                     # core; authoritative for team membership
    org: example-org
precedence: {existence: google-workspace, manager: google-workspace, team: github, role: declared, timezone: [slack, declared]}

roles:
  - {id: engineer}
  - {id: product-manager}
  - {id: engineering-manager}
  - {id: lead}

teams:
  - id: product
    kind: leadership
  - id: checkout
    parent: product
    kind: stream
    sources: {github: example-org/checkout, slack: S0CHECKOUT}
  - id: payments-platform
    parent: product
    kind: platform
    sources: {github: example-org/payments-platform}

people:                               # handles and the GitHub login only; the rest is resolved at run time
  - id: ava
    identities: {github: ava-example, google: "1029384756"}
    memberships:
      - {team: checkout, role: engineering-manager, from: 2026-01-01}
  - id: ben
    identities: {github: ben-example, google: "1029384757"}
    memberships:
      - {team: checkout, role: engineer, from: 2025-09-15}
      - {team: payments-platform, role: engineer, from: 2025-09-15, until: 2026-03-31}
  - id: cleo
    identities: {github: cleo-example}  # PM without a directory match yet: email mapped below
    aliases: {email: [cleo@example.com]}
    memberships:
      - {team: checkout, role: product-manager, from: 2026-04-01}
  - id: dana
    identities: {github: dana-example, google: "1029384759"}
    memberships:
      - {team: product, role: lead, from: 2024-02-01}
```

```yaml
# state repo: people/ben.yaml — CODEOWNERS: people/ben.yaml @ben-example (only Ben edits it)
timezone: Europe/Lisbon
working_hours: {mon-thu: "09:00-17:00"}
preferences:
  opt_out: [newsletter.weekly]
  recognition: private              # drafts about Ben go only to Ben's manager, never public channels
  pseudonymise_in_prompts: true
```

**Identity resolution**

1. Each source returns records with its immutable ID: Google `id`, Entra object `id`, GitHub numeric user id plus
   login, Slack `U…`, Atlassian account ID.
2. Records are matched to a person through **email**: primary, aliases, Google `aliases`, GitHub SAML `nameId`.
   Matching is case-insensitive, after IDNA normalisation; plus-addressing is not stripped by default.
3. Once matched, the **source ID** is the anchor. An email or login change does not split a person. A renamed
   GitHub login is detected through the numeric id.
4. Anything unmatched or ambiguous goes to `hz org validate` as a finding. A human resolves it by adding
   `identities` or `aliases` in the declared file (by PR). There is no fuzzy matching on names.
5. **Approver and owner rule:** anyone who can be an approver or owner must have a `github` identity. ADR 0020
   puts approvals in issues and PRs and ownership in CODEOWNERS-protected policies. Receipts store
   `approval.by` as the hz handle plus the GitHub login.

**How workflows reference people** (align with ADR 0021; minimal interface only):

```yaml
audience:  [team:checkout]                                  # who receives the draft
subjects:  [team:checkout]                                  # whom the draft is about
approvers: [role:engineering-manager@team:checkout]         # any one of them may approve
owner:     role:engineering-manager@team:checkout           # owns the workflow and its auto-approval
except:    [opted-out]                                      # always applied, cannot be removed by a contract
```

- The selectors are `person:<id>`, `team:<id>` (members, `+sub` for descendants), `role:<id>@team:<id>`,
  `orgunit:<path>`, `manager-of:<selector>`, `reports-of:<selector>` (direct only) and `@subject` (the person a
  per-person draft is about).
- A selector that resolves to nobody is an error at registration and a `failed` receipt at run time. It never
  silently sends to nobody.
- Resolution is part of the job's input facets. A membership change changes the fingerprint and re-renders the
  job (ADR 0020 §4).

## 3. Commands

Under ADR 0018 the module is `hz org`: it does team work, so it is not under `hz system`. Nouns are singular. Every
command has `--json`.

| Command | What it does | Read-only |
| --- | --- | --- |
| `hz org show` | Sources, last sync, counts by team and role, validation status | yes |
| `hz org sync [--source X] [--dry-run]` | Fetch, normalise, diff, write the cache; propose a PR for declared changes. Also registered as a `WatchLet` (scope `team`, daily) so the daemon does it | no |
| `hz org validate` | Dangling references, manager cycles, empty selectors, approvers without a GitHub identity, teams without an owner, conflicting sources, expired memberships, unmatched identities | yes |
| `hz org chart [--root team:X] [--depth N] [--reporting] [--format mermaid\|dot\|svg\|json]` | Team tree by default; manager lines only with `--reporting` | yes |
| `hz org coverage [--workflow W] [--team T]` | Matrix of workflows × people (audience, subject, approver, owner) and the gaps | yes |
| `hz org person list\|show\|resolve <selector>\|me` | Lookups; `resolve` shows who a selector reaches today, after opt-outs | yes |
| `hz org person prefs set …` | Edits your own `people/<id>` file by PR | no |
| `hz org team list\|show`, `hz org role list` | Lookups | yes |
| `hz org privacy notice [--lang de\|en]` | Generates the employee notice from the registered contracts and enabled sources | yes |
| `hz org privacy export <id>` / `forget <id>` | Data subject access export (Art. 15); erasure: tombstone + cache purge + a PR removing state-repo entries (Art. 17) | export yes, forget no |

`hz people` (plural) and a top-level `hz person` are rejected because ADR 0018 calls for singular nouns.

**Coverage**

- For each registered workflow, resolve `audience ∪ subjects ∪ approvers ∪ owner`.
- Gaps:
  - active people in no workflow's audience (informational);
  - workflows whose selectors resolve to nobody, or to a single approver (warning);
  - approvers without a GitHub identity (error);
  - teams with members but no `engineering-manager` or `lead` (warning).
- Opt-outs are shown as counts per workflow, not names, except to the person themselves (`hz org person me`).

**Org chart rendering**

- **Mermaid** is the default: `flowchart TD`, one subgraph per team, people as nodes, roles as labels. GitHub renders
  it in issues, PRs and Markdown, so the chart can live in the state repo's README or an approval issue.
- Mermaid's defaults are `maxTextSize = 50000` characters and `maxEdges = 500`. `maxEdges` is a secure setting
  that cannot be raised inside the diagram, and GitHub's renderer cannot be configured. At about 60–100 characters
  per node and edge, a whole-company chart breaks at a few hundred people. So `--root` and `--depth` default to the
  caller's team and depth 2, and the command warns before the limits.
- **Graphviz DOT** (`--format dot`) for large charts. SVG through `dot -Tsvg`, a plugin that needs the binary;
  `--format json` for other tools.
- The chart shows teams and roles, not reporting lines, unless `--reporting` is given. Reporting lines are HR data
  that teams rarely need in drafts.

## 4. Privacy and works-council design rules

**Legal frame** (verify current state; not legal advice):

- **Lawful basis.** Use GDPR Art. 6(1)(b) (employment contract: who is in which team, who approves) and Art. 6(1)(f)
  (legitimate interest in running team communication), with a documented balancing test. After CJEU C-34/21
  (30 March 2023), German commentators treat §26(1) sentence 1 BDSG as unreliable as a stand-alone basis, so
  Art. 6 is the primary basis. A works agreement (Betriebsvereinbarung) can be a "more specific rule" under
  Art. 88 GDPR. Consent is weak in employment (imbalance of power, EDPB WP249), so opt-in is a design feature, not
  the lawful basis.
- **Principles.**
  - Purpose limitation and data minimisation (Art. 5(1)(b), (c)).
  - Storage limitation (Art. 5(1)(e)).
  - Data protection by design and by default (Art. 25).
  - Transparency (Art. 13/14).
  - Data subject rights (Art. 15–21).
  - Records of processing (Art. 30).
  - A DPIA (Art. 35) if systematic monitoring of employees is possible. Supervisory authorities' blacklists
    commonly include employee monitoring.
  - No decisions based solely on automated processing (Art. 22). Human approval (tenet 4) helps.
- **Co-determination.**
  - BetrVG §87(1) no. 6: the works council co-determines the introduction and use of technical systems "designed"
    to monitor behaviour or performance. The BAG reads this as **objectively suitable**, whatever the employer
    intends (BAG 13.12.2016 – 1 ABR 7/15; 10.12.2013 – 1 ABR 43/12). Standard software qualifies (Excel: BAG
    23.10.2018 – 1 ABN 36/18; Office 365: BAG 08.03.2022 – 1 ABR 20/21).
  - Any `hz` that processes who did what will almost certainly need works-council involvement. Design can only
    keep the agreement simple.
  - Since 2021 BetrVG §90(1) no. 3 covers AI in information and consultation, §80(3) lets the works council call
    in an AI expert, and §95(2a) covers AI in selection guidelines.
- **EU AI Act.**
  - AI systems for monitoring and evaluating the performance and behaviour of workers are high-risk (Annex III
    point 4(b)).
  - The Art. 6(3) derogation (narrow procedural task, preparatory to a human assessment) never applies when the
    system **profiles** natural persons.
  - Deployers must inform workers' representatives and the affected workers before use (Art. 26(7)).
  - Verify the current application date of the high-risk obligations. It was 2 August 2026; postponement proposals
    were pending.

**Design rules for hz** (each one testable):

1. **No per-person metrics.** No operation computes, stores or outputs per-person counts, rates, durations,
   rankings or scores (PRs, tickets, commits, response times, hours). Aggregates only at team level with a minimum
   group size (default 5). Below that, report work items without counts.
2. **Drafts are about work, not workers.** Status reports and newsletters are organised by outcome or work item.
   Names appear as owners of items, not as subjects of evaluation.
3. **Recognition starts from a human nomination.** hz drafts the text; it never picks who deserves recognition from
   activity data. Public recognition requires the subject's `recognition: public` preference.
4. **Opt-out always wins.** `except: [opted-out]` is applied by the `Directory` service. A contract cannot remove
   it.
5. **Purpose is declared per workflow.** Each contract states its purpose and the person attributes it reads. The
   privacy notice and the record of processing are generated from these declarations, so documentation cannot
   drift from the code.
6. **Activity data from ADR 0020 is minimised.**
   - `fleet/<person>@<host>.json` heartbeats are opt-in and hold date, not time.
   - Receipts keep `runner` for lineage (tenet 4).
   - `skipped` receipts stay local.
   - No command aggregates receipts per person.
7. **Never collect sensitive categories.**
   - No HR fields: compensation, performance ratings, absence reasons, health (Art. 9), contract type.
   - Availability is at most a boolean from the person's own calendar, opt-in.
   - Birthdays and work anniversaries are opt-in only.
8. **Data sent to models is minimised.** Prompts get only the attributes a contract declares. Optionally,
   pseudonymise (handles in the prompt, names put back after rendering). Model providers are processors: Art. 28
   DPA, EU region where available. This choice belongs to the controller (open-provider tenet 8).
9. **Local-first.** Caches are in `platformdirs.user_cache_dir` with mode 0600 and a TTL (default 24 h); no hz
   server.
10. **Templates ship with hz.**
    - `hz org privacy notice` (DE/EN): purpose, categories, sources, recipients, retention, rights, contact
      placeholders.
    - A works-agreement annex: system description, exhaustive purpose list, an explicit "no performance or
      behaviour control" clause, a list of excluded features, change procedure = PR review with a works-council
      CODEOWNER.

## 5. Storage

| Data | Where | Why |
| --- | --- | --- |
| Handles, GitHub logins, source IDs, roles, teams, memberships with dates | state repo `org/` | Needed by receipts, approvals, CODEOWNERS and inventory references (ADR 0023); reviewable by PR |
| Preferences, working hours, time zone (self-declared) | state repo `people/<id>`, owned by that person | Self-sovereign; edits leave an audit trail |
| Names, emails, titles, managers, locations | fetched at run time; local cache with TTL | Reduces exposure in Git history, which is hard to delete |
| Declared names and emails (small team, no directory) | state repo, private, limited to team members | Unavoidable without a directory; already visible to colleagues; minimal |

- **Repository access.** A private state repo; read access only for the covered teams (GitHub team permissions);
  CODEOWNERS on `org/` (team owners) and `people/*` (each person).
- **Encryption.** sops with age (or a cloud KMS) can encrypt individual values. Encrypted values cannot be reviewed
  in the PR diff that ADR 0020 relies on for ownership, and keys must be distributed and rotated. Use it only for
  attributes that must be committed and are not needed for review. Not the default.
- **Deletion.**
  - `forget` drops the cache, removes the person's files by PR and replaces their handle with a tombstone (receipts
    keep the handle, now unresolvable, so lineage survives pseudonymously).
  - Removing data from Git history needs `git filter-repo --sensitive-data-removal`, a force-push, cleanup of
    every clone and fork, and GitHub Support for cached views and PR refs. That is the strongest reason not to
    commit personal attributes at all.
- **Retention defaults** (configurable): expired memberships pruned after 90 days; inactive people tombstoned at
  the next sync; caches 24 h; receipts per the team's ledger policy (ADR 0020 open question 3).

## 6. Risks

- **Monitoring creep.** Plugins can compute per-person metrics anyway. Mitigations: a contract lint that rejects
  person-level aggregation in core workflows; documented rules for plugin authors; the works agreement names what
  is forbidden.
- **Lineage against erasure.** Receipts keep handles; erasure leaves pseudonymous lineage. Art. 17(3) exceptions may
  or may not apply; the controller decides.
- **Identity drift.** Logins and emails change, and people have several accounts. Anchoring on immutable IDs plus
  `validate` limits silent misrouting.
- **Admin scopes.** Google and Entra read access often needs admin consent; small teams fall back to declared plus
  GitHub.
- **Third-party processors.** Merge, Finch and LLM providers see personal data and need DPAs.
- **Matrix organisations.** Team trees plus several memberships cover most cases; dotted-line managers are carried
  but not used for approvals by default.

## 7. Open questions

1. Declared file format: TOML (ADR 0012) or YAML for data files? Recommendation: TOML, with SCIM JSON import.
2. Selector grammar: final syntax decided with ADR 0021; cross-references with ADR 0023's person and team IDs.
3. Is the hz handle chosen by people (`ava`) or generated (`p-7f3a`)? Readable handles are personal data too.
4. Minimum group size for team aggregates (5?), and does it belong in core or in config?
5. Who may run `forget`, and who approves its PR?
6. Should `hz org privacy notice` also produce Art. 30 record fields?
7. One state repo per org with directories per team (ADR 0020 open question 1) changes the access control above.

## Sources

- Google Workspace Directory API, [User resource](https://developers.google.com/workspace/admin/directory/reference/rest/v1/users)
  and [users.list](https://developers.google.com/workspace/admin/directory/reference/rest/v1/users/list)
- Microsoft Graph, [List manager](https://learn.microsoft.com/en-us/graph/api/user-list-manager)
- IETF [RFC 7643, SCIM Core Schema](https://datatracker.ietf.org/doc/html/rfc7643) (§4.1 User, §4.3 Enterprise User)
- Atlassian Teams [members public API](https://developer.atlassian.com/platform/teams/rest/v1/api-group-teams-members-public-api/)
  and [site-scoped teams KB](https://support.atlassian.com/jira/kb/how-to-fetch-site-scoped-atlassian-teams-via-public-teams-rest-api/)
- GitHub REST [teams](https://docs.github.com/en/rest/teams/members),
  [CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners),
  [removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)
- Slack [users.list](https://api.slack.com/methods/users.list), [usergroups.list](https://api.slack.com/methods/usergroups.list)
- Merge [Groups replacing Teams](https://help.merge.dev/en/articles/7170465-migrating-from-teams-to-groups);
  Finch [IndividualInDirectory.Manager](https://javadoc.io/static/com.tryfinch.api/finch-java/7.7.0/com/tryfinch/api/models/IndividualInDirectory.Manager.html)
- Mermaid config [maxEdges](https://mermaid.js.org/config/schema-docs/config-properties-maxedges.html),
  [schema](https://mermaid.js.org/config/schema-docs/config.html)
- GDPR, [Regulation (EU) 2016/679](https://eur-lex.europa.eu/eli/reg/2016/679/oj) (Art. 5, 6, 13–22, 25, 28, 30, 35, 88)
- EU AI Act, [Regulation (EU) 2024/1689](https://eur-lex.europa.eu/eli/reg/2024/1689/oj) (Art. 6(3), 26(7), Annex III 4(b))
- [BetrVG §87](https://www.gesetze-im-internet.de/betrvg/__87.html), [§90](https://www.gesetze-im-internet.de/betrvg/__90.html),
  [§80](https://www.gesetze-im-internet.de/betrvg/__80.html); [BDSG §26](https://www.gesetze-im-internet.de/bdsg_2018/__26.html)
- CJEU C-34/21 (30.03.2023), summarised by [CMS](https://cms.law/de/deu/legal-updates/EuGH-Keine-Generalklauseln-im-Beschaeftigtendatenschutz)
  and [Otto Schmidt](https://www.otto-schmidt.de/blog/arbeitsrecht-blog/eugh-kippt-beschaftigtendatenschutzrechtliche-generalklausel-ARBLOG0007899.html)
- BAG 1 ABR 7/15, 1 ABR 43/12, 1 ABN 36/18, 1 ABR 20/21, summarised by
  [Görg](https://www.goerg.de/de/aktuelles/veroeffentlichungen/20-03-2024/neues-zur-it-mitbestimmung) (verify against
  the decision texts)
- Article 29 Working Party, Opinion 2/2017 on data processing at work (WP249), endorsed by the EDPB
- AI Act for employers, secondary: [L&E Global](https://leglobal.law/?p=30725)
