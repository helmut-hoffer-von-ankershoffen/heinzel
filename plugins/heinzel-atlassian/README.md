# heinzel-atlassian

Read Jira issues and Confluence pages, the `tracker` and `documents` capabilities ([ADR 0028](../../docs/adr/0028-integrations-are-capability-adapters.md)): a first-party
[Heinzelmännchen](../../README.md) plugin. It is released with the core, at the same version, and not installed by default:

```bash
hz system plugin add atlassian             # the release matching your hz; run it again to upgrade
hz system plugin remove heinzel-atlassian
```

## Set up

Name your Atlassian Cloud site and the email address of your Atlassian account, in your user configuration:

```bash
hz system config set atlassian.site_url https://acme.atlassian.net
hz system config set atlassian.email ada@acme.example
```

Bind the adapter, also in your user configuration, never in a project `hz.toml`, which Heinzelmännchen ignores for
bindings:

```bash
hz system config set capabilities.tracker atlassian
hz system config set capabilities.documents atlassian
```

which together write

```toml
[atlassian]
site_url = "https://acme.atlassian.net"
email = "ada@acme.example"

[capabilities]
tracker = "atlassian"
documents = "atlassian"
```

Or per shell: `HZ_ATLASSIAN__SITE_URL`, `HZ_ATLASSIAN__EMAIL`, `HZ_CAPABILITIES__TRACKER=atlassian`,
`HZ_CAPABILITIES__DOCUMENTS=atlassian`. `site_url` and `email` are ignored in a project `hz.toml`: the token goes to
that site, as that account. `site_url` must be `https://<site>.atlassian.net`; a pasted `.../wiki` is trimmed.
Optionally, `atlassian.timeout` sets the seconds to wait for each answer (default 30).

Store an API token in the OS keychain (the value is read from a hidden prompt, never the command line):

```bash
hz system credential set atlassian-token
```

Create it at [id.atlassian.com → Security → API tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
with **Create API token** — the one *without* scopes. The adapter sends it with your email as Basic auth to your site,
which is what tokens without scopes support; a token created with scopes only works through Atlassian's
`api.atlassian.com` gateway, which this adapter doesn't use yet ([ADR 0034](../../docs/adr/0034-atlassian-cloud-rest-apis-over-urllib.md)).
A token without scopes can do whatever your account can, so the adapter's read-only use is what limits it: it only
sends GET requests to two read endpoints. For the least access, create the token for an account that can browse just
the Jira projects and Confluence spaces your workflows read. On machines without a keychain, export
`HZ_CREDENTIAL_ATLASSIAN_ATLASSIAN_TOKEN` instead.

There is no anonymous mode: without the site, the email or the token, `hz system doctor` reports the adapter as
degraded with the commands above, and requests fail before anything is sent.

## Use

Workflows ask for `tracker` and `documents`, never for Jira or Confluence:

```python
from datetime import datetime

from heinzel import Document, Documents, Issue, Module, Tracker, Window

module = Module("digest", "Weekly digest.", requires=["tracker:read", "documents:read"])


@module.operation
def updated(tracker: Tracker, since: datetime, until: datetime) -> list[Issue]:
    """Issues updated this week."""
    return tracker.issues_updated("WID", Window(since=since, until=until))


@module.operation
def plan(documents: Documents) -> Document:
    """The plan, as Markdown."""
    return documents.read("https://acme.atlassian.net/wiki/spaces/WID/pages/123456/Widget+plan")
```

| Method | Capability | Atlassian endpoint |
| --- | --- | --- |
| `issues_updated(project, window)` | `tracker:read` | Jira Cloud REST v3 `GET /rest/api/3/search/jql` with `project = "WID" AND updated >= <ms> AND updated < <ms> ORDER BY updated ASC, key ASC`, paged by `nextPageToken` |
| `read(ref)` | `documents:read` | Confluence Cloud REST v2 `GET /wiki/api/v2/pages/{id}?body-format=storage` |

- **Projects** are Jira project keys such as `WID` (lower case is upper-cased); anything else is refused before a
  request, so no input reaches JQL unchecked. Windows are half-open `[since, until)` with timezone-aware times, sent as
  epoch milliseconds widened to whole minutes, which Jira compares as instants whatever your profile's time zone;
  the exact window is applied to the results, which come oldest first, each issue once.
  `state` is the status name, `assignee` the display name, `labels` the issue's labels.
- **Documents** are a page id (`123456`) or a page URL on your site: `/wiki/spaces/<space>/pages/<id>/...` or
  `/wiki/pages/viewpage.action?pageId=<id>`. URLs on any other host are refused before a request; short links
  (`/wiki/x/...`) need the full URL. The page's storage format comes back as Markdown-flavoured text (headings,
  lists, tasks, links, tables, code blocks); images and macro settings are left out, image captions stay. `revision` is the page version.

Why these endpoints and not others: [ADR 0034](../../docs/adr/0034-atlassian-cloud-rest-apis-over-urllib.md).

```bash
hz atlassian info               # this plugin, its version, the site and the APIs it uses
hz system capability list       # who reads what, through which adapter and hosts
hz system doctor                # site, email, token and bindings; never contacts Atlassian
```

Errors say what to do: a rejected email or token names `hz system credential set atlassian-token` and
`atlassian.email`; a 403 names the permission (Browse projects in Jira, View on the space in Confluence); a missing
project or page says the account may lack access; a rate limit says when to retry (the adapter doesn't wait or retry
on its own). The token and the Basic credential never appear in output, errors or logs, and requests never follow a
redirect to another host.
