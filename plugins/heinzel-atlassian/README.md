# heinzel-atlassian

Read Jira issues and Confluence pages through Atlassian's CLI `acli`, the `tracker` and `documents` capabilities
([ADR 0028](../../docs/adr/0028-integrations-are-capability-adapters.md)): a first-party
[Heinzelmännchen](../../README.md) plugin. It is released with the core, at the same version, and not installed by
default:

```bash
hz system plugin add atlassian             # the release matching your hz; run it again to upgrade
hz system plugin remove heinzel-atlassian
```

## Set up

The plugin does only what you could do yourself: every read runs [acli](https://developer.atlassian.com/cloud/acli/),
logged in as you ([ADR 0035](../../docs/adr/0035-integrations-go-through-vendor-clis-as-an-ordinary-employee.md),
proposed). It stores no token and needs no app an administrator registers. Install acli 1.3.39 or later
([guide](https://developer.atlassian.com/cloud/acli/guides/install-acli/)) and log in to both products, in the
browser or with an API token without scopes:

```bash
acli jira auth login --web
acli confluence auth login --web
```

Name the site acli is logged in to, and bind the adapter, in your user configuration (never a project `hz.toml`,
which Heinzelmännchen ignores for both):

```bash
hz system config set atlassian.site_url https://acme.atlassian.net
hz system config set capabilities.tracker atlassian
hz system config set capabilities.documents atlassian
```

which together write

```toml
[atlassian]
site_url = "https://acme.atlassian.net"

[capabilities]
tracker = "atlassian"
documents = "atlassian"
```

Or per shell: `HZ_ATLASSIAN__SITE_URL`, `HZ_CAPABILITIES__TRACKER=atlassian`, `HZ_CAPABILITIES__DOCUMENTS=atlassian`.
`site_url` must be `https://<site>.atlassian.net` (a pasted `.../wiki` is trimmed). The plugin builds issue and page
links on it and accepts page URLs only on it. acli decides which site it reads: an answer that names another site
(an issue's `self`, a page's `_links.base`) is refused with `acli jira auth switch` and the setting to change; one
through Atlassian's gateway after a browser login names no site and is taken as it is. Optionally,
`atlassian.timeout` sets the seconds each acli command may take (default 60), and `atlassian.acli` the program (a name
looked up on absolute `PATH` entries, or an absolute path; default `acli`). A project `hz.toml` can set none of the
three.

Upgrading from the release that stored an API token: the plugin no longer reads it. Remove it with your keychain's
own tool (on macOS: `security delete-generic-password -s heinzel/atlassian -a atlassian-token`), and drop
`atlassian.email` from your configuration.

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

| Method | Capability | acli command |
| --- | --- | --- |
| `issues_updated(project, window)` | `tracker:read` | `acli jira workitem search --jql 'project = "WID" AND updated >= <ms> AND updated < <ms> ORDER BY updated ASC, key ASC' --fields key,summary,status,updated,assignee,labels --paginate --json` |
| `read(ref)` | `documents:read` | `acli confluence page view --id <id> --body-format storage --json` |

- **Projects** are Jira project keys such as `WID` (lower case is upper-cased); anything else is refused before acli
  runs, so no input reaches JQL unchecked. Windows are half-open `[since, until)` with timezone-aware times, sent as
  epoch milliseconds widened to whole minutes, which Jira compares as instants whatever your profile's time zone;
  the exact window is applied to the results, which come oldest first, each issue once. More than 1,000 issues in
  one window is refused with a request for a shorter one. `state` is the status name, `assignee` the display name,
  `labels` the issue's labels.
- **Documents** are a page id (`123456`) or a page URL on your site: `/wiki/spaces/<space>/pages/<id>/...` or
  `/wiki/pages/viewpage.action?pageId=<id>`. URLs on any other host are refused before acli runs; short links
  (`/wiki/x/...`) need the full URL. The page's storage format comes back as Markdown-flavoured text (headings,
  lists, tasks, links, tables, code blocks); images and macro settings are left out, image captions stay.
  `revision` is the page version.
- **Not available through acli**: a search for pages updated in a window (acli 1.3 has `page view` but no page
  search or CQL), so the `documents` capability stays `read(ref)` only, which is all it defines today.

```bash
hz atlassian info               # this plugin, its version, the site, acli's path and the commands it runs
hz system capability list       # who reads what, through which adapter and hosts
hz system doctor                # acli and its version, the site and bindings; runs only `acli --version`, offline
```

Doctor doesn't check whether acli is logged in, because asking could reach Atlassian; the first read tells. Errors say
what to do: acli missing names its install and `acli jira auth login`; acli older than 1.3.39 names the upgrade; acli
logged out (`unauthorized`) names `acli jira auth login` or `acli confluence auth login`; a refusal names the
permission (Browse projects in Jira, View on the space in Confluence); a missing project or page says the account may
lack access; a rate limit says to retry later.

acli runs with an argument list (never a shell), no standard input, the home directory as working directory, an
allowlisted environment (`PATH`, `HOME`, locale, proxy and `ACLI_*` variables; never `HZ_CREDENTIAL_*`), in its own
process group under a deadline (SIGTERM, then SIGKILL for it and anything it started), with standard output capped at
10 MiB and the last 64 KiB of standard error kept. Its error text is cleaned of escape sequences and controls,
redacted of anything shaped like a token and only then shortened.

The hosts `hz system capability list` shows are your site and `api.atlassian.com`, the gateway acli uses after a
browser login. acli is Atlassian's program with its own settings; what it sends besides the reads is Atlassian's
to document, not this plugin's.

The shapes of acli's `--json` output are assumed from Atlassian's REST APIs (issue objects, the Confluence v2 page),
not yet checked against a logged-in acli; the samples in `tests/fixtures` follow that assumption.
