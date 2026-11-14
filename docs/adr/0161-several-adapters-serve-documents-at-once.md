---
status: proposed
date: 2026-11-14
implementation: built
decision-makers: maintainer (need), builder (shape)
amends: 0028
amends-0028: a capability that allows it is served by several adapters at once, routed by item and fanned out for lists
related: 0021, 0026, 0028, 0055, 0057, 0159, 0160
---

# 0161: Several adapters serve documents at once

## Context and Problem Statement

People keep documents in more than one place: Google Docs and Drive for one team, a Confluence wiki for another.
Until now Core bound one adapter per capability ([ADR 0028](0028-integrations-are-capability-adapters.md)), so
someone with both the Google and the Atlassian plugin had to pick one, and links to the other failed.
`Adapters[P]` ([ADR 0057](0057-ai-access-is-a-module-one-choice-like-the-theme.md)) hands out every installed
adapter, but only to the module that chooses among them. It doesn't route a call.

How can several `documents` adapters serve at the same time, with existing configurations and single-adapter setups
working as before?

## Decision Drivers

- One binding must keep working exactly as today: same handle, same errors, same configuration.
- A link must reach the connector it belongs to without the person saying which one.
- A slow or broken connector must not break the others' results.
- Plugins declare what they serve. Core never knows vendor URL formats.
- The mechanism must be generic, so another capability can switch it on with a declaration.

## Considered Options

1. **One router adapter per plugin**, which reads Docs and Confluence links itself. Rejected: it couples unrelated
   plugins, and every pair of vendors would need its own router.
2. **The caller picks** with `Adapters[Documents]` and a pinned requirement. Rejected: every feature would
   reimplement routing and merging, and contracts can't pick at all.
3. **A multi-binding in the kernel**, keyed by capability. The capability declares which methods route on an item and
   which fan out. Adapters declare the hosts of their links. Chosen.

## Decision Outcome

Chosen option 3.

**Configuration.** `[capabilities] documents` takes one adapter name, as before, or a list:
`documents = ["google", "atlassian"]`. A comma-separated string (`google,atlassian`) is the same list, which serves
`HZ_CAPABILITIES__DOCUMENTS` and `hz system config set`. The first adapter is the default. An empty list, or
`["none"]`, binds nothing; `none` can't be listed with adapters.

Settings shows a switch per installed documents connector instead of one choice, says which one is the default, and
counts every connector that is on as in use. Only capabilities with `Capability.multiple` take a list. The others
refuse one, as before.

**Declarations.** `Capability` gains:

- `multiple`: several adapters may be bound.
- `routes`: methods that act on one item, mapped to the parameter that names it. For documents, `read` and
  `replace_section` route on `ref`.
- `fan_out`: read methods that return a list and are asked of every adapter. For documents, `search`.
- `optional`: methods an adapter may leave out, the field
  [ADR 0160](0160-chat-is-an-extra-place-for-notices-and-questions.md) added. For documents, `search`.

`module.provides(..., links=...)` declares the **hosts of the links** an adapter opens, such as `docs.google.com`, as
a static list or as a function of the module's settings, like `hosts=`. Declared `hosts` are where an adapter sends
data, often an API host such as `docs.googleapis.com`, not where people open documents. When `links` is absent,
routing falls back to `hosts`.

**The protocol.** `Documents.search(query: str, limit: int) -> list[DocumentHit]` is a new optional read method.
`DocumentHit` holds `ref`, `title`, `url`, `snippet`, `updated_at` and `source`. `Document` gains `source` too. Core
sets `source` to the adapter's name, and adapters leave it empty. An adapter without `search`, or one that declares it
`unsupported` ([ADR 0159](0159-plugins-bring-their-own-ai-models-through-pydantic-ai.md)), raises `NotSupportedError`
when asked alone, and is reported as "can't search" in a fan-out.

**Routing.** With more than one adapter bound and no pinned requirement, a requester's handle routes each call:

1. A link goes to the one adapter whose links contain its host, matched exactly. If none or several match, Core
   refuses and names each adapter with the hosts it opens. Links are read strictly, where browsers and Python's
   parser disagree: only `http` and `https` written `scheme://host`; no user information, backslash, percent escape
   or whitespace in the host part, and no tab or newline anywhere. Anything else is refused, never routed. Whitespace
   around the whole reference is stripped, as a browser does.
2. `<adapter>:<id>`, where `<adapter>` is one of the bound adapters, goes to that adapter, which gets `<id>`. For a
   link, the prefix only confirms: the named adapter must open its host, or Core refuses. The prefix never overrides
   a host.
3. An id goes to the adapter that returned it earlier in this process, as a `ref` or `url` of a result. Core remembers
   every adapter that gave an id out, for the last 4096 ids. An id more than one adapter gave out is refused, naming
   them: it never goes to whichever answered last. This memory is per process and shared by every requester and
   every chat. It is lost on restart, so a bare id then needs the prefix again, and a chat grant or draft on one fails
   closed: its approval no longer routes, and is refused rather than sent to another connector.
4. Otherwise Core refuses: "'4242' could be in google or atlassian: pass its link, or name the connector, e.g.
   google:4242."

A write routes the same way. If the owning adapter can't write, Core says so, naming the adapter. A method that names
no item, such as a future `create`, goes to the default.

`hz system doctor` warns when connectors that are on declare the same link host: such links are refused, so one
connector claiming another's host breaks its links.

**Fan-out.** Core asks each adapter at once, each in a daemon thread with the caller's context. Every adapter has
`Capabilities.fan_out_timeout` (20 s). An adapter may have at most `fan_out_cap` (4) calls running; a hung one gets no
new thread and is reported busy, so threads stay bounded.

Each answer is validated strictly against the method's return type, and one in the wrong shape is that adapter's
failure: a string, a mapping or another iterable is not a list, and a list with an item of the wrong type is wrong
too. With one adapter bound, its call fails with that reason, never with a validation error of the whole result.
Results are merged in turns, binding order first, which is stable and fair, and are cut to `limit`. The result is a
`Gathered` list, still a plain list, whose `failures` name each adapter that failed and why:

- a `HeinzelError`'s own message, cleaned and cut to 300 characters. It is the connector's own words, content from
  it like a title or a snippet, never an instruction. Chat never searches through this handle: its reads and drafts
  go through approved grants, whose failures are redacted;
- the type only for any other error, since its message may hold a token;
- "didn't answer within 20 s", or "still busy with earlier requests";
- "answered in a shape search doesn't return";
- "can't search".

Only when every adapter fails does the call fail, with every reason.

**Labels.** Core sets `source` on every `Document` and `DocumentHit` an adapter returns, also when one adapter serves
alone, and never keeps what the adapter put there.

**One adapter.** With one bound, the handle is today's `_Handle`, and it routes nothing. Its results carry `source`,
and a fan-out method's results come back as `Gathered`.

**Features that use documents:**

- `hz document read` and `hz document search`, a new module on the CLI, MCP and the web app but not the open REST
  head. `search` returns `hits` and `failures`.
- Contracts and workflows: compiled jobs get the routed handle through `Kernel.handle_for`. A contract input can name
  `documents.search` with fixed `args` such as `{query: weekly, limit: 10}`. Read methods may now take an `int`.
- The approved version of an asset (`documents:<ref>`) reads through the routed handle.
- Chat's connected reads route each grant to the adapter that holds its `ref` (`Kernel.capability_route`). The adapter
  is part of the reviewed binding digest, so changing which connector holds a document invalidates the approval.
  Changing the list of bound adapters invalidates it too.
- Chat drafts and captured writes go to the connector that holds the document, routed as its read is: by link host,
  by `<adapter>:<id>`, or by the remembered id; an ambiguous reference is refused, and no draft is offered for it.
  The write binding the owner approves holds that adapter and the list of bound adapters, so a change of which
  connector holds the document, or of the list, invalidates the approval. If the connector that holds it can't
  write, Core says so, naming it, and offers no draft. A proposed new file names no document and goes to the
  default.
- `hz system capability list` shows every bound adapter, `multiple`, and each adapter's links.

**Calendar, tracker and mail stay single for now.** The mechanism is generic, but switching them on is not free:

- **Calendar:** `events(window)` would fan out naturally. Event ids carry no host, so updating or cancelling an event
  needs ids remembered across processes, and `Event` needs a `source`. Two calendars of one Google account are already
  served by one adapter.
- **Tracker** (for example, Jira and GitHub issues): `issues_updated(project, window)` names a project in the vendor's
  notation, so it routes on the project, not a URL. Keys like `APP-12` and `org/app#12` carry no host, so routing needs
  a `project` route and per-adapter key patterns.
- **Mail:** one mailbox per person, never chosen automatically (ADR 0055). Two mailboxes would also need a send
  identity, a separate decision.

Each can be switched on with `multiple=True`, `routes`, `fan_out` and a `source` field on its models, plus tests,
once the need is concrete.

### Consequences

- Good: Google Docs and Confluence work together, and a link opens in the right one without configuration beyond
  switching both on.
- Good: one binding, existing configurations and existing plugins behave as before. `links=` and `search` are
  optional, so plugins adopt them when they can.
- Good: a search reports a failing connector next to the others' results instead of failing.
- Bad: an id without a link is routed only within one process. The CLI asks again in a new process, and the error
  says how to name the connector.
- Good: chat drafts and captured writes reach the connector that holds the document, and an approval stops
  matching when that connector changes.
- Bad: a draft for a document whose connector can't write is not offered, even when another connector could write
  it: a draft never moves to another connector. Drafts are offered only while the default connector can write.
- Bad: a configuration with a list can't be read by an older hz. Single-name configurations load unchanged, so
  the release notes say that switching on a second documents connector is one-way.
- Neutral: the public API gains `DocumentHit`, `Gathered` and `SourceFailure`, and `Document.source`
  ([ADR 0026](0026-plugin-api-versioned-with-semver.md)).

### Confirmation

- `tests/unit/kernel/multi_adapter_test.py` covers routing by links, by hosts, by id and by prefix; overlapping and
  unknown hosts; fan-out with a failing, a crashing, a hanging, a non-searching adapter and one answering in a wrong
  shape, alone or with others; writes to the owner and to a read-only adapter; one binding unchanged; and the
  settings forms.
- `tests/integration/captured_write_owner_test.py` covers reviewed writes and chat drafts with two connectors on: a
  write reaches the connector holding the document by link and by prefix, an ambiguous reference is refused, a
  change of owner or of the bound list invalidates the approval, and a connector that can't write is named.
- `tests/integration/document_test.py` covers the CLI commands, the heads they are on, and `config set` with a list.
- The Vitest cases in `web/src/layout/SettingControls.test.tsx` and `SettingsStructure.test.tsx` cover the switches,
  the default and connector use.
- The e2e journey `tests/e2e/multi_documents_test.py` (`web/e2e/multi-documents.mjs`) switches two documents
  connectors on in mounted Settings in Chromium and WebKit, on desktop and phone. It then routes a link to each and
  searches both on the saved configuration.

## More Information

The worked example plugin ships two documents adapters, drive-like and wiki-like, each declaring `links=` and
`search`, instead of one adapter that routes inside itself.
