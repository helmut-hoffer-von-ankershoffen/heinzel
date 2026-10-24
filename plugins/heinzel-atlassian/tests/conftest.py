"""Shared fixtures: a fake Atlassian site behind the plugin's `Transport`, and kernels that use it. Nothing goes online.

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
from heinzel_atlassian import Response, Transport

from heinzel import Config, Kernel, Module, Settings
from heinzel.kernel import ModuleRecord, Origin, discover

SITE = "https://acme.atlassian.net"
EMAIL = "ada@acme.example"
TOKEN = "fake-token-test-token-never-shown"
SEARCH = "/rest/api/3/search/jql"
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
class FakeAtlassian(Transport):
    """Jira's search and Confluence's pages in memory: serves answers per path and page token, records requests."""

    site: str = SITE
    routes: dict[tuple[str, str | None], Response] = field(default_factory=dict)
    requests: list[Request] = field(default_factory=list)
    error: OSError | None = None

    def get(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        request = Request(url, dict(headers), timeout)
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        response = self.routes.get((request.path, request.query.get("nextPageToken")))
        assert response is not None, f"unexpected request to {url}"
        return response

    def answer(self, path: str, body: object, token: str | None = None) -> None:
        """Answer GETs of `path` (with page token `token`) with `body` as JSON."""
        self.routes[path, token] = Response(200, {"content-type": "application/json"}, json.dumps(body).encode())

    def search(self, *pages: list[dict[str, Any]], is_last: bool | None = None) -> None:
        """Answer Jira searches with `pages` of issues, chained by `nextPageToken` as Jira does."""
        for number, issues in enumerate(pages):
            body: dict[str, Any] = {"issues": issues}
            if number + 1 < len(pages):
                body["nextPageToken"] = f"token-{number + 1}"
            if is_last is not None:
                body["isLast"] = is_last
            self.answer(SEARCH, body, f"token-{number}" if number else None)

    def page(self, page_id: str, title: str = "Plan", storage: str = "<p>Hello</p>", version: int = 7) -> None:
        """Answer a Confluence v2 page by id, trimmed to what matters plus some noise."""
        self.answer(
            f"/wiki/api/v2/pages/{page_id}",
            {
                "id": page_id,
                "status": "current",
                "title": title,
                "spaceId": "98304",
                "version": {"number": version, "message": "", "minorEdit": False},
                "body": {"storage": {"representation": "storage", "value": storage}},
                "_links": {"webui": f"/spaces/WID/pages/{page_id}/{title}", "base": "https://elsewhere.example/wiki"},
            },
        )

    def fail(self, path: str, status: int, body: object = None, headers: dict[str, str] | None = None) -> None:
        """Answer GETs of `path` with an error status."""
        self.routes[path, None] = Response(status, headers or {}, json.dumps(body or {}).encode())

    @staticmethod
    def issue(  # noqa: PLR0913 - the fields tests vary
        number: int,
        *,
        updated: str,
        status: str = "To Do",
        assignee: str | None = None,
        labels: tuple[str, ...] = (),
        project: str = "WID",
    ) -> dict[str, Any]:
        """An issue as the enhanced search returns it with `fields=summary,status,updated,assignee,labels`."""
        return {
            "expand": "renderedFields",
            "id": str(10000 + number),
            "self": f"https://elsewhere.example/rest/api/3/issue/{10000 + number}",
            "key": f"{project}-{number}",
            "fields": {
                "summary": f"Issue {number}",
                "status": {"name": status, "statusCategory": {"key": "new"}},
                "updated": updated,
                "assignee": {"accountId": "5b10ac8d82e05b22cc7d4ef5", "displayName": assignee} if assignee else None,
                "labels": list(labels),
            },
        }


@pytest.fixture
def fake_atlassian() -> FakeAtlassian:
    """A fresh fake Atlassian site."""
    return FakeAtlassian()


@pytest.fixture
def boot() -> Callable[..., Kernel]:
    """Boot a kernel the way the `hz` command does: Heinzelmännchen's built-in modules plus every installed plugin."""

    def make(**settings: object) -> Kernel:
        records, errors = discover()
        return Kernel(Settings(**settings), records, errors)

    return make


@pytest.fixture
def atlassian_kernel(fake_atlassian: FakeAtlassian) -> Callable[..., Kernel]:
    """A kernel with every installed plugin, extra test-only modules, the given configuration and the fake site."""

    def make(config: Config | None = None, **modules: Module) -> Kernel:
        records, errors = discover()
        records += [ModuleRecord(qualname=name, module=module, origin=TEST_ORIGIN) for name, module in modules.items()]
        kernel = Kernel(Settings(), records, errors, config or Config.empty())
        kernel.bind(Transport, fake_atlassian)
        return kernel

    return make
