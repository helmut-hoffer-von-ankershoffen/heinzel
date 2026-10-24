"""The installable app (ADR 0046): manifest, service worker and icons served with the right types, headers and CSP."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from heinzel.heads import web
from heinzel.heads.web._devices import Devices

pytestmark = pytest.mark.integration

BASE = "http://127.0.0.1:8000"
PUBLIC = Path(__file__).resolve().parents[2] / "web" / "public"


@pytest.fixture
def client(make_kernel, tmp_path):
    static = tmp_path / "static"
    (static / "icons").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (static / "sw.js").write_text((PUBLIC / "sw.js").read_text(encoding="utf-8"), encoding="utf-8")
    (static / "manifest.webmanifest").write_text(
        (PUBLIC / "manifest.webmanifest").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (static / "icons" / "icon-192.png").write_bytes((PUBLIC / "icons" / "icon-192.png").read_bytes())
    app = web.build(make_kernel(builtins=True), port=8000, token="t", static=static, devices=Devices(tmp_path / "d"))
    return TestClient(app, base_url=BASE)


def test_the_manifest_and_the_worker_are_served_fresh_under_the_csp(client):
    manifest = client.get("/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.headers["cache-control"] == "no-cache"
    worker = client.get("/sw.js")
    assert worker.headers["content-type"].startswith(("text/javascript", "application/javascript"))
    assert worker.headers["cache-control"] == "no-cache"
    csp = worker.headers["content-security-policy"]
    assert "worker-src 'self'" in csp
    assert "manifest-src 'self'" in csp
    assert client.get("/icons/icon-192.png").headers["content-type"] == "image/png"


def test_the_manifest_makes_an_installable_standalone_app():
    manifest = json.loads((PUBLIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert (manifest["name"], manifest["short_name"], manifest["display"]) == ("Heinzelmännchen", "hz", "standalone")
    assert manifest["start_url"] == "/"
    assert manifest["theme_color"] == manifest["background_color"] == "#181818"
    sizes = {(icon["sizes"], icon["purpose"]) for icon in manifest["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes
    for icon in manifest["icons"]:
        assert (PUBLIC / icon["src"].lstrip("/")).is_file()
    assert (PUBLIC / "icons" / "apple-touch-icon.png").is_file()
    assert (PUBLIC / "favicon.ico").is_file()


def test_the_worker_never_caches_api_data_or_leaves_this_origin():
    source = (PUBLIC / "sw.js").read_text(encoding="utf-8")
    assert 'url.pathname.startsWith("/api/")' in source
    assert "url.origin !== self.location.origin" in source
    assert "http" not in source.replace("https://", "").replace("http://", "")  # no other host named
