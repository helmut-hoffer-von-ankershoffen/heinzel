---
status: proposed
date: 2026-10-28
decision-makers: maintainer
amends: 0084
related: 0046
implemented-by: 247
---

# 0090: Installed apps share their authenticated URL

## Context and Problem Statement

An installed PWA reopened from the Dock authenticates with its saved device token. The sharing control previously
attached only a session token to its URL, leaving a paired PWA unable to copy a usable sign-in link.
The maintainer requires the PWA to display and copy its authenticated URL.

## Considered Options

- **Use the active session or device token** (chosen).
- Require opening a fresh session link before sharing: prevents sharing directly from the installed app.

## Decision Outcome

Chosen option: **use the active session or device token**, because an authenticated PWA must be able to share access.

- The host displays, opens and copies the approved HTTPS URL with the active bearer in its `#token` fragment.
- A session token takes precedence over a saved device token, matching API authentication.
- Existing readiness and exact approved-origin checks still apply; enabling sharing never copies automatically.
- A copied device token retains device authority. It cannot mint another device token, and its existing revocation
  and expiry apply to every browser using that same credential ([ADR 0046](0046-an-installable-web-app-with-paired-devices.md)).

### Consequences

- Good: the installed PWA can provide a working link without reopening through `hz ui`.
- Bad: recipients of a device link share that device credential and its revocation scope. The owner must treat the
  complete link as a credential.

### Confirmation

`web/src/components/ContributionToggle.test.tsx` verifies displayed, opened and copied URLs for device tokens in
persistent storage and session storage, session precedence, and refusal of unapproved origins.
