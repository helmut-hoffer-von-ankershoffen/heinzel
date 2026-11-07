---
status: accepted
date: 2026-11-07
implementation: partial
decision-makers: maintainer
amends: 0055
related: 0028, 0035, 0047, 0068, 0094
---

# 0136: Connectors run their vendor's sign-in in the app

## Context and Problem Statement

A connector's sign-in is a command the person runs in a terminal (ADR 0055): `gws auth login --scopes ...` for
Google. The calendar footer needs Google Calendar write access to decline, extend or annotate a meeting, and the
maintainer asked that the app wrap whatever permissions it needs: "The user must not be forced to use the gws CLI
themselves to auth." How does a person connect a tool, see what it was granted, and allow more, without a terminal,
while the vendor's tool keeps owning the secrets?

## Decision Drivers

- The vendor's tool owns the sign-in and its secrets (ADR 0035); hz never sees, stores or logs a token.
- The person's click on the vendor's own consent screen is the only manual step; nothing may automate it.
- Ask for the minimum: write access only when a feature that needs it is first used, explained in plain words.
- Generic: Google first; Slack and Atlassian follow through the same contract. Core never names a plugin (ADR 0094).
- Unknown stays unknown: a grant is shown granted only when the vendor confirms it.

## Considered Options

- **A sign-in let: the connector's provider runs its vendor's supported flow; core runs and tracks it** (chosen)
- **Open the in-app terminal with the sign-in command** (ADR 0055's plan): still a terminal, and a phone has none.
- **hz runs OAuth itself** with its own client and stores the tokens: hz would hold every vendor's secrets.

## Decision Outcome

Chosen option: **a sign-in let**, because the vendor's tool already has a supported flow that prints its consent page
and stores its own credentials; hz only needs to start it, show the page and read back the outcome.

- **`module.sign_in(grants=[...])(Provider)`** declares a `SignInLet`. Each `Grant` has a name, a short title, a
  plain-words explanation, the capability and modes it serves, the vendor scopes it takes (shown, never secret) and
  whether a first sign-in asks for it. The provider implements `SignInProvider`: `status(live=...)` (who, which
  grants are granted, missing or unknown, whether the tool is ready or how to install it through its supported path,
  any expiry or refresh problem), `sign_in(grants, progress, stop)` and `sign_out()`. Its `Login` checks stay as
  they are: doctor and `hz system connector list` are unchanged and offline.
- **`system.connector`** runs sign-ins: `access` (status, `--live` asks the vendor once), `connect` (asks for every
  grant that is granted, needed by what is bound, or a default nothing confirms either way, plus `--allow`; a vendor
  that replaces its scopes on each sign-in keeps the others), `flow`, `cancel`, `open` and `disconnect`. A sign-in
  runs in a thread of the serving process with a fifteen-minute limit; a second `connect` stops the first. The CLI
  waits, says the consent page and opens it in this machine's browser; the web app polls.
- **Where consent happens**: progress carries the consent page, a device code when the vendor offers one, and
  `finish_on`. `host` means the vendor returns the browser to a local address on the machine hz runs on, so a phone
  reaching hz over Tailscale is offered **Continue on this Mac** (`open`, which opens the page there) instead of a
  page that could not complete. The consent page opens from the person's click, so popup blockers don't stop it.
- **Secrets**: only https consent pages, codes and plain words cross the contract, each cleaned to one line. The
  service writes no log. The operations are on the CLI and the private web head only: never MCP, where an agent
  could start a sign-in, nor the open REST head (ADR 0047, ADR 0068).

### Consequences

- Good: Settings can say "signed in as ada@example.com", list each grant with what it permits, and offer Connect,
  Reconnect, Allow and Disconnect, with no terminal.
- Good: a feature that needs write access can ask for exactly that grant when first used.
- Bad: a sign-in in progress lives in one process; if hz restarts, the person starts it again.
- Bad: a vendor without a device flow (`gws`) can only finish on the machine hz runs on.

### Confirmation

`tests/unit/kernel/sign_in_test.py` (declarations and cleaning) and `tests/unit/modules/connector_sign_in_test.py`
(grant choice, progress, wait, cancel, timeout, isolation of a failing provider, open, disconnect) with a fake
provider. The Google plugin's sign-in is tested against a fake `gws` that prints a consent page and succeeds. Still to
come: the web app's controls and Slack and Atlassian sign-ins.
