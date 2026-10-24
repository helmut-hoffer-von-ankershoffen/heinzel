---
status: accepted
date: 2026-10-24
decision-makers: maintainer
related: 0017, 0027, 0028, 0030, 0032, 0035
---

# 0042: Read Google Workspace through the Google Workspace CLI (`gws`)

## Context and Problem Statement

[ADR 0035](0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md) decides that adapters reach a service
through the vendor's CLI, which owns authentication. This record is how the Google Workspace adapter does it.

`heinzel-google` is the first-party default provider of the `documents` (Docs, Sheets) and `calendar` capabilities
for Google Workspace ([ADR 0028](0028-integrations-are-capability-adapters.md)). Google's APIs need OAuth 2.0: an OAuth
client, a consent flow in the browser, a refresh token kept somewhere safe and short-lived access tokens. The
GitHub and Atlassian adapters send personal tokens over `urllib`; Google has no such token. Who signs in, keeps the
tokens and talks to the APIs?

## Decision Drivers

- The least security-critical code of our own: no OAuth flow, loopback server or token refresh to get wrong.
- The narrowest read-only scopes, and no token in Heinzelmännchen's output, logs or prompts.
- No heavy Python dependency for four read endpoints; one way per vendor, easy to replace by another provider.
- Doctor doesn't contact Google; a project `hz.toml` decides nothing about where data comes from.

## Considered Options

- **Run the open-source Google Workspace CLI, `gws`** (chosen)
- **Our own OAuth installed-app flow (PKCE, loopback redirect) and REST calls over `urllib`**, the refresh token in
  `heinzel.Credentials`: works without another program, but we would own the consent flow, a local HTTP server,
  token refresh and its error cases, and every user would create an OAuth client by hand.
- **Google's Python client libraries** (`google-api-python-client`, `google-auth-oauthlib`): a large dependency tree in
  every installation of the plugin, for four read endpoints, and the OAuth flow still to wire up.

## Decision Outcome

Chosen option: **the plugin runs `gws`**, because `gws` already owns sign-in, credential storage and API discovery,
so the plugin holds no OAuth client, refresh token or access token, and needs no Python dependency.

- Each read runs `gws <service> <resource> <method> --params <JSON> --format json`: an argument list, never a shell
  string; parameters are one JSON argument.
- The executable is `google.gws_path` (absolute; ignored in a project `hz.toml`) or `gws` on the absolute entries of
  `PATH`, never a relative one, so the working directory can't choose the program.
- `gws` runs with the root directory as its working directory, because it reads a `.env` from where it starts, which
  a project could ship (a token, credentials file, OAuth client or log directory of its choosing); its environment
  is an allowlist (`PATH`, `HOME`, user, locale, `TMPDIR`, the XDG and D-Bus variables its keyring needs, proxies and
  certificates, and of `gws`'s own variables only `GOOGLE_WORKSPACE_CLI_CONFIG_DIR`, `_KEYRING_BACKEND`,
  `_CLIENT_ID`, `_CLIENT_SECRET` and `GOOGLE_WORKSPACE_PROJECT_ID`), so neither `HZ_CREDENTIAL_*` nor an unrelated
  secret reaches it, nor `GOOGLE_WORKSPACE_CLI_TOKEN` or `_CREDENTIALS_FILE` (another identity), `_SANITIZE_*` (every
  answer sent to a Model Armor template in some Cloud project) or `_LOG` and `_LOG_FILE` (logs of the traffic); it runs in its own process group, stopped with SIGTERM and SIGKILL past `google.timeout`;
  at most 10 MB of its output is read, and output still held open by something it started counts as cut.
- `gws`'s exit codes map to errors that say what to do: 2 (auth) names the exact
  `gws auth login --scopes ...` with the four read-only scopes `documents.readonly`, `spreadsheets.readonly`,
  `drive.metadata.readonly` and `calendar.readonly`; 1 (API) by Google's code and reason (rate limit, missing scope,
  disabled API, not found, forbidden); 4 (discovery) says to check the network. Token-shaped strings are redacted
  before any text is cut. Rate limits are retried after 1, 2 and 4 seconds; `gws` doesn't pass `Retry-After` on.
- `gws` 0.22.0 is the minimum: the first read of a session runs `gws --version` and refuses an older one with the
  upgrade command.
- `hz system doctor` runs only `gws --version` and reads `gws`'s configuration directory
  (`GOOGLE_WORKSPACE_CLI_CONFIG_DIR`, else `~/.config/gws`) for `client_secret.json` and `credentials.enc` or
  `credentials.json`; it never runs `gws auth status`, which may refresh the token and ask Google who is signed in.
  It reports whether `gws` is found and new enough, has an OAuth client and a stored sign-in (whether Google still
  accepts it shows on the first read), and names `hz setup google` ([ADR 0032](0032-modules-declare-interactive-setup-steps.md)),
  which finds `gws`, runs its login with the terminal and binds the capabilities.
- Credentials stay with `gws` (encrypted, with the key in the OS keychain, by default). The plugin reads no secret,
  so [ADR 0030](0030-credentials-live-in-the-os-keychain.md)'s "only way in" holds for it; `hz system credential list`
  shows no Google entry, and doctor covers the sign-in instead.
- Docs and Sheets JSON become Markdown-flavoured text through a bounded converter: output at most four times the JSON,
  tables and tabs nested at most 8 deep, at most a million cells; titles and link targets can't start a line or end a
  link; everything returned passes `clean_text`.

### Consequences

- Good: no OAuth or token code of ours; `gws` updates follow Google's APIs; another provider can be bound instead.
- Bad: npm's `gws` is a Node launcher that downloads its platform binary when it is missing, even on
  `gws --version`; a broken install can therefore reach the network from doctor. Setting `google.gws_path` to the
  binary itself avoids the launcher.
- Bad: a second runtime outside uv, Node and npm, which `hz system plugin add google` does not install; setup and
  doctor say how.
- Bad: `gws` is 0.x and "not an officially supported Google product": its exit codes and JSON are not a stable
  contract. The plugin is tested against 0.22; a changed shape shows as an error that names the command, and the
  stand-in tests pin what the plugin relies on.
- Bad: `drive.metadata.readonly` is a scope Google classes as restricted; a person's own OAuth client in testing or
  internal mode needs no verification, a published one would.
- Neutral (ADR 0027): data goes from Google to this machine only: file ids, the Drive query (the modified-time window
  and the name filter), the calendar window and the mail search go to Google's API hosts, and `gws` also fetches API
  discovery documents and refreshes tokens at `oauth2.googleapis.com`, which every provider declares among its hosts.

### Confirmation

`plugins/heinzel-google/tests/integration/runner_test.py` (argument list, neutral working directory, no
`HZ_CREDENTIAL_*`, the environment allowlist, timeout and kill, output bounds, a real auth failure, with a stand-in `gws`);
`tests/unit/gws_test.py` (arguments, PATH and `gws_path`, exit codes, retries, redaction before cutting, paging
bounds); `tests/integration/capability_test.py` (a project `hz.toml` can neither bind nor choose the program; doctor
runs only `gws --version` and reads `gws`'s files); `tests/unit/docs_test.py` (a property test and deep nesting); `tests/unit/setup_test.py`.

## More Information

- The maintainer chose `gws` as the backend on 2026-10-24, replacing a first draft with its own OAuth flow.
- Mail (Gmail) follows this record: `gmail users messages list/get` and `users labels list` through `gws`, with
  `gmail.readonly`, which Google classes as restricted and which covers all of a person's mail, asked for only when
  `mail` is bound to `google` (setup offers it with "no" as the default, then signs in again with the scope added).
- The directory joins this plugin as a further `gws`-backed provider once its protocol is in the core.
