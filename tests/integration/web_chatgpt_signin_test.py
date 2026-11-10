"""Start a real loopback-shaped web sign-in with a fictional browser, token endpoint, JWKS and keychain."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from time import time
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic_ai.providers import openai_chatgpt

from heinzel import CredentialLet, Credentials
from heinzel.heads import web
from heinzel.heads.web import _chatgpt_signin
from heinzel.heads.web._devices import Devices
from heinzel.testing import MemoryBackend

pytestmark = pytest.mark.integration
ORIGIN = "http://127.0.0.1:34123"
START = "/api/ai/access/chatgpt-sign-in"
AUTH = {"Authorization": "Bearer fictional-owner-session", "Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
REDIRECT = {"Sec-Fetch-Site": "cross-site"}
CODE = "fictional-provider-code"
ACCESS = "fictional-provider-access-token"
REFRESH = "fictional-provider-refresh-token"
CLIENT = "oaiapp_fictional"


@pytest.fixture
def sign_in(make_kernel, tmp_path, monkeypatch):
    kernel = make_kernel(builtins=True)
    backend = MemoryBackend()
    kernel.bind(Credentials, Credentials(backend, [(r.qualname, let) for r, let in kernel.lets(CredentialLet)]))
    devices = Devices(tmp_path / "devices")
    _, device = devices.pair("Fictional paired device")
    app = web.build(kernel, port=34123, token="fictional-owner-session", static=None, devices=devices)
    opened = Queue()
    monkeypatch.setattr(_chatgpt_signin.webbrowser, "open", lambda url, *, new: opened.put(url) or True)

    async def no_exchange(*_args, **_kwargs):
        pytest.fail("This isolated test has no fictional provider response")

    monkeypatch.setattr(openai_chatgpt, "_post_token_request", no_exchange)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk["kid"] = "fixture-key"
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda _self: {"keys": [jwk]})
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50400), follow_redirects=False) as client:
        yield client, backend, opened, key, device


def token_response(key, nonce, *, scopes="openid chatgpt.tokens.use.direct"):
    identity = jwt.encode(
        {
            "iss": "https://auth.openai.com",
            "sub": "fictional-account",
            "aud": CLIENT,
            "nonce": nonce,
            "exp": time() + 90,
        },
        key,
        algorithm="RS256",
        headers={"kid": "fixture-key"},
    )
    return openai_chatgpt._TokenResponse(  # test seam at the actual network exchange
        access_token=ACCESS, refresh_token=REFRESH, id_token=identity, scope=scopes
    )


def test_local_owner_starts_one_flow_and_verified_callback_keeps_only_keychain_credentials(
    sign_in, monkeypatch, caplog
):
    client, backend, opened, key, _ = sign_in
    exchanges = []
    caplog.set_level(logging.INFO, logger=_chatgpt_signin.__name__)
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(client.post, START, headers=AUTH)
        authorization = urlsplit(opened.get(timeout=4))
        params = {k: values[0] for k, values in parse_qs(authorization.query).items()}
        assert (authorization.scheme, authorization.hostname) == ("https", "auth.openai.com")
        assert params["redirect_uri"] == f"{ORIGIN}/auth/callback"
        assert params["code_challenge_method"] == "S256"
        assert "id_token_hint" not in params

        async def exchange(_url, form, *_args, **_kwargs):
            exchanges.append(form)
            return token_response(key, params["nonce"])

        monkeypatch.setattr(openai_chatgpt, "_post_token_request", exchange)
        assert client.post(START, headers=AUTH).status_code == 409
        assert backend.get("ai.access", "chatgpt-login") is None
        query = {"state": params["state"], "code": CODE, "client_id": CLIENT}
        for bad_query in ({**query, "state": "wrong"}, {**query, "state": [params["state"], "wrong"]}):
            wrong = client.get("/auth/callback", params=bad_query, headers=REDIRECT)
            assert wrong.status_code == 400
            assert CODE not in wrong.text
        refused = client.get("http://localhost:34123/auth/callback", params=query, headers=REDIRECT)
        assert refused.status_code == 403
        received = client.get("/auth/callback", params=query, headers=REDIRECT)
        assert received.status_code == 303
        assert received.headers["Location"] == "/auth/chatgpt-finished"
        assert received.headers["Cache-Control"] == "no-store"
        assert received.headers["Referrer-Policy"] == "no-referrer"
        assert CODE not in received.text
        assert client.get(received.headers["Location"], headers=REDIRECT).status_code == 200
        assert client.get("/auth/callback", params=query, headers=REDIRECT).status_code == 400
        result = pending.result(timeout=4)
    assert result.json() == {"outcome": "connected"}
    assert result.headers["Cache-Control"] == "no-store"
    assert len(exchanges) == 1
    assert exchanges[0]["code"] == CODE
    assert exchanges[0]["redirect_uri"] == params["redirect_uri"]
    assert exchanges[0]["code_verifier"] != params["code_challenge"]
    assert json.loads(backend.get("ai.access", "chatgpt-login"))["refresh_token"] == REFRESH
    records = [record for record in caplog.records if record.name == _chatgpt_signin.__name__]
    assert [(record.phase, getattr(record, "outcome", None)) for record in records] == [
        ("start", None),
        ("finish", "connected"),
    ]
    for sensitive in (ACCESS, REFRESH, CODE, params["state"], params["nonce"]):
        assert sensitive not in result.text
        assert sensitive not in str([record.__dict__ for record in records])


def test_a_paired_device_reverse_proxy_cross_site_and_secret_input_cannot_start(sign_in):
    client, backend, opened, _, device = sign_in
    assert client.post(START).status_code == 401
    assert client.post(START, headers={"Authorization": AUTH["Authorization"]}).status_code == 403
    assert client.post(START, headers={**AUTH, "Authorization": f"Bearer {device}"}).status_code == 403
    assert client.post(START, headers={**AUTH, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post(START, headers={**AUTH, "Origin": "https://fictional.example"}).status_code == 403
    assert client.post(START, headers={**AUTH, "X-Forwarded-For": "192.0.2.4"}).status_code == 403
    assert client.post(START, headers=AUTH, json={"secret": ACCESS}).status_code == 400
    assert client.post(START, headers=AUTH, params={"secret": ACCESS}).status_code == 400
    assert opened.empty()
    assert backend.get("ai.access", "chatgpt-login") is None


def test_an_owner_browser_can_omit_fetch_metadata_but_keeps_the_exact_origin(sign_in, monkeypatch):
    client, _, _, _, _ = sign_in
    monkeypatch.setattr(_chatgpt_signin.webbrowser, "open", lambda *_args, **_kwargs: False)
    result = client.post(START, headers={name: value for name, value in AUTH.items() if name != "Sec-Fetch-Site"})
    assert result.json() == {"outcome": "browser-unavailable"}


def test_a_forged_signature_never_replaces_a_prior_credential(sign_in, monkeypatch):
    client, backend, opened, _, _ = sign_in
    prior = '{"client_id":"oaiapp_fictional","access_token":"prior","refresh_token":"prior","scope":"openid"}'
    backend.set("ai.access", "chatgpt-login", prior)
    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(client.post, START, headers=AUTH)
        params = parse_qs(urlsplit(opened.get(timeout=4)).query)
        assert params["prompt"] == ["consent"]

        async def exchange(*_args, **_kwargs):
            return token_response(forged_key, params["nonce"][0])

        monkeypatch.setattr(openai_chatgpt, "_post_token_request", exchange)
        query = {"state": params["state"][0], "code": CODE, "client_id": CLIENT}
        assert client.get("/auth/callback", params=query, headers=REDIRECT).status_code == 303
        assert pending.result(timeout=4).json() == {"outcome": "failed"}
    assert backend.get("ai.access", "chatgpt-login") == prior


def test_declining_in_the_browser_prevents_exchange_and_leaves_no_credential(sign_in, monkeypatch):
    client, backend, opened, _, _ = sign_in

    async def no_exchange(*_args, **_kwargs):
        pytest.fail("OAuth denial must not exchange an authorization code")

    monkeypatch.setattr(openai_chatgpt, "_post_token_request", no_exchange)
    with ThreadPoolExecutor() as executor:
        pending = executor.submit(client.post, START, headers=AUTH)
        params = parse_qs(urlsplit(opened.get(timeout=4)).query)
        query = {"state": params["state"][0], "error": "access_denied", "code": CODE}
        assert client.get("/auth/callback", params=query, headers=REDIRECT).status_code == 303
        assert pending.result(timeout=4).json() == {"outcome": "declined"}
    assert backend.get("ai.access", "chatgpt-login") is None


def test_an_expired_attempt_is_forgotten(sign_in, monkeypatch):
    client, backend, opened, _, _ = sign_in
    monkeypatch.setattr(_chatgpt_signin, "SIGN_IN_TIMEOUT", 0.2)
    assert client.post(START, headers=AUTH).json() == {"outcome": "expired"}
    params = parse_qs(urlsplit(opened.get(timeout=4)).query)
    query = {"state": params["state"][0], "code": CODE, "client_id": CLIENT}
    assert client.get("/auth/callback", params=query, headers=REDIRECT).status_code == 400
    assert backend.get("ai.access", "chatgpt-login") is None


def test_a_disconnected_initiating_session_cannot_be_completed_later(sign_in):
    client, backend, opened, _, _ = sign_in

    async def disconnect():
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": b""})

        async def send(_message):
            pass

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "scheme": "http",
            "http_version": "1.1",
            "method": "POST",
            "path": START,
            "root_path": "",
            "query_string": b"",
            "server": ("127.0.0.1", 34123),
            "client": ("127.0.0.1", 50400),
            "headers": [(b"host", b"127.0.0.1:34123")]
            + [(name.lower().encode(), value.encode()) for name, value in AUTH.items()],
        }
        request = asyncio.create_task(client.app(scope, incoming.get, send))
        url = await asyncio.to_thread(opened.get, timeout=4)
        incoming.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(request, 4)
        return parse_qs(urlsplit(url).query)["state"][0]

    state = asyncio.run(disconnect())
    assert client.get("/auth/callback", params={"state": state, "code": CODE}, headers=REDIRECT).status_code == 400
    assert backend.get("ai.access", "chatgpt-login") is None
