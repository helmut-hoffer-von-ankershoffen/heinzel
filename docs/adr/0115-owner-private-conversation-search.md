---
status: proposed
date: 2026-10-30
implementation: built
decision-makers: maintainer
related: 0099, 0109
---

# 0115: Search owner-private conversation history

## Context and Problem Statement

A person may need to find an earlier message by text or time within one conversation or across their own
conversations. Loading entire transcripts into the browser or model is unnecessary and can expose private content
or exhaust memory. Retained message bodies can expire and search indexes can be damaged or unavailable.

## Considered Options

- **Search verified owner journals in bounded steps with a disposable local index** (chosen).
- Load all transcripts into the client or a model: this grows with history and expands the private data exposed.
- Treat an index or navigation cursor as authority: either may be stale or incomplete.

## Decision Outcome

Owner-authenticated operations return bounded result pages for one conversation or traverse the owner's conversation
catalog using continuation hints. Owner-wide search advances in bounded event steps; building a single-conversation
index may scan its entire journal before returning. The verified journals and immutable bodies remain authoritative.

A process-local
SQLite full-text index accelerates suitable text searches; short or time-only searches use bounded scans. The index
limits retained indexed text and the number of cached sessions, requires SQLite secure deletion and can be discarded and rebuilt. Missing,
expired or damaged data and index state mean results cannot promise completeness. A query does not grant access or
place history in model context.

Search result navigation uses a hint bound to owner, storage, session and a verified journal prefix. The server
revalidates that prefix and target before returning a bounded message page. Hints are not credentials or proof against
same-user tampering. The client displays one bounded results page, offers explicit continuation, and identifies the
selected message only after the requested page loads. Searches do not modify authoritative conversation history.

### Consequences

A person can navigate retained history without downloading full transcripts, but single-conversation indexing and
catalog reconstruction can still require substantial local CPU and I/O. Progress and cached index state are process-local; multiple workers do
not share a completion guarantee. SQLite secure deletion does not guarantee erasure from Python or operating-system
memory. Authentication identity changes without a UI remount need separate verification.

### Confirmation

Focused tests cover owner and session refusal, query/time-bound continuations, index and journal inconsistencies,
expired bodies, and a 160-message conversation with a result beyond the initially loaded page. Component tests cover
bounded result pages, cancellation, selected-message navigation and source-session mismatch. These tests do not prove
search completeness, bounded total catalog cost, cross-worker progress or a mounted browser journey for this version.
