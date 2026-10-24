---
status: proposed
date: 2026-10-24
decision-makers: maintainer
amends: 0018, 0019
amended-by: 0041, 0043, 0046, 0047
related: 0003, 0020, 0021, 0023, 0024, 0027, 0029, 0036, 0037
---

# 0039: A web head: a React app served by the API head

## Context and Problem Statement

Heinzelmännchen has four heads: the CLI, MCP, REST and the daemon ([ADR 0003](0003-heads-are-views-on-operations.md),
[ADR 0019](0019-mcp-and-rest-heads.md), [ADR 0020](0020-daemon-head.md)). All of them are for people at a terminal
or for machines. What is coming is visual: drafts to read and diff and, later, approve in place
([ADR 0037](0037-plugins-bring-playbooks.md)), a graph of contracts ([ADR 0021](0021-workflow-contracts.md)), an
inventory of assets ([ADR 0023](0023-inventory.md)), an org chart and a team roster ([ADR 0024](0024-people-and-org.md)),
a doctor report with its fixes, and every setting in one place. A manager who approves the week's report should not
need a terminal; a browser is already open. The maintainer asked for "another head: a React application", hosted by
FastAPI and talking to the FastAPI API, and confirmed the stack and the layout below on 2026-10-24.

The REST head today has no authentication; it stays safe by answering only local, same-origin requests and by not
exposing anything that spends money or reads personal data (`workflow run`, `drafts`, `discover`, `org show`,
`system daemon tick` declare `heads={"cli", "mcp"}`; ADR 0019, More Information). A web head needs exactly those
operations, and an AI chat that runs the configured agent runner spends money. So the questions are: what the head
is made of, how it is served and secured, how its build fits a repository whose only prerequisites are git, make and
uv ([ADR 0036](0036-the-build-installs-its-own-tools.md)), and how it stays a view on the same operations.

## Decision Drivers

- Heads are views (ADR 0003): the UI never gets an operation the API doesn't have, and plugins' operations appear
  without UI code. A chat is no exception: it is an operation like every other.
- The API can spend money and shows data read from the person's systems, so a browser must prove it is the person's
  own tab, not any page that can reach `127.0.0.1` ([ADR 0019](0019-mcp-and-rest-heads.md), More Information).
- Nothing is sent home ([ADR 0027](0027-nothing-is-sent-home.md)): no CDN, no web font service, no analytics.
- A fresh clone needs only git, make and uv; every tool is pinned and checksummed (ADR 0036). `pip install heinzel`
  must not need Node.
- Typed end to end (tenet 10): the operations' pydantic models are the only source of the UI's types, and drift fails
  the build.
- Joyful and for all roles (tenets 1, 2): accessible, fast, no account, no server to run.

## Considered Options

- **Vite + React + TypeScript, built to static assets the API head serves on the same origin; a typed client
  generated from the OpenAPI schema; a per-session token** (chosen)
- **Next.js or Remix (React Router framework mode)**: need a Node server at run time (or lose the features that make
  them worth it); `hz` is a Python process installed with `uv tool`, and a second runtime per machine is against ADR
  0036 and against "one `hz` command".
- **HTMX with Jinja templates rendered by FastAPI**: no build step and Python only, but the screens are
  interactive (a graph, a diff, a chat) and would need hand-written JavaScript anyway; templates would become a
  second presentation layer inside the Python package, against ADR 0003's "the only presentation code is `render=`".
- **Svelte or SolidJS**: smaller bundles, but a smaller pool of accessible component libraries and of agents and
  contributors fluent in them; React's ecosystem (Radix primitives, TanStack, Testing Library) is the safer default.
- **A desktop shell (Tauri, Electron)**: a native app per OS to sign and distribute; the browser is already there.
- **A server-side GraphQL or tRPC layer**: a second API beside REST with its own schema; the OpenAPI schema FastAPI
  already derives from the operations is enough to generate a typed client.
- **Cookie session instead of a header token**: a cookie is sent by the browser with every request, so it needs CSRF
  defences on top; a bearer header a page sets itself is never attached by another origin.
- **Node through corepack for pnpm**: corepack is deprecated and no longer ships with Node 25; a pinned pnpm binary in
  `scripts/tools.toml` follows ADR 0036 instead.

## Decision Outcome

Chosen option: **a Vite + React + TypeScript application, built to static assets that the API head serves on the same
origin behind a per-session token, with a typed client generated from the OpenAPI schema**, because it adds no run
time beyond the Python process, keeps the UI a view on operations, and reuses the REST head's security model with one
addition, the token, that the browser can prove and another page cannot forge. Decided by the maintainer, 2026-10-24.

**The head.**

- `heinzel.heads.web` is the fifth head, named `web` in `heads=`. It builds the API head's FastAPI app for head `web`
  (the exposure rule of `heads/_exposure.py`, so `heads=` restrictions apply as in every head) and mounts it at `/api`
  (OpenAPI at `/api/openapi.json` and `/api/docs`); the built assets are served from `/` with the single-page
  fallback to `index.html`. `hz system serve api` is unchanged: no token, root paths, the restricted set of
  operations.
- `hz ui [--port N] [--no-browser]` starts it: a free loopback port unless given, a fresh token, the URL printed, the
  browser opened. It binds `127.0.0.1` only and has no `--host`; serving a team is a later ADR (team hosting: an
  identity, HTTPS, the state repository of ADR 0020). Someone on another machine uses an SSH port forward.
- `hz ui` sits at the top level like `hz setup` (ADR 0018, amended by 0032): it is what a person types to see
  Heinzelmännchen, not administration. Heads also never import modules (import-linter), so `hz ui` is a command of
  the CLI head beside `hz system serve`, not an operation.

**Security model.** Everything ADR 0019 does for the REST head stays, and a token is added:

- The token: 32 random bytes (`secrets.token_urlsafe`), new for every `hz ui` process, compared with
  `hmac.compare_digest`. It travels in the URL fragment of the opened link (`http://127.0.0.1:PORT/#token=...`), as
  Jupyter does; a fragment never reaches the server or its access log. The page keeps it in memory and
  `sessionStorage` and sends `Authorization: Bearer <token>` with every `/api` call. No cookie is set, so there is no
  CSRF surface: another origin can't read `sessionStorage` and can't make the browser attach the header. A request
  under `/api` without the right token gets `401`; static assets need no token and hold no data.
- The Host, Origin and `Sec-Fetch-Site` checks of the REST head apply to `/api` unchanged, as defence in depth
  (DNS rebinding, a page that guessed the port).
- Response headers on every response: `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src
  'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none';
  frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Opener-Policy: same-origin`, `Cache-Control: no-store` for `/api` and `immutable` for hashed assets.
  `connect-src 'self'` and `font-src 'self'` make "no third-party CDN" a rule the browser enforces (ADR 0027): fonts
  and icons are bundled or system. Any `'unsafe-inline'` needs a reason in this file's successor.
- Which operations the web head exposes: an operation hidden from the REST head only because "a web page can reach a
  local REST server" (`drafts`, `discover`, `org show`, `prune --dry-run`, `sent`) adds `web` to its `heads=`, because
  the token closes that hole. Operations that spend money stay off the web head (`workflow run`, `system daemon tick`)
  except the chat operation below, which carries its own cap; interactive ones (`setup`, `credential set`, `plugin
  new`, `daemon install`) stay CLI-only. Every change to an operation's `heads=` names its reason in a comment, as
  today, and a test lists what the web head must not expose. This amends ADR 0019's rule from "not REST endpoints at
  all" to "not endpoints of the unauthenticated REST head".

**Layout and first screens** (the maintainer's, 2026-10-24).

- Three columns: a left full-height sidebar with home and search on top, the navigation tree as its main content, and
  profile and settings bottom-left; the content in the middle; a right full-height sidebar with an AI chat. Both
  sidebars collapse; on a phone-width screen they become sheets.
- The chat runs the configured agent runner ([ADR 0029](0029-agent-runners-are-capability-adapters.md)) through an
  operation, `hz system runner ask <prompt> [--budget-usd]` (name to settle in its PR), which the CLI and MCP get too:
  no UI-only backdoor. It uses the read-only tool profile, the `Budget` and `AgentResult` the kernel already has, and
  the UI shows the cap before sending and the reported cost (`usage.cost_usd`) under every answer; a run stopped for
  its budget says so. The cap's default and ceiling are settings (`[system.runner] ask_max_usd`), user scope only
  (ADR 0028's rule for settings that spend).
- First screens, each a view on operations that exist or are added as operations first: all settings
  (`system config show`, `set`, `unset`, with the layer each value comes from); the full contract graph (`workflow
  list` and `show`; `hz workflow graph` when ADR 0021 builds it, so the UI computes nothing the CLI couldn't);
  org chart and team roster (`org` operations as ADR 0024 lands them; `org show` for the profile now); the inventory of
  assets (ADR 0023's operations when built; playbook assets from `workflow playbooks` and `discover` now); doctor
  (`system doctor`, each check with its fix). Drafts with read and diff follow (`workflow drafts`), then approval in
  place once ADR 0037's seams exist.
- Later, not now: a generic screen that renders any exposed operation from the OpenAPI schema (a form from the
  parameters, a table from the result), so plugins' operations appear with no UI code.

**The generated, typed client.**

- `scripts/generate_openapi.py` boots a kernel and writes `web/openapi.json` from the web head's app;
  `openapi-typescript` turns it into `web/src/api/schema.d.ts`; `openapi-fetch` is the client (one dependency, the
  types from the schema, `fetch` underneath). Both files are committed. `make fix` regenerates them; `make check`
  (`check-fast`'s `docs` step style) regenerates to a temporary file and fails on a difference, so an operation whose
  signature changed without the client fails the build, as generated docs do today.
- TanStack Query owns fetching, caching and invalidation; React Router the routes; Tailwind and shadcn/ui (copied
  components on Radix primitives, so they are ours to fix) the look; Biome formats and lints; Vitest with Testing
  Library tests components; Playwright tests the built app against a real `hz ui --no-browser`.

**Build, make, CI, wheel.**

- Node comes from the `nodejs-wheel-binaries` wheel already in `uv.lock` (`pyright[nodejs]`, ADR 0036) and runs as
  `uv run node`; pnpm is a pinned, checksummed binary in `scripts/tools.toml` (`build` group), installed by `make
  install` on all four platforms. `make doctor` lists both. No system prerequisite is added.
- The app lives in `web/` (`package.json`, `pnpm-lock.yaml`, `vite.config.ts`, `src/`, `tests/`); `pnpm build`
  writes `src/heinzel/heads/web/static/` (gitignored). `make build` builds the app first, and a hatch build hook
  refuses to build a wheel or sdist without `static/index.html`, so a wheel is complete or absent; the sdist ships
  the built assets, so `pip install` from the sdist needs no Node. CI's build job checks the wheel ships
  `heinzel/heads/web/static/index.html` beside the changelog.
- `make fix` adds `biome check --write` and the client generation; `make lint` adds `biome ci`; `make typecheck` adds
  `tsc --noEmit`; `make test-unit` runs Vitest; `make test-e2e` (`PREFLIGHT=full`) runs Playwright, whose browsers
  are pinned by its version and cached. `make audit` adds `pnpm audit` and a license check of the bundled
  dependencies against `.license-allowlist`: they ship in the wheel, so they must be MIT-compatible like Python
  dependencies. Dependabot gets an `npm` entry with the same cooldown.
- `scripts/sync_template.py` leaves the web targets out of the plugin template: plugins bring operations, not UI.
- Dev loop: `pnpm dev` runs Vite with its proxy to a running `hz ui`, so the browser stays on one origin and the
  Host and Origin checks hold in development too.

**Accessibility and i18n baseline.**

- WCAG 2.2 AA: Radix primitives for keyboard and ARIA; visible focus; no state carried by colour alone;
  `prefers-color-scheme` and `prefers-reduced-motion` honoured; `axe-core` runs in Vitest (`vitest-axe`) and in
  Playwright (`@axe-core/playwright`) and a violation fails the test.
- English only for now, but every string goes through one message catalogue from the first screen, dates, numbers and
  relative times through `Intl`, and `<html lang>` is set; adding a language is a file, not a refactor.

### Consequences

- Good: no second runtime, no account, no server of ours: `hz ui` opens a page that talks to the process the person
  already runs, and the wheel is still the whole product.
- Good: a plugin's operations reach the UI the day they exist (restricted only by `heads=`), and the chat, the
  drafts and the approvals are operations the CLI, MCP and the web head share.
- Good: type drift between operations and UI is a failing `make check`, like generated docs today.
- Bad: a second language, toolchain and lockfile in the repository; `make install` adds pnpm (a few MB) and `make
  check` gets slower by a TypeScript build. Biome, Vitest and Playwright are more tools to keep pinned.
- Bad: the sdist and wheel grow by the built assets (a few hundred kilobytes); a wheel can no longer be built on a
  machine without Node unless the assets are already built.
- Bad: a new tab or browser needs the link again (the token lives in `sessionStorage`); `hz ui` prints it, and
  `hz ui --no-browser` prints only it. Accepted over a cookie and CSRF tokens.
- Bad: ADR 0019's "not REST endpoints at all" becomes a two-tier rule (unauthenticated REST head, token-protected web
  head) that reviewers must keep in mind when an operation sets `heads=`.
- Neutral: team hosting, identities and HTTPS are a later ADR; this one is localhost only.

### Confirmation

- `tests/integration/web_security_test.py` (to write with the head): a `/api` request without the token or with a
  wrong one gets `401`; the right token works; the REST head's Host, Origin and `Sec-Fetch-Site` refusals still
  apply under `/api`; every response carries the CSP and the other headers above; `workflow run`, `system daemon
  tick`, `credential set`, `credential remove`, `plugin new` and `daemon install` are not web endpoints; `hz system
  serve api` has no token and exposes the same set as before (`tests/integration/rest_security_test.py`).
- `tests/unit/heads/exposure_test.py`: `web` is a head name `heads=` restricts like the others.
- `make check`: the regenerated `web/openapi.json` and `schema.d.ts` equal the committed ones; `biome ci`, `tsc
  --noEmit` and Vitest pass; `tests/unit/tools_test.py` pins pnpm on all four platforms; a test checks that
  `web/package.json`'s `engines.node` matches the Node version in `uv.lock`.
- CI's build job: the wheel ships `heinzel/heads/web/static/index.html`; `make audit` covers the npm dependencies.
- `tests/unit/documentation_test.py`: this record, its index row and the `amended-by` links on ADRs 0018 and 0019.
- Review only: a new `heads=` value or an `'unsafe-inline'` in the CSP names its reason.

## More Information

- Decided by the maintainer, 2026-10-24: a React application as a head, hosted by FastAPI and talking to its API;
  the stack above ("ok, go for it"); the three-column layout with the AI chat on the right; the first screens
  (settings, contract graph, org chart and roster, inventory, doctor); localhost only with a session token for now,
  team hosting in its own ADR.
- Open: the chat operation's name and whether it keeps a session across turns (a session id in `AgentResult`, or the
  UI resends the transcript); whether `hz system serve api` should take `--token` too, giving scripts the web head's
  set of operations; what `hz ui` shows when a `heads=` operation needs a terminal (a copyable command).
