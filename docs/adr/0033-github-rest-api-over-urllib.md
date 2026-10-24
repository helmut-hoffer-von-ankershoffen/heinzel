---
status: superseded
date: 2026-10-24
decision-makers: maintainer (delegated)
related: 0017, 0028, 0030
superseded-by: 0035
---

# 0033: Read GitHub through its REST API with urllib; merged pull requests from the pulls list

## Context and Problem Statement

The adapter serves `code-host:read` (`merged_pull_requests`) and `tracker:read` (`issues_updated`) of
[ADR 0028](0028-integrations-are-capability-adapters.md).
It needs a transport to GitHub and a way to find the pull requests merged in a window, which the REST API offers in two
ways. Which transport, and which endpoint?

## Decision Drivers

- No new heavy dependency: Heinzelmännchen's runtime doesn't depend on an HTTP client library.
- Exact half-open windows `[since, until)`, oldest first, for every repository size.
- Rate limits: a weekly workflow must not exhaust them; anonymous use must work for public repositories.
- A token never leaves the configured API origin ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)).

## Considered Options

- **REST API over urllib; pulls list sorted by update** (chosen)
- **Search API** (`is:pr is:merged merged:A..B`): one query per window, but its own, lower rate limit (30 requests a
  minute with a token, 10 without), at most 1,000 results, day- or second-granular inclusive ranges that need filtering
  anyway, and an index that lags behind.
- **GraphQL API**: fewer bytes, but needs a token even for public repositories and another query language to maintain.
- **httpx or the `gh` CLI**: a new dependency or an external program to install and authenticate.

## Decision Outcome

Chosen option: **REST API over urllib; pulls list sorted by update**, because it is exact, uses the core rate limit
(5,000 requests an hour with a token) and needs nothing beyond the standard library.

- `merged_pull_requests`: `GET /repos/{owner}/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100`,
  keeping items whose `merged_at` is in the window, each pull request once by number. Merging updates a pull request,
  so paging stops at the first item updated before `since`; how far it pages depends on `since` alone.
- `issues_updated`: `GET /repos/{owner}/{repo}/issues?state=all&since=...&sort=updated&direction=asc&per_page=100`,
  dropping the pull requests GitHub mixes in and stopping at the first item updated at or after `until`.
- `heinzel_github._transport.Transport` is the only code that sends requests; redirects and `Link` next pages outside
  the configured API origin are refused. Rate limits fail with the reset time; nothing retries or sleeps.
- A call follows at most 100 pages, 10,000 items updated since `since` (for pull requests including those updated
  after `until`), then fails: for pull requests asking for a window that starts more recently, for issues for a shorter
  window.

### Consequences

- Good: exact windows, no result cap, no dependency; tests replace one class.
- Bad: a busy repository costs one request per 100 pull requests updated since the window started, including updates
  after it ended; a call stops after 100 pages (10,000 updated items).
- Bad: pages are separate requests over a list sorted by update, so an item updated while a call pages moves. Pull
  requests (newest first) shift down and one can appear on two pages, which the number dedups; one updated during the
  call jumps to a page already read and can be missed. Issues (oldest first) shift up, so one can be skipped at a page
  boundary, and one updated during the call can appear again at the end if the window reaches into the present. A
  repeated call after the window has passed sees a stable list.

### Confirmation

`plugins/heinzel-github/tests/unit/adapter_test.py` (the window, the early stop, no PRs among issues, the dedup, the page-cap advice),
`plugins/heinzel-github/tests/unit/client_test.py` (rate limits, foreign next links and redirects, redaction, malformed tokens) and `plugins/heinzel-github/tests/unit/transport_test.py` (same-origin redirects).

## More Information

- Decided as ADR 0002 of the separate `heinzel-github` repository, released there as v0.1.0; renumbered unchanged when
  the adapter moved into `plugins/heinzel-github` as a first-party plugin (2026-10-24, see
  [ADR 0028](0028-integrations-are-capability-adapters.md) More Information).
