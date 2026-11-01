---
status: proposed
date: 2026-10-25
implementation: partial
decision-makers: maintainer, architect and security reviewer
amends: 0018, 0024
related: 0006, 0012, 0014, 0017, 0020, 0021, 0025, 0028, 0030, 0032, 0035, 0037, 0040, 0049, 0068
---

# 0080: One invite line and a reviewed first win

## Context and Problem Statement

Joining a team should start with one shared invite line, discover useful information from existing vendor logins,
and offer a useful first draft. Earlier onboarding research predates the implemented team and join modules, private
call identity and durable-agent approval policy. This proposed decision preserves that journey while distinguishing
identity hints, repository access, installed code and authority to act. It does not claim the full journey is shipped.

## Decision Drivers

- A newcomer should reach a useful result without first learning the configuration model.
- Team-specific defaults and first-win skills belong to plugins and playbooks.
- A repository or vendor-login discovery result must not grant execution or impersonation authority.
- Every authorized head should use the same setup, identity, approval and scheduling services.

## Considered Options

- **One repository invite, progressive setup and an explicitly approved first draft** (chosen).
- A personalized token per newcomer: adds provisioning and a separate hosted invitation service.
- A configuration form before useful work: duplicates information available from existing services.
- Automatic execution of repository recommendations: gives remote data authority over local work.

## Decision Outcome

Chosen option: **one repository invite, progressive setup and an explicitly approved first draft**, because it
reduces setup effort without making discovered data an authorization source.

1. The invite identifies a team repository, never embeds a secret. Existing repository permissions control access
   to that repository. They do not establish the current local caller, approve a draft or authorize a scheduled job.
   Use the existing join front door and Prompter/setup seam rather than a second onboarding framework.
2. Code-host and office-suite adapters may suggest profile/roster matches from their authenticated accounts. Exactly
   one match avoids redundant questions; zero or multiple matches ask only for missing information. A chosen handle
   or an `--as` hint never replaces the trusted CurrentUser/call identity of ADR0068. Proposed roster changes remain
   proposals until an authorized team owner reviews them.
3. Team manifests and rosters are untrusted data. Show recommended plugins, origins, resolved versions, capabilities
   and effects before installation. Code is installed only after the person's explicit approval. Recommendations
   never silently bind credentials, schedule jobs, export personal data or change policy on a later fetch.
4. The manager owns the reviewed roster; hz may draft it from existing services. Keep minimal handles, account
   identifiers, roles and membership dates according to ADR0024. Source names, titles and reporting lines stay in
   their authorized systems unless a separate approved policy permits disclosure. Repository directories do not
   provide read-access control.
5. Keep profile, team, plugin and credential setup progressive and re-runnable. A persistent installation uses the
   supported uv tool installation path; a disposable uvx environment must not become an OS background-service path.
   The final documented invite syntax follows the verified distribution and console-script names.
6. A team's plugin/playbook may propose a first-win skill and draft. Before execution, show its inputs, outbound
   hosts, read/write effects and cost cap. Use the same approval/proof policy across UI, CLI and MCP; an agent cannot
   mint human consent, and a generic yes flag is not approval for arbitrary remote instructions. The result remains
   a local draft until separately approved publication.
7. First-run setup separately asks **Keep my Heinzelmännchen working while the app is closed.** Declining installs
   no scheduler or login item. Enabling configures and verifies the existing daemon; opening the desktop app at
   login remains a separate choice. A first win alone never installs or activates background work.
8. Expose actual status, missing prerequisites and the next action through every authorized head. Noninteractive
   callers receive structured missing-answer or approval requirements. Re-running join does not duplicate effects,
   silently spend again or overwrite the person's choices.

### Consequences

- Good: one understandable journey can serve a terminal, desktop UI and authorized coding agent.
- Good: existing logins reduce typing while local identity and approval remain explicit boundaries.
- Bad: identity matching, roster proposals and first-win execution need separate acceptance tests and permissions.
- Bad: illustrative research transcripts cannot be treated as current installation or command documentation.

### Confirmation

Existing team/join, org recommendations, setup and private identity tests cover parts of the journey. Before calling
this decision implemented, add end-to-end journeys with fake vendor CLIs and a local team repository: unique and
ambiguous roster matches; hostile recommendations; declined installation/background work; exact approved first-win
effects and cost limits; noninteractive parity; interrupted/resumed setup; and duplicate-free reruns. Only a verified
published distribution and actual desktop/background walkthrough establish installation acceptance.

## More Information

[Onboarding research](../research/onboarding.md) preserves the original journeys and illustrative transcripts.
ADRs0032 and0068 define the reused setup and approval seams. The current build order and release scope are tracked
by the coordinator; this proposed ADR adds no claim that unfinished roster, desktop or agent workflows are available.
