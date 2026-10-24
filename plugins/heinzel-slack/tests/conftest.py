"""Shared fixtures: a fake Slack behind the plugin's `Transport`, and kernels that use it. Nothing goes online.

heinzel.testing (a pytest plugin, loaded automatically) brings the rest: every test is exactly one of unit,
integration or e2e and gets its level's timeout (docs/CODING_STANDARDS.md#tests), and every test has its own
HZ_CONFIG_HOME, HZ_DATA_DIR, HZ_CACHE_DIR, HZ_STATE_DIR, HZ_LOG_DIR and daemon unit directory, without the developer's
HZ_* variables; credentials come from HZ_CREDENTIAL_* variables, never the keychain.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from heinzel_slack import SLACK_API, Response, Transport

from heinzel import Config, Kernel, Module, Settings
from heinzel.kernel import ModuleRecord, Origin, discover

TOKEN = "xoxp-test-token-never-shown"
CHANNEL = "C0123456789"
TEST_ORIGIN = Origin(distribution="heinzel-test", version="0", builtin=False)


@dataclass
class Request:
    """A request the fake received."""

    url: str
    headers: dict[str, str]
    body: bytes | None
    timeout: float

    @property
    def method(self) -> str:
        return urlsplit(self.url).path.rsplit("/", 1)[-1]

    @property
    def query(self) -> dict[str, str]:
        return {key: values[0] for key, values in parse_qs(urlsplit(self.url).query).items()}

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.body or b"{}")


def ok(**data: object) -> Response:
    """An `ok: true` answer."""
    return Response(200, {"content-type": "application/json"}, json.dumps({"ok": True, **data}).encode())


def error(code: str, status: int = 200, headers: dict[str, str] | None = None, **data: object) -> Response:
    """An `ok: false` answer with Slack's error `code`."""
    return Response(status, headers or {}, json.dumps({"ok": False, "error": code, **data}).encode())


def message(ts: str, text: str = "", user: str | None = "U0ADA", **fields: object) -> dict[str, Any]:
    """A message as conversations.history and conversations.replies return it."""
    item: dict[str, Any] = {"type": "message", "ts": ts, "text": text or f"message {ts}"}
    if user:
        item["user"] = user
    item.update(fields)
    return item


@dataclass
class FakeSlack(Transport):
    """Slack's Web API in memory: one workspace with channels, people, history and threads; records every request.

    `queued` answers, by method, come before the simulated ones; `page` is the page size, so tests page quickly.
    """

    api: str = SLACK_API
    channels: list[dict[str, Any]] = field(
        default_factory=lambda: [{"id": "C0OTHER0000", "name": "random"}, {"id": CHANNEL, "name": "widgets"}]
    )
    users: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            "U0ADA": {"id": "U0ADA", "name": "ada", "profile": {"display_name": "Ada", "email": "ada@acme.example"}},
            "U0GRACE": {"id": "U0GRACE", "name": "grace", "real_name": "Grace H.", "profile": {"display_name": ""}},
        }
    )
    history: list[dict[str, Any]] = field(default_factory=list)
    threads: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    workspace: str = "https://acme.slack.com/"
    page: int = 2
    queued: dict[str, list[Response]] = field(default_factory=dict)
    requests: list[Request] = field(default_factory=list)
    slept: list[float] = field(default_factory=list)
    unreachable: OSError | None = None
    posted: list[dict[str, Any]] = field(default_factory=list)

    def send(self, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float) -> Response:
        request = Request(url, dict(headers), body, timeout)
        self.requests.append(request)
        if self.unreachable is not None:
            raise self.unreachable
        assert url.startswith(f"{self.api}/"), f"request away from the API: {url}"
        if self.queued.get(request.method):
            return self.queued[request.method].pop(0)
        handler: Callable[[Request], Response] | None = getattr(self, "_" + request.method.replace(".", "_"), None)
        assert handler is not None, f"unexpected request to {url}"
        return handler(request)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def queue(self, method: str, *answers: Response) -> None:
        """Answer the next requests of `method` with `answers`, in order."""
        self.queued.setdefault(method, []).extend(answers)

    def methods(self) -> list[str]:
        """The methods requested, in order."""
        return [r.method for r in self.requests]

    def _paged(self, request: Request, key: str, items: list[dict[str, Any]], **extra: object) -> Response:
        start = int(request.query.get("cursor", "0"))
        chunk = items[start : start + self.page]
        more = start + self.page < len(items)
        metadata = {"next_cursor": str(start + self.page) if more else ""}
        return ok(**{key: chunk, "has_more": more, "response_metadata": metadata, **extra})

    @staticmethod
    def _within(request: Request, ts: str) -> bool:
        oldest, latest = Decimal(request.query.get("oldest", "0")), Decimal(request.query.get("latest", "9e12"))
        return oldest <= Decimal(ts) <= latest  # inclusive=true, as the adapter asks

    def _auth_test(self, request: Request) -> Response:
        return ok(url=self.workspace, team="Acme", user="ada", user_id="U0ADA")

    def _conversations_list(self, request: Request) -> Response:
        return self._paged(request, "channels", self.channels)

    def _conversations_history(self, request: Request) -> Response:
        assert request.query["channel"] == CHANNEL
        items = sorted((m for m in self.history if self._within(request, m["ts"])), key=lambda m: -Decimal(m["ts"]))
        return self._paged(request, "messages", items)

    def _conversations_replies(self, request: Request) -> Response:
        parent = request.query["ts"]
        thread = self.threads.get(parent)
        if thread is None:
            return error("thread_not_found")
        head = next(m for m in self.history if m["ts"] == parent)
        replies = [m for m in thread if self._within(request, m["ts"])]
        return self._paged(request, "messages", [head, *replies])  # Slack answers the parent first

    def _users_info(self, request: Request) -> Response:
        user = self.users.get(request.query["user"])
        return ok(user=user) if user else error("user_not_found")

    def _chat_postMessage(self, request: Request) -> Response:  # noqa: N802 - Slack's method name
        payload = request.payload
        self.posted.append(payload)
        ts = f"1761000000.{len(self.posted):06d}"
        return ok(channel=payload["channel"], ts=ts, message={"text": payload["text"]})

    ok = staticmethod(ok)
    error = staticmethod(error)
    message = staticmethod(message)

    def thread(self, parent: str, *replies: dict[str, Any]) -> None:
        """Make the history message `parent` start a thread with `replies`."""
        head = next(m for m in self.history if m["ts"] == parent)
        head.update(thread_ts=parent, reply_count=len(replies), latest_reply=max(r["ts"] for r in replies))
        for reply in replies:
            reply["thread_ts"] = parent
        self.threads[parent] = list(replies)


@pytest.fixture
def fake_slack() -> FakeSlack:
    """A fresh fake Slack."""
    return FakeSlack()


@pytest.fixture
def boot() -> Callable[..., Kernel]:
    """Boot a kernel the way the `hz` command does: Heinzelmännchen's built-in modules plus every installed plugin."""

    def make(**settings: object) -> Kernel:
        records, errors = discover()
        return Kernel(Settings(**settings), records, errors)

    return make


@pytest.fixture
def slack_kernel(fake_slack: FakeSlack) -> Callable[..., Kernel]:
    """A kernel with every installed plugin, extra test-only modules, the given configuration and the fake Slack."""

    def make(config: Config | None = None, **modules: Module) -> Kernel:
        records, errors = discover()
        records += [ModuleRecord(qualname=name, module=module, origin=TEST_ORIGIN) for name, module in modules.items()]
        kernel = Kernel(Settings(), records, errors, config or Config.empty())
        kernel.bind(Transport, fake_slack)
        return kernel

    return make
