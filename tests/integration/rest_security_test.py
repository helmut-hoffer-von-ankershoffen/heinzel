"""The REST head answers only local, same-origin requests and exposes nothing that spends or acts unattended."""

import pytest
from fastapi.testclient import TestClient

from heinzel import Module
from heinzel.heads import api

pytestmark = pytest.mark.integration


def notes() -> Module:
    module = Module("notes", "Test-only operations.")

    @module.operation
    def title() -> str:
        """Show a title."""
        return "Weekly"

    @module.operation(read_only=False)
    def touch() -> str:
        """Change something."""
        return "touched"

    return module


@pytest.fixture
def app(make_kernel):
    return api.build(make_kernel(builtins=True, notes=notes()), port=8000, allowed_hosts=["hz.example.test"])


@pytest.mark.parametrize("base", ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"])
def test_local_requests_work(app, base):
    client = TestClient(app, base_url=base)
    assert client.get("/notes/title").json() == "Weekly"
    assert client.post("/notes/touch", headers={"Origin": base}).json() == "touched"


def test_a_configured_host_works(app):
    assert TestClient(app, base_url="http://hz.example.test:8000").get("/notes/title").status_code == 200


@pytest.mark.parametrize(
    "base",
    ["http://evil.example.test:8000", "http://localhost:9999", "http://localhost", "http://testserver"],
)
def test_a_request_naming_another_host_or_port_is_refused(app, base):
    response = TestClient(app, base_url=base).get("/notes/title")
    assert response.status_code == 403
    assert "this server answers requests to" in response.json()["error"]
    assert "api_allowed_hosts" in response.json()["error"]


def test_a_dns_rebinding_host_header_is_refused(app):
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    response = client.post("/notes/touch", headers={"Host": "attacker.example.test:8000"})
    assert response.status_code == 403


def test_a_cross_origin_post_is_refused(app):
    client = TestClient(app, base_url="http://localhost:8000")
    response = client.post("/notes/touch", headers={"Origin": "https://evil.example.test"})
    assert response.status_code == 403
    assert "another origin" in response.json()["error"]


@pytest.mark.parametrize(
    ("site", "status"), [("cross-site", 403), ("same-site", 403), ("same-origin", 200), ("none", 200)]
)
def test_without_an_origin_a_request_another_site_made_is_refused(app, site, status):
    client = TestClient(app, base_url="http://localhost:8000")
    response = client.post("/notes/touch", headers={"Sec-Fetch-Site": site})
    assert response.status_code == status
    if status == 403:
        assert "Sec-Fetch-Site" in response.json()["error"]


@pytest.mark.parametrize("port", [80, 443])
def test_on_a_default_port_a_host_without_a_port_works(make_kernel, port):
    app = api.build(make_kernel(builtins=True, notes=notes()), port=port)
    assert TestClient(app, base_url="http://localhost").get("/notes/title").status_code == 200
    assert TestClient(app, base_url=f"http://localhost:{port}").get("/notes/title").status_code == 200
    assert TestClient(app, base_url="http://localhost:8000").get("/notes/title").status_code == 403


def test_spending_and_unattended_operations_are_not_rest_endpoints(make_kernel):
    client = TestClient(api.build(make_kernel(builtins=True)), base_url="http://localhost")
    assert client.post("/system/daemon/tick").status_code == 404
    assert client.post("/heinzel/wake", params={"name": "x"}).status_code == 404
    assert client.post("/workflow/run", params={"name": "x"}).status_code == 404
    assert client.get("/heinzel/list").status_code == 404  # the module's HTTP API is the web app's (ADR 0051)
    assert client.get("/system/info").status_code == 200


def test_the_split_of_host_headers():
    assert api._split("LocalHost:8000") == ("localhost", "8000")
    assert api._split("[::1]:8000") == ("[::1]", "8000")
    assert api._split("[::1]") == ("[::1]", "")
    assert api._split("[::1") == ("[::1", "")


def test_serve_passes_its_port_and_your_allowed_hosts(monkeypatch, make_kernel):
    import uvicorn

    monkeypatch.setenv("HZ_API_ALLOWED_HOSTS", "hz.example.test, other.example.test")
    served = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: served.update(app=app, host=host, port=port))
    api.serve("0.0.0.0", 8123)  # noqa: S104 - nothing listens; uvicorn.run is replaced
    client = TestClient(served["app"], base_url="http://other.example.test:8123")
    assert client.get("/system/doctor").status_code in {200, 503}
    assert TestClient(served["app"], base_url="http://localhost:8000").get("/system/doctor").status_code == 403
    assert (served["host"], served["port"]) == ("0.0.0.0", 8123)  # noqa: S104 - what was passed
