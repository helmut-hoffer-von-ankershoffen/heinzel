# heinzel-github

Read pull requests and issues from GitHub, the `code-host` and `tracker` capabilities ([ADR 0028](../../docs/adr/0028-integrations-are-capability-adapters.md)): a first-party
[Heinzelmännchen](../../README.md) plugin. It is released with the core, at the same version, and not installed by default:

```bash
hz system plugin add github             # the release matching your hz; run it again to upgrade
hz system plugin remove heinzel-github
```

## Set up

Bind the adapter in your user configuration, never in a project `hz.toml`, which Heinzelmännchen ignores for bindings:

```bash
hz system config set capabilities.code_host github
hz system config set capabilities.tracker github
```

which writes

```toml
[capabilities]
code_host = "github"
tracker = "github"
```

Or per shell: `HZ_CAPABILITIES__CODE_HOST=github`, `HZ_CAPABILITIES__TRACKER=github`.

Store a token in the OS keychain (the value is read from a hidden prompt, never the command line):

```bash
hz system credential set github-token --service github
```

Create a [fine-grained personal access token](https://github.com/settings/personal-access-tokens) with access to the
repositories you need and these repository permissions, all **read-only**: **Metadata**, **Pull requests**, **Issues**.
Without a token the adapter works anonymously for public repositories, at GitHub's 60 requests an hour;
`hz system doctor` says so. On machines without a keychain, export `HZ_CREDENTIAL_GITHUB_GITHUB_TOKEN` instead.

For GitHub Enterprise Server, point the adapter at its API, in your user configuration or `HZ_GITHUB__API_URL`:

```toml
[github]
api_url = "https://ghe.example.com/api/v3"
timeout = 30   # seconds per request
```

`api_url` is ignored in a project `hz.toml`: the token goes wherever it points. The adapter's hosts come from it, so
`hz system capability list` shows `api.github.com`, or the host of your GitHub Enterprise Server.

## Use

Workflows ask for `code-host` and `tracker`, never for GitHub:

```python
from datetime import datetime

from heinzel import CodeHost, Module, PullRequest, Window

module = Module("digest", "Weekly digest.", requires=["code-host:read", "tracker:read"])


@module.operation
def merged(code_host: CodeHost, since: datetime, until: datetime) -> list[PullRequest]:
    """Pull requests merged this week."""
    return code_host.merged_pull_requests("acme/widgets", Window(since=since, until=until))
```

| Method | Capability | GitHub endpoint |
| --- | --- | --- |
| `merged_pull_requests(repository, window)` | `code-host:read` | `GET /repos/{owner}/{repo}/pulls?state=closed&sort=updated&direction=desc`, kept when `merged_at` is in the window; paging stops before the window |
| `issues_updated(project, window)` | `tracker:read` | `GET /repos/{owner}/{repo}/issues?state=all&since=...&sort=updated&direction=asc`, without pull requests |

Repositories and projects are written `owner/repo`; windows are half-open `[since, until)` with timezone-aware times;
results are oldest first. A call follows at most 100 pages (10,000 items), then fails and says what to ask for
instead: merged pull requests page through every pull request updated since `since`, even after `until`, so they need
a window that starts more recently; issues stop at `until`, so they need a shorter window. Why the pulls list and not
the search API: [ADR 0033](../../docs/adr/0033-github-rest-api-over-urllib.md).

```bash
hz github info                  # this plugin, its version and the GitHub API it uses
hz system capability list       # who reads what, through which adapter and hosts
hz system doctor                # bindings, the token's presence; never contacts GitHub
```

Errors say what to do: a rejected token or an exhausted rate limit (with the time it resets, or when to retry) names
`hz system credential set github-token --service github`; a missing or forbidden repository says whether a token or
access is lacking; a network error names the underlying error. Tokens never appear in output, errors or logs: a stored
token is stripped of surrounding whitespace and refused, without being shown, unless it is one line of printable ASCII.
Requests never follow redirects or next-page links to another host, and a refused redirect says so.
