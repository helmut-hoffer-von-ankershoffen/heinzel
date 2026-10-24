"""Shared fixtures: a fake GitHub behind the plugin's `Transport`, and kernels that use it. Nothing goes online.

heinzel.testing (a pytest plugin, loaded automatically) brings the rest: every test is exactly one of unit,
integration or e2e and gets its level's timeout (docs/CODING_STANDARDS.md#tests), and every test has its own
HZ_CONFIG_HOME, HZ_DATA_DIR, HZ_CACHE_DIR, HZ_STATE_DIR, HZ_LOG_DIR and daemon unit directory, without the developer's
HZ_* variables; credentials come from HZ_CREDENTIAL_* variables, never the keychain.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from heinzel_github import GITHUB_API, Response, Transport

from heinzel import Config, Kernel, Module, Settings
from heinzel.kernel import ModuleRecord, Origin, discover

TOKEN = "ghp_test-token-never-shown"
TEST_ORIGIN = Origin(distribution="heinzel-test", version="0", builtin=False)


@dataclass
class Request:
    """A request the fake received."""

    url: str
    headers: dict[str, str]
    timeout: float

    @property
    def path(self) -> str:
        return urlsplit(self.url).path

    @property
    def query(self) -> dict[str, str]:
        return {key: values[0] for key, values in parse_qs(urlsplit(self.url).query).items()}


@dataclass
class FakeGitHub(Transport):
    """GitHub's REST API in memory: serves pages per path, with `Link` headers, and records every request."""

    api: str = GITHUB_API
    routes: dict[str, list[Response]] = field(default_factory=dict)
    requests: list[Request] = field(default_factory=list)
    error: OSError | None = None

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        request = Request(url, dict(headers), timeout)
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        pages = self.routes.get(request.path)
        assert pages is not None, f"unexpected request to {url}"
        return pages[int(request.query.get("page", "1")) - 1]

    def serve(self, path: str, *pages: list[dict[str, Any]]) -> None:
        """Answer GETs of `path` with `pages`, linking each to the next as GitHub does."""
        responses = []
        for number, items in enumerate(pages, start=1):
            headers = {"content-type": "application/json"}
            if number < len(pages):
                headers["link"] = f'<{self.api}{path}?per_page=100&page={number + 1}>; rel="next", <x>; rel="last"'
            responses.append(Response(200, headers, json.dumps(items).encode()))
        self.routes[path] = responses

    def fail(self, path: str, status: int, body: object = None, headers: dict[str, str] | None = None) -> None:
        """Answer GETs of `path` with an error status."""
        self.routes[path] = [Response(status, headers or {}, json.dumps(body or {}).encode())]

    @staticmethod
    def pull(  # noqa: PLR0913 - the fields tests vary
        number: int,
        *,
        updated_at: str,
        merged_at: str | None,
        title: str = "",
        user: str | None = "ada",
        labels: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """A pull request as the pulls list returns it, trimmed to what matters plus some noise."""
        return {
            "number": number,
            "title": title or f"Pull {number}",
            "html_url": f"https://github.com/acme/widgets/pull/{number}",
            "state": "closed",
            "user": {"login": user, "id": 1} if user else None,
            "labels": [{"id": i, "name": name, "color": "ededed"} for i, name in enumerate(labels)],
            "created_at": "2026-09-01T00:00:00Z",
            "updated_at": updated_at,
            "closed_at": merged_at or updated_at,
            "merged_at": merged_at,
            "base": {"repo": {"full_name": "acme/widgets"}},
        }

    @staticmethod
    def issue(  # noqa: PLR0913 - the fields tests vary
        number: int,
        *,
        updated_at: str,
        state: str = "open",
        assignee: str | None = None,
        labels: tuple[str, ...] = (),
        pull_request: bool = False,
    ) -> dict[str, Any]:
        """An issue as the issues list returns it; with `pull_request`, the pull request GitHub mixes in."""
        item: dict[str, Any] = {
            "number": number,
            "title": f"Issue {number}",
            "html_url": f"https://github.com/acme/widgets/issues/{number}",
            "state": state,
            "assignee": {"login": assignee} if assignee else None,
            "labels": [{"name": name} for name in labels],
            "updated_at": updated_at,
        }
        if pull_request:
            item["pull_request"] = {"url": f"{GITHUB_API}/repos/acme/widgets/pulls/{number}"}
        return item


@pytest.fixture
def fake_github() -> FakeGitHub:
    """A fresh fake GitHub."""
    return FakeGitHub()


@pytest.fixture
def boot() -> Callable[..., Kernel]:
    """Boot a kernel the way the `hz` command does: Heinzelmännchen's built-in modules plus every installed plugin."""

    def make(**settings: object) -> Kernel:
        records, errors = discover()
        return Kernel(Settings(**settings), records, errors)

    return make


@pytest.fixture
def github_kernel(fake_github: FakeGitHub) -> Callable[..., Kernel]:
    """A kernel with every installed plugin, extra test-only modules, the given configuration and the fake GitHub."""

    def make(config: Config | None = None, **modules: Module) -> Kernel:
        records, errors = discover()
        records += [ModuleRecord(qualname=name, module=module, origin=TEST_ORIGIN) for name, module in modules.items()]
        kernel = Kernel(Settings(), records, errors, config or Config.empty())
        kernel.bind(Transport, fake_github)
        return kernel

    return make
