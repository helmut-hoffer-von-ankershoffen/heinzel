"""The `pydantic-ai` AI access option against a fake OpenAI-compatible server on this machine (ADR 0057).

A real turn through Pydantic AI and OpenAI's client, as with Ollama, LM Studio or vLLM; the server is a thread in this
test that lists one model and answers one chat completion. No test reaches a real model.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from heinzel import Config, Kernel
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": "tiny",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10},
}


class Server(BaseHTTPRequestHandler):
    """Lists `tiny` and answers every chat completion with OK; records each request's path and headers."""

    seen: ClassVar[list[tuple[str, str, dict[str, str]]]] = []

    def _answer(self, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        """The models listing; under /moved, a redirect elsewhere."""
        Server.seen.append(("GET", self.path, dict(self.headers)))
        if self.path.startswith("/moved"):
            self.send_response(302)
            self.send_header("Location", "http://198.51.100.1/v1/models")
            self.end_headers()
            return
        self._answer({"object": "list", "data": [{"id": "tiny", "object": "model"}]})

    def do_POST(self) -> None:
        """One chat completion."""
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        Server.seen.append(("POST", self.path, dict(self.headers)))
        self._answer(COMPLETION)

    def log_message(self, *args: object) -> None:
        del args


@pytest.fixture
def endpoint() -> Iterator[str]:
    Server.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    server.server_close()


def hz(kernel: Kernel, *args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    assert result.output.startswith("{"), result.output
    return result.exit_code, json.loads(result.output)


def test_a_local_openai_compatible_endpoint_answers_a_test_and_lists_its_models(make_kernel, endpoint):
    config = Config(files=(), data={"ai": {"access": {"model": "tiny", "base_url": endpoint}}})
    kernel = make_kernel(builtins=True, config=config)
    code, report = hz(kernel, "ai", "access", "test", "--name", "pydantic-ai")
    assert code == 0
    assert (report["ok"], report["detail"]) == (True, "OK; 1 models listed")
    assert [(method, path) for method, path, _ in Server.seen] == [
        ("GET", "/v1/models"),
        ("POST", "/v1/chat/completions"),
    ]
    keys = [{k.lower(): v for k, v in headers.items()}.get("authorization") for *_, headers in Server.seen]
    assert keys == [None, "Bearer none"]  # no key configured: the listing sends none, OpenAI's client a placeholder
    _, listing = hz(kernel, "ai", "access", "list")
    [option] = [o for o in listing["options"] if o["name"] == "pydantic-ai"]
    assert (option["status"], option["models"], option["hosts"]) == ("tested", ["tiny"], ["127.0.0.1"])


def test_a_listing_follows_no_redirect(make_kernel, endpoint, monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-local")
    moved = endpoint.replace("/v1", "/moved/v1")
    config = Config(files=(), data={"ai": {"access": {"model": "tiny", "base_url": moved}}})
    _, report = hz(make_kernel(builtins=True, config=config), "ai", "access", "test", "--name", "pydantic-ai")
    assert "models not listed" in report["detail"]
    assert "sk-local" not in report["detail"]
    assert [path for method, path, _ in Server.seen if method == "GET"] == ["/moved/v1/models"]  # nothing followed
