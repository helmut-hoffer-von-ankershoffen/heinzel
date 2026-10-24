---
status: superseded
date: 2026-10-24
decision-makers: maintainer (delegated)
related: 0017, 0028, 0030
superseded-by: 0035
---

# 0034: Read Jira and Confluence Cloud through their REST APIs with urllib, Basic auth and epoch-millisecond JQL

## Context and Problem Statement

The adapter serves `tracker:read` (`issues_updated(project, window)`) from Jira and `documents:read` (`read(ref)`) from
Confluence, for [ADR 0028](0028-integrations-are-capability-adapters.md).
Atlassian Cloud offers several endpoints, two kinds of API tokens and a query language whose dates depend on the
account's time zone. Which transport, authentication, endpoints and window encoding?

## Decision Drivers

- No new heavy dependency: Heinzelmännchen's runtime doesn't depend on an HTTP client library or a vendor SDK.
- Exact half-open windows `[since, until)`, oldest first, independent of the time zone of the account.
- No unvalidated input in JQL or URLs; the token never leaves the configured site
  ([ADR 0030](0030-credentials-live-in-the-os-keychain.md)).
- Endpoints that are current, not deprecated.

## Considered Options

- **Site REST APIs over urllib, Basic auth with a token without scopes; `/rest/api/3/search/jql` with epoch
  milliseconds; Confluence v2 pages in storage format** (chosen)
- **The old Jira search, `/rest/api/3/search` with `startAt`**: removed from Jira Cloud (announced for 1 May 2025).
- **JQL with quoted dates (`updated >= "2026-10-01 00:00"`)**: Jira reads them in the profile time zone of the token's
  account, which the adapter doesn't know without another request (`/rest/api/3/myself`), and a DST gap can make a
  literal invalid. Widening by a day and filtering would work, at the cost of up to two days of extra issues.
- **Scoped API tokens through `api.atlassian.com/ex/{jira,confluence}/{cloudId}`**: least privilege, but a second
  host, a cloud id to discover (`/_edge/tenant_info`) and different base paths; parked, see below.
- **OAuth 2.0 (3LO)**: needs a registered app, a browser flow and refresh tokens; Heinzelmännchen has no OAuth flow yet
  (ADR 0030 "revisit when OAuth flows arrive").
- **Confluence REST v1 (`/wiki/rest/api/content/{id}?expand=body.storage`)**: still served, but v2 is the current API
  for pages.
- **`atlas_doc_format` (ADF JSON) instead of storage**: a richer tree to walk, but storage is XHTML that the
  standard library's `html.parser` reads, and every page has it.
- **httpx, `atlassian-python-api` or an MCP server**: a new dependency, or a transport the generic MCP adapter of
  ADR 0028 will cover anyway.

## Decision Outcome

Chosen option: **site REST APIs over urllib, Basic auth with a token without scopes, enhanced search with
epoch-millisecond JQL, Confluence v2 pages in storage format**, because it is exact, current and needs nothing beyond
the standard library.

- **Settings and auth.** `[atlassian] site_url` must be `https://<site>.atlassian.net` (a pasted `/wiki` or `/jira` is
  trimmed); `email` is the account's address; both are in `ignored_in_project`. The token is the credential
  `atlassian-token`; every request carries `Authorization: Basic base64(email:token)`. There is no anonymous mode:
  without the site, the email or the token the adapter refuses before sending anything, and doctor is degraded with
  the commands that fix it. The adapters' hosts are the configured site, a function of the settings (ADR 0028) that
  the settings only accept under `atlassian.net`; `unknown (configured)` until it is set.
- **Jira.** `GET /rest/api/3/search/jql?jql=...&fields=summary,status,updated,assignee,labels&maxResults=100`, then
  the same with `nextPageToken` until the answer has no token or says `isLast`. A short page is not the end; a token
  seen before stops with an error (tokens that cycle have been reported); 100 pages at most.
- **JQL.** The project is upper-cased and must match `^[A-Z][A-Z0-9_]{1,254}$` (Jira's key rule: a letter, then
  letters, digits or underscores) before it is used; it is then written as a JQL string with `"` and `\` escaped, so
  nothing in it can end the literal. The window is `updated >= <since ms> AND updated < <until ms>`: Jira reads an
  **unquoted** number as milliseconds since the epoch ([JQL fields: Updated](https://support.atlassian.com/jira-software-cloud/docs/jql-fields/)),
  an instant that the account's time zone doesn't move, whereas a quoted value is a date in that time zone. Bounds
  widen to whole minutes (`since` down, `until` up): JQL compares dates at minute resolution, and Atlassian doesn't
  document whether a millisecond bound is truncated, so an `until` of 10:05:15 could otherwise become `< 10:05` and
  leave out issues updated in the last seconds of the window; widened bounds are exact minutes, which no rounding
  moves. Results are `ORDER BY updated ASC, key ASC`, and each issue's `updated` (`2026-10-05T12:34:56.789+0200`) is
  compared with the exact window again, so the widening only costs up to two minutes of extra issues read; paging
  stops at the first issue at or after `until`. An issue updated while the pages are read can appear on two pages;
  it is returned once, in its latest state. An empty window (`since == until`) returns no issues without a request.
- **Issues.** `key` is the issue key, `state` the status name, `assignee` the display name (Jira has no logins),
  `labels` the labels; `url` is `<site>/browse/<key>`, built on the configured site, never taken from the answer.
- **Text from answers.** Every string the adapters return (an issue's title, state, assignee and labels; a page's
  title, text and path) and Atlassian's error text lose control characters (C0 except newline and tab, DEL, C1) and
  bidirectional overrides (U+202A–U+202E, U+2066–U+2069): anyone who can edit an issue or a page could otherwise hide
  or reorder what a terminal or an agent's prompt shows. Heinzelmännchen's CLI strips controls too; this is defence in
  depth, because the same text also reaches agents.
- **Confluence.** A ref is a page id or a URL whose scheme, host and port are exactly the configured site's, with the id
  in `/wiki/spaces/<space>/pages/<id>/...` (also `edit-v2/<id>`) or `/wiki/pages/viewpage.action?pageId=<id>`;
  anything else is refused before a request. `GET /wiki/api/v2/pages/{id}?body-format=storage`; `revision` is the
  version number; `url` is the site plus `/wiki` plus the answer's relative `_links.webui`, never its `_links.base`.
- **Storage to text** (`_storage.py`, `html.parser`): headings as `#`, paragraphs, line breaks, nested lists, task
  lists as `- [x]`, links as `[text](url)` (links to pages by their title), tables as pipe rows, inline code, and code
  and noformat macros as fenced blocks with their CDATA bodies; images, emoticons, mentions and macro parameters are
  dropped, other macros keep their text. Malformed markup degrades locally: a cell left open ends at the next cell,
  row or the end of its table, links can't outlive the cell they opened in, an unclosed image hides nothing past the
  next block, a link around blocks keeps them and names its target after them, and a code block containing backticks
  gets a longer fence. Best effort: it is for reading, not round-tripping. The output is bounded,
  because a page is input from anyone who can edit it: lists and tasks nested deeper than 16 levels are indented no
  further (otherwise the indentation grows with the square of the depth), and a page whose text would pass four times
  its storage size, at least 1 MB and at most 20 MB, is refused with an error instead of converted.
- **Transport and errors.** `heinzel_atlassian._transport.Transport` is the only code that sends requests; redirects
  to another origin are refused, answers above 10 MB are refused. 401 names the token and email fixes and the scoped
  token pitfall, 403 the missing permission, 404 and Jira's 400 (unknown or invisible project) the access to check;
  429 says when to retry from `Retry-After`. Nothing retries or sleeps. Atlassian's message text is shown redacted of
  the token, `email:token` and its base64 form, redacted whole and only then cut to 300 characters, so a cut can't
  leave the start of a secret unredacted.

### Consequences

- Good: exact windows in any time zone, no dependency, no deprecated endpoint; tests replace one class.
- Bad: a token without scopes can do whatever its account can; least privilege comes from the account, not the
  token, until the gateway is supported.
- Bad: sites on custom domains, `*.jira.com` and Data Center installations aren't supported; project keys outside
  Jira's default character set are refused.
- Bad: a busy project costs one request per 100 issues updated in the window; a call stops after 100 pages and asks
  for a shorter window. A rate limit fails the call instead of waiting.

### Confirmation

`plugins/heinzel-atlassian/tests/unit/jira_test.py` (the exact JQL, whole-minute widening and the exact filter, duplicates across pages, time zones, token paging, repeated tokens, key
validation), `plugins/heinzel-atlassian/tests/unit/confluence_test.py` (refs and foreign hosts), `plugins/heinzel-atlassian/tests/unit/storage_test.py`,
`plugins/heinzel-atlassian/tests/unit/client_test.py` (statuses, redaction of all three secret forms), `plugins/heinzel-atlassian/tests/unit/transport_test.py`
(same-origin redirects), `plugins/heinzel-atlassian/tests/integration/transport_test.py` (urllib against a local server: a redirect to another
origin is refused and nothing reaches it), `plugins/heinzel-atlassian/tests/unit/contract_test.py` and `plugins/heinzel-atlassian/tests/integration/capability_test.py` (bound by
configuration, a hostile project `hz.toml`, no secret in any output).

## More Information

- Not verified against a live site: no credentials were available while building; every answer shape comes from
  Atlassian's documentation and is reproduced in `plugins/heinzel-atlassian/tests/conftest.py`.
- Revisit for scoped tokens: discover the cloud id from `https://<site>.atlassian.net/_edge/tenant_info`, send
  requests to `https://api.atlassian.com/ex/jira/<cloudId>` and `/ex/confluence/<cloudId>` with the read scopes of
  the search and page endpoints (check Atlassian's scope list for each when building it), and add `api.atlassian.com` to the hosts.
- Revisit when the core gains approvals: `Documents.replace_section` (write) through Confluence v2 `PUT /pages/{id}`.
- Decided as ADR 0002 of the separate `heinzel-atlassian` repository, released there as v0.1.0; renumbered unchanged
  when the adapter moved into `plugins/heinzel-atlassian` as a first-party plugin (2026-10-24, see
  [ADR 0028](0028-integrations-are-capability-adapters.md) More Information).
