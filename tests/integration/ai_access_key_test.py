"""The Model API key typed in the web app goes only to the credentials store, never back out (owner 2026-11-08).

It travels in the paired web app's strict JSON body, is kept in the credential backend, is never in a response, a log
record, a configuration file or the OpenAPI document as a value, and can be replaced and removed.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from heinzel import CredentialLet, Credentials
from heinzel.heads import api
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.integration

SECRET = "fixture-model-api-key"  # made up and low-entropy, never a real key
HEADERS = {"Authorization": "Bearer fixture-session"}


@pytest.fixture
def web(make_kernel):
    kernel = make_kernel(builtins=True)
    backend = MemoryBackend()
    kernel.bind(Credentials, Credentials(backend, [(r.qualname, let) for r, let in kernel.lets(CredentialLet)]))
    client = TestClient(api.build(kernel, head="web", token="fixture-session"), base_url="http://localhost")
    return client, backend


def test_a_typed_key_is_kept_masked_replaced_and_removed_and_never_leaves(web, caplog, tmp_path):
    client, backend = web
    with caplog.at_level(logging.DEBUG):
        assert client.get("/ai/access/key", headers=HEADERS).json() == {"stored": False}
        stored = client.post("/ai/access/set-key", headers=HEADERS, json={"value": SECRET})
        assert stored.status_code == 200, stored.text
        assert stored.json() == {"stored": True}
        assert backend.get("ai.access", "api-key") == SECRET
        assert client.get("/ai/access/key", headers=HEADERS).json() == {"stored": True}
        listing = client.get("/ai/access/list", headers=HEADERS).text
        replaced = client.post("/ai/access/set-key", headers=HEADERS, json={"value": SECRET + "-new"})
        assert backend.get("ai.access", "api-key") == SECRET + "-new"
        removed = client.post("/ai/access/remove-key", headers=HEADERS)
        assert removed.json() == {"stored": False}
        assert backend.get("ai.access", "api-key") is None
    for text in (stored.text, listing, replaced.text, removed.text):
        assert SECRET not in text
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert SECRET not in logged
    events = [(r.event, r.outcome) for r in caplog.records if getattr(r, "event", "").startswith("ai.access.key")]
    assert events == [("ai.access.key.set", "ok"), ("ai.access.key.set", "ok"), ("ai.access.key.remove", "ok")]
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert SECRET not in path.read_text(errors="ignore")


def test_the_key_is_never_taken_from_a_query_or_by_an_unpaired_caller(web):
    client, backend = web
    assert client.post("/ai/access/set-key", json={"value": SECRET}).status_code == 401
    query = client.post("/ai/access/set-key", headers=HEADERS, params={"value": SECRET})
    assert query.status_code == 422
    assert SECRET not in query.text
    assert backend.get("ai.access", "api-key") is None


@pytest.mark.parametrize(
    "body",
    [{"value": SECRET * 200}, {"value": [SECRET]}, {"value": {"key": SECRET}}, {"value": SECRET, "extra": SECRET}],
    ids=["too-long", "list", "object", "extra-field"],
)
def test_a_refused_body_never_echoes_the_key(web, caplog, body):
    client, backend = web
    with caplog.at_level(logging.DEBUG):
        refused = client.post("/ai/access/set-key", headers=HEADERS, json=body)
    assert refused.status_code == 422
    assert refused.json()["detail"]
    assert SECRET not in refused.text
    assert SECRET not in "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert backend.get("ai.access", "api-key") is None


def test_a_backend_that_refuses_logs_the_failure_without_the_key(make_kernel, caplog):
    class Refusing(MemoryBackend):
        def set(self, service: str, name: str, value: str) -> None:
            message = f"keychain locked for {value}"  # a careless backend that echoes the value
            raise OSError(message)

    kernel = make_kernel(builtins=True)
    kernel.bind(Credentials, Credentials(Refusing(), [(r.qualname, let) for r, let in kernel.lets(CredentialLet)]))
    client = TestClient(api.build(kernel, head="web", token="fixture-session"), base_url="http://localhost")
    with caplog.at_level(logging.DEBUG):
        failed = client.post("/ai/access/set-key", headers=HEADERS, json={"value": SECRET})
    assert failed.status_code == 400
    assert SECRET not in failed.text
    assert SECRET not in "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    events = [(r.event, r.outcome) for r in caplog.records if getattr(r, "event", "").startswith("ai.access.key")]
    assert events == [("ai.access.key.set", "error")]
