"""Web update facts describe the running profile and frontend without checking the network or changing a pin."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from heinzel import Settings, __version__
from heinzel.heads import web
from heinzel.kernel import CorePin, Installation, ReleaseCache

pytestmark = pytest.mark.integration
TOKEN = "t" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Receipt(Installation):
    def __init__(self, pin):
        self.pin = pin

    def core_pin(self):
        return self.pin


def client(make_kernel, tmp_path, **options):
    kind, ref = options.get("kind", "release"), options.get("ref", "v0.1.0")
    checking, restart, unsafe = options.get("checking", True), options.get("restart", True), options.get("unsafe")
    static = tmp_path / "static"
    static.mkdir(exist_ok=True)
    (static / "assets").mkdir(exist_ok=True)
    (static / "index.html").write_text(
        '<script type="module" src="/assets/app-a.js"></script>'
        "<script>window.splashReady = true;</script>"
        '<link rel="stylesheet" href="/assets/app-a.css">'
    )
    (static / "assets" / "app-a.js").write_text("export const ready = true;")
    (static / "assets" / "app-a.css").write_text("body { color: black; }")
    if unsafe == "remote":
        (static / "index.html").write_text('<script src="https://example.test/app.js"></script>')
    elif unsafe in {"missing", "symlink"}:
        (static / "assets" / "app-a.js").unlink()
        if unsafe == "symlink":
            outside = tmp_path / "outside.js"
            outside.write_text("not an installed asset")
            (static / "assets" / "app-a.js").symlink_to(outside)
    settings = Settings(cache_dir=tmp_path / "cache", update_check=checking)
    kernel = make_kernel(builtins=True, settings_=settings)
    kernel.bind(Installation, Receipt(CorePin(kind, "heinzel", ref=ref) if kind else None))
    ReleaseCache(settings.cache_dir).remember("99.0.0")
    return TestClient(
        web.build(kernel, port=8000, token=TOKEN, static=static, restart=(lambda: None) if restart else None),
        base_url="http://127.0.0.1:8000",
    )


def test_update_facts_are_authenticated_cache_only_and_keep_source_intent(make_kernel, tmp_path):
    app = client(make_kernel, tmp_path)
    assert app.get("/api/update-status").status_code == 401
    response = app.get("/api/update-status", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "current": __version__,
        "latest": "99.0.0",
        "available": True,
        "supported": True,
        "reason": None,
    }


@pytest.mark.parametrize(
    ("kind", "ref"), [(None, None), ("commit", "a" * 40), ("ref", "work"), ("local", "/example/hz")]
)
def test_deliberate_pins_and_development_installs_have_no_one_click_override(make_kernel, tmp_path, kind, ref):
    result = client(make_kernel, tmp_path, kind=kind, ref=ref).get("/api/update-status", headers=AUTH).json()
    assert result["available"]
    assert not result["supported"]
    assert result["reason"]
    assert "/example/hz" not in str(result)


def test_check_opt_out_hides_the_hint_and_non_restartable_server_is_honest(make_kernel, tmp_path):
    result = client(make_kernel, tmp_path, checking=False, restart=False).get("/api/update-status", headers=AUTH).json()
    assert result["latest"] is None
    assert not result["available"]
    assert not result["supported"]


def test_frontend_descriptor_hashes_only_installed_assets_and_entrypoints(make_kernel, tmp_path):
    app = client(make_kernel, tmp_path)
    assert app.get("/api/frontend").status_code == 401
    response = app.get("/api/frontend", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    result = response.json()
    assert result["entrypoints"] == ["/assets/app-a.css", "/assets/app-a.js"]
    assert result["files"] == {
        "/assets/app-a.css": hashlib.sha256(b"body { color: black; }").hexdigest(),
        "/assets/app-a.js": hashlib.sha256(b"export const ready = true;").hexdigest(),
    }
    assert len(result["identity"]) == 64
    assert result["version"] == __version__


@pytest.mark.parametrize("unsafe", ["remote", "missing", "symlink"])
def test_unsafe_or_missing_frontend_cannot_enable_one_click(make_kernel, tmp_path, unsafe):
    app = client(make_kernel, tmp_path, unsafe=unsafe)
    assert app.get("/api/frontend", headers=AUTH).status_code == 409
    status = app.get("/api/update-status", headers=AUTH).json()
    assert not status["supported"]
    assert "Frontend assets are incomplete" in status["reason"]
    assert "uv tool upgrade heinzel" in status["reason"]
