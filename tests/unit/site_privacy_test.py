"""The built site may request only its own origin; a reintroduced third-party load fails the site check."""

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
OWN = "heinzel.team"


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location("site_privacy", ROOT / "scripts" / "site_privacy.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["site_privacy"] = module
    spec.loader.exec_module(module)
    return module


def build(tmp_path: Path, html: str = "", css: str = "") -> Path:
    (tmp_path / "index.html").write_text(f"<!doctype html><html><head>{html}</head><body></body></html>")
    (tmp_path / "site.css").write_text(css)
    return tmp_path


@pytest.mark.parametrize(
    "html",
    [
        '<img src="https://img.shields.io/badge/a-b-green">',
        '<img srcset="/a.png 1x, https://cdn.example.org/a.png 2x" src="/a.png">',
        '<script src="//cdn.example.org/x.js"></script>',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Roboto">',
        '<link rel="preconnect" href="https://fonts.gstatic.com">',
        '<iframe src="https://www.youtube-nocookie.com/embed/abcdefghijk"></iframe>',
        '<video poster="https://i.ytimg.com/vi/x/hqdefault.jpg"></video>',
        '<div style="background:url(https://example.org/a.png)"></div>',
        "<style>@import 'https://fonts.example.org/f.css';</style>",
    ],
)
def test_a_third_party_load_in_a_page_fails(check, tmp_path, html):
    with pytest.raises(check.PrivacyError, match="Third-party loads"):
        check.static_privacy(build(tmp_path, html), OWN)


def test_a_third_party_font_in_a_stylesheet_fails(check, tmp_path):
    css = '@font-face{src:url("https://fonts.gstatic.com/s/roboto.woff2")}'
    with pytest.raises(check.PrivacyError, match=r"fonts\.gstatic\.com"):
        check.static_privacy(build(tmp_path, css=css), OWN)


def test_own_origin_relative_urls_and_followed_links_pass(check, tmp_path):
    html = (
        '<img src="/a.png"><img src="data:image/gif;base64,AAAA"><img src="https://heinzel.team/b.png">'
        '<link rel="canonical" href="https://heinzel.team/">'
        '<a href="https://www.youtube.com/watch?v=abcdefghijk">Listen on YouTube</a>'
    )
    check.static_privacy(build(tmp_path, html, "a{background:url(img/x.svg)}"), OWN)


def test_a_listed_host_passes_and_must_be_named_in_both_privacy_pages(check, tmp_path, monkeypatch):
    monkeypatch.setitem(check.THIRD_PARTY_HOSTS, "badges.example.org", "status badge")
    check.static_privacy(build(tmp_path, '<img src="https://badges.example.org/x.svg">'), OWN)
    for name in check.LEGAL_PAGES:
        (tmp_path / name).mkdir()
        (tmp_path / name / "index.html").write_text("no host here")
    with pytest.raises(check.PrivacyError, match=r"badges\.example\.org is listed but not named"):
        check.legal_pages_name_hosts(tmp_path)
    for name in ("privacy", "datenschutz"):
        (tmp_path / name / "index.html").write_text("badges.example.org")
    check.legal_pages_name_hosts(tmp_path)


def test_the_legal_pages_are_in_the_footer_of_the_template():
    footer = (ROOT / "docs/overrides/partials/copyright.html").read_text()
    for page in ("impressum/", "imprint/", "datenschutz/", "privacy/"):
        assert f"'{page}' | url" in footer
