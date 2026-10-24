"""The web head (ADR 0039): the app at `/`, the API at `/api` behind a session token, all under a strict CSP."""

import base64
import hashlib
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from heinzel import HeinzelError, Module
from heinzel.heads import api, web
from heinzel.heads._exposure import exposed

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
TOKEN = "s3ssion-token-for-tests"
BASE = "http://127.0.0.1:8000"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def notes() -> Module:
    module = Module("notes", "Test-only operations.")

    @module.operation
    def title() -> str:
        """Show a title."""
        return "Weekly"

    @module.operation(heads={"cli", "web"})
    def private() -> str:
        """Show what only the token-protected web head may."""
        return "for your eyes"

    @module.operation(read_only=False)
    def touch() -> str:
        """Change something."""
        return "touched"

    return module


@pytest.fixture
def static(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>hz</title><div id=root></div>", encoding="utf-8")
    (tmp_path / "assets" / "app-1234.js").write_text("console.log('hz')", encoding="utf-8")
    (tmp_path / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path.parent / "secret.txt").write_text("outside", encoding="utf-8")
    return tmp_path


@pytest.fixture
def kernel(make_kernel):
    return make_kernel(builtins=True, notes=notes())


@pytest.fixture
def client(kernel, static):
    return TestClient(web.build(kernel, port=8000, token=TOKEN, static=static), base_url=BASE)


def test_the_api_needs_the_session_token_as_a_bearer_header(client):
    assert client.get("/api/notes/title", headers=AUTH).json() == "Weekly"
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}, {"X-Hz-Token": TOKEN}):
        response = client.get("/api/notes/title", headers=headers)
        assert response.status_code == 401
        assert "Authorization: Bearer" in response.json()["error"]
        assert response.headers["WWW-Authenticate"] == "Bearer"


def test_a_cookie_does_not_stand_in_for_the_token(client):
    client.cookies.set("token", TOKEN)
    assert client.get("/api/notes/title").status_code == 401


def test_the_rest_heads_refusals_still_apply_under_api(client):
    other_origin = {**AUTH, "Origin": "http://evil.example.test"}
    assert client.post("/api/notes/touch", headers=other_origin).status_code == 403
    cross_site = {**AUTH, "Sec-Fetch-Site": "cross-site"}
    assert client.get("/api/notes/title", headers=cross_site).status_code == 403
    rebinding = {**AUTH, "Host": "attacker.example.test:8000"}
    assert client.get("/api/notes/title", headers=rebinding).status_code == 403
    assert client.post("/api/notes/touch", headers={**AUTH, "Origin": BASE}).json() == "touched"


def test_pages_are_refused_to_another_host_or_site_too(client):
    assert client.get("/", headers={"Host": "attacker.example.test:8000"}).status_code == 403
    assert client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_every_response_carries_the_csp_and_the_other_security_headers(client):
    responses = [
        client.get("/"),
        client.get("/doctor"),
        client.get("/assets/app-1234.js"),
        client.get("/api/notes/title", headers=AUTH),
        client.get("/api/notes/title"),
        client.get("/", headers={"Host": "attacker.example.test:8000"}),
    ]
    for response in responses:
        assert response.headers["Content-Security-Policy"] == web.CSP
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "default-src 'none'" in web.CSP
    # Inline styles only, for the terminal (ADR 0045) and the splash; scripts stay this origin's and the splash's hash.
    assert f"script-src 'self' {web.SPLASH_SCRIPT};" in web.CSP
    assert "style-src 'self' 'unsafe-inline';" in web.CSP
    assert web.CSP.count("unsafe-inline") == 1
    assert "unsafe-eval" not in web.CSP


class InlineScripts(HTMLParser):
    """The text of every script element without a `src`, whatever its other attributes, as a browser parses them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)  # a script's text is raw: the hash covers it as written
        self.scripts: list[str] = []
        self._inside = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # HTMLParser lowercases tag and attribute names, so `SRC=` and `src = "…"` count as a src too.
        if tag == "script" and not any(name == "src" for name, _ in attrs):
            self._inside = True
            self.scripts.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._inside = False

    def handle_data(self, data: str) -> None:
        if self._inside:
            self.scripts[-1] += data


def inline_scripts(page: str) -> list[str]:
    parser = InlineScripts()
    parser.feed(page)
    parser.close()
    return parser.scripts


def script_hash(source: str) -> str:
    """A script's CSP source: `'sha256-<base64>'` of its exact text."""
    return f"'sha256-{base64.b64encode(hashlib.sha256(source.encode()).digest()).decode()}'"


def script_sources(csp: str) -> list[str]:
    directive = next(part.strip() for part in csp.split(";") if part.strip().startswith("script-src "))
    return directive.split()[1:]


def test_the_csp_allows_the_splash_script_by_its_hash_and_no_other_inline_script():
    page = (REPO / "web" / "index.html").read_text(encoding="utf-8")
    inline = inline_scripts(page)
    assert len(inline) == 1, "web/index.html has one inline script, the splash's"
    assert script_hash(inline[0]) == web.SPLASH_SCRIPT, f"update SPLASH_SCRIPT to {script_hash(inline[0])}"
    assert script_sources(web.CSP) == ["'self'", web.SPLASH_SCRIPT]
    for other in ("alert(1)", inline[0] + " ", inline[0].replace("hz.splash", "hz.other")):
        assert script_hash(other) not in web.CSP
    assert inline_scripts('<script SRC="/a.js"></script><script src = "/b.js"></script><SCRIPT type=x>y</SCRIPT>') == [
        "y"
    ]
    assert not any(source in web.CSP for source in ("'unsafe-hashes'", "'strict-dynamic'", "'nonce-"))


def test_the_built_page_keeps_the_splash_script_as_written():
    built = web.STATIC / "index.html"
    if not built.is_file():
        pytest.skip("the web app isn't built (make web-build)")
    inline = inline_scripts(built.read_text(encoding="utf-8"))
    assert [script_hash(source) for source in inline] == [web.SPLASH_SCRIPT], "the build is stale: make web-build"


def test_caching_api_answers_never_and_hashed_assets_forever(client):
    assert client.get("/api/notes/title", headers=AUTH).headers["Cache-Control"] == "no-store"
    assert "immutable" in client.get("/assets/app-1234.js").headers["Cache-Control"]
    assert client.get("/").headers["Cache-Control"] == "no-store"


def test_the_app_is_served_at_every_route_it_has(client):
    for path in ("/", "/doctor", "/contracts/me%3Aweekly-digest", "/settings?q=hz"):
        response = client.get(path)
        assert response.status_code == 200
        assert "<div id=root>" in response.text
    assert client.get("/favicon.svg").text == "<svg/>"


def test_no_file_outside_the_app_is_served(client):
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
        assert "outside" not in client.get(path).text


def test_the_api_alone_serves_no_pages(kernel):
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url=BASE)
    assert client.get("/api/notes/title", headers=AUTH).json() == "Weekly"
    assert client.get("/").status_code == 404


def test_an_installation_without_the_built_app_says_how_to_build_it(kernel, tmp_path):
    with pytest.raises(HeinzelError, match="make web-build"):
        web.build(kernel, port=8000, token=TOKEN, static=tmp_path)


def test_the_openapi_document_is_behind_the_token_and_the_cdn_docs_are_off(client):
    assert client.get("/api/openapi.json").status_code == 401
    assert client.get("/api/openapi.json", headers=AUTH).json()["paths"]["/notes/private"]
    assert client.get("/api/docs", headers=AUTH).status_code == 404


# What the web head must never expose, whatever its token (ADR 0039): money or unattended work, interactive steps.
NEVER_ON_THE_WEB = {
    "workflow_run",
    "system_daemon_tick",
    "system_daemon_install",
    "system_daemon_uninstall",
    "system_credential_set",
    "system_credential_remove",
    "system_plugin_new",
}


def test_the_web_head_exposes_what_the_token_protects_and_never_what_spends_or_asks(kernel):
    names = {item.name for item in exposed(kernel, web.HEAD)}
    assert not names & NEVER_ON_THE_WEB
    assert {"org_show", "workflow_discover", "system_runner_ask", "notes_private"} <= names


def test_the_rest_head_keeps_its_set_and_its_lack_of_a_token(kernel):
    names = {item.name for item in exposed(kernel, api.HEAD)}
    assert not names & {"org_show", "workflow_discover", "system_runner_ask", "notes_private"}
    rest = TestClient(api.build(kernel, port=8000), base_url=BASE)
    assert rest.get("/notes/title").json() == "Weekly"
    assert rest.get("/notes/private").status_code == 404
