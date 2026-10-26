"""A fixed local endpoint and conditional version protect unrelated Serve state without leaking credentials."""

import base64
import json

import httpx
import pytest
from heinzel_tailscale import _localapi
from heinzel_tailscale._cli import Result, TailscaleCli
from heinzel_tailscale._localapi import Connection, ServeStore, Snapshot

from heinzel import HeinzelError

pytestmark = pytest.mark.unit
CONNECTION = Connection("http://127.0.0.1:41112", password="fixture-local-secret")


def test_client_credential_example_is_parsed_as_data_without_logging_or_execution():
    connection = Connection.parse(b"curl -u:fixture-local-secret http://localhost:41112/localapi/v0/status\n")
    assert connection == CONNECTION
    assert "fixture-local-secret" not in repr(connection)
    socket = Connection.parse(
        b"curl --unix-socket /var/run/tailscale/tailscaled.sock http://local-tailscaled.sock/localapi/v0/status\n"
    )
    assert socket.socket == "/var/run/tailscale/tailscaled.sock"
    assert socket.password is None


@pytest.mark.parametrize(
    "example",
    [
        b"curl -u:secret https://evil.example/localapi/v0/status",
        b"curl -u:secret http://localhost:0/localapi/v0/status",
        b"curl -u:secret http://localhost:65536/localapi/v0/status",
        b"curl -u:secret http://localhost:1234/unrelated",
        b"curl --unix-socket relative.sock http://local-tailscaled.sock/localapi/v0/status",
        b"curl --unix-socket /tmp/bad\x00socket http://local-tailscaled.sock/localapi/v0/status",
        b"curl -u:secret http://localhost:41112/localapi/v0/status ; unexpected",
        b"curl -u:secret http://localhost:41112/localapi/v0/status\nother-command",
        b"curl --header Authorization:secret http://localhost:41112/localapi/v0/status",
    ],
)
def test_unknown_remote_or_shell_extended_credential_formats_are_refused(example):
    with pytest.raises(HeinzelError):
        Connection.parse(example)


def test_exact_loopback_request_keeps_basic_credential_out_of_url_and_uses_if_match():
    seen = []

    def handle(request):
        seen.append(request)
        assert str(request.url) == "http://127.0.0.1:41112/localapi/v0/serve-config"
        expected = base64.b64encode(b":fixture-local-secret").decode()
        assert request.headers["authorization"] == f"Basic {expected}"
        if request.method == "GET":
            return httpx.Response(200, json={"FutureField": True}, headers={"ETag": '"revision-1"'})
        assert request.headers["if-match"] == '"revision-1"'
        assert json.loads(request.content) == {"FutureField": True, "Web": {}}
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handle)
    store = ServeStore(TailscaleCli())
    snapshot = store.read(CONNECTION, transport)
    store.write(CONNECTION, snapshot, {**snapshot.config, "Web": {}}, transport)
    assert [request.method for request in seen] == ["GET", "POST"]
    assert all("fixture-local-secret" not in str(request.url) for request in seen)


def test_concurrent_revision_change_refuses_update_without_overwriting_new_state():
    state = {"revision": "one", "config": {"Unrelated": "original"}}

    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json=state["config"], headers={"ETag": state["revision"]})
        assert request.headers["if-match"] == "one"
        assert state["revision"] == "two"
        return httpx.Response(412)

    store = ServeStore(TailscaleCli())
    transport = httpx.MockTransport(handle)
    snapshot = store.read(CONNECTION, transport)
    state.update(revision="two", config={"Unrelated": "external update"})
    with pytest.raises(HeinzelError, match="nothing was overwritten"):
        store.write(CONNECTION, snapshot, {"Unrelated": "old"}, transport)
    assert state["config"] == {"Unrelated": "external update"}


@pytest.mark.parametrize("headers", [{}, {"etag": ""}, [("etag", "one"), ("etag", "two")], {"etag": "x" * 257}])
def test_missing_ambiguous_or_unbounded_version_is_refused(headers):
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={}, headers=headers))
    with pytest.raises(HeinzelError, match="conditional Serve updates"):
        ServeStore(TailscaleCli()).read(CONNECTION, transport)


def test_redirect_is_not_followed_with_the_local_credential():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/"})

    with pytest.raises(HeinzelError, match="refused the action"):
        ServeStore(TailscaleCli()).read(CONNECTION, httpx.MockTransport(handle))
    assert len(requests) == 1


def test_unknown_write_outcome_is_not_automatically_retried():
    requests = []

    def handle(request):
        requests.append(request)
        msg = "response lost after acceptance"
        raise httpx.ReadTimeout(msg)

    with pytest.raises(HeinzelError, match="outcome is unknown"):
        ServeStore(TailscaleCli()).write(CONNECTION, Snapshot({}, "one"), {}, httpx.MockTransport(handle))
    assert len(requests) == 1


def test_oversized_response_is_bounded(monkeypatch):
    monkeypatch.setattr(_localapi, "MAX_BYTES", 32)
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"x" * 64))
    with pytest.raises(HeinzelError, match="size limit"):
        ServeStore(TailscaleCli()).read(CONNECTION, transport)


@pytest.mark.parametrize("body", [b'{"Web": {}, "Web": {}}', b'{"Future": NaN}', b'{"Future": Infinity}'])
def test_ambiguous_or_non_json_serve_documents_cannot_be_used_for_replacement(body):
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=body, headers={"ETag": "one"}))
    with pytest.raises(HeinzelError, match="invalid Serve document"):
        ServeStore(TailscaleCli()).read(CONNECTION, transport)


@pytest.mark.parametrize("output", [b"x" * 4097, b"\xff", b"curl 'unfinished"])
def test_unbounded_or_unparsable_endpoint_discovery_is_refused(output):
    with pytest.raises(HeinzelError):
        Connection.parse(output)


@pytest.mark.parametrize("code", [0, 1])
def test_connection_discovery_is_an_explicit_bounded_client_call(monkeypatch, code):
    cli = TailscaleCli()
    seen = []

    def run(arguments):
        seen.append(tuple(arguments))
        return Result(code, b"curl -u:fixture-local-secret http://localhost:41112/localapi/v0/status", b"")

    monkeypatch.setattr(cli, "run", run)
    if code:
        with pytest.raises(HeinzelError, match="permissions"):
            ServeStore(cli).connect()
    else:
        assert ServeStore(cli).connect() == CONNECTION
    assert seen == [("debug", "local-creds")]


@pytest.mark.parametrize("body", [b"null", b"[]", b"invalid"])
def test_empty_config_is_supported_but_other_document_shapes_are_refused(body):
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=body, headers={"ETag": "one"}))
    if body == b"null":
        assert ServeStore(TailscaleCli()).read(CONNECTION, transport).config == {}
    else:
        with pytest.raises(HeinzelError, match="Serve document"):
            ServeStore(TailscaleCli()).read(CONNECTION, transport)
