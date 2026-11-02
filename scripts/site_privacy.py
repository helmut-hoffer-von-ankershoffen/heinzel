# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""What the built site may request from a visitor's browser: its own origin, plus hosts listed here with a reason.

Standard library only, so the unit tests can import it; `site_check.py` runs it over the built site and in a browser.
"""

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


class PrivacyError(Exception):
    """A built page would make a visitor's browser contact a host that is not listed."""


def require(condition: bool, message: str) -> None:
    """Fail with what was found.

    Raises:
        PrivacyError: If the condition does not hold.
    """
    if not condition:
        raise PrivacyError(message)


# Every host a visitor's browser may contact besides the site itself, with the reason. The list is empty on purpose:
# a page view requests only this site (docs/privacy.md says so). To add a host, name it here with the reason AND in
# docs/privacy.md, or the check fails.
THIRD_PARTY_HOSTS: dict[str, str] = {}
LOADED_ATTRIBUTES = {
    ("img", "src"),
    ("img", "srcset"),
    ("source", "src"),
    ("source", "srcset"),
    ("script", "src"),
    ("iframe", "src"),
    ("frame", "src"),
    ("embed", "src"),
    ("object", "data"),
    ("video", "src"),
    ("video", "poster"),
    ("audio", "src"),
    ("track", "src"),
    ("input", "src"),
    ("image", "href"),
    ("image", "xlink:href"),
}
LOADED_RELATIONS = {
    "stylesheet",
    "icon",
    "preload",
    "prefetch",
    "modulepreload",
    "preconnect",
    "dns-prefetch",
    "manifest",
}
CSS_URL = re.compile(r"""url\(\s*['"]?([^'")\s]+)|@import\s+['"]([^'"]+)""")
LEGAL_PAGES = ("imprint", "privacy")


class LoadedUrls(HTMLParser):
    """Collect the URLs a browser fetches by itself when it renders a page; links the visitor follows are not loads."""

    def __init__(self) -> None:
        """Start with no URLs found and outside any <style> element."""
        super().__init__()
        self.urls: list[str] = []
        self.style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record loading attributes, stylesheet-like links and inline CSS urls."""
        values = {name: value or "" for name, value in attrs}
        for name, value in values.items():
            if (tag, name) in LOADED_ATTRIBUTES:
                self.urls.extend(part.split()[0] for part in value.split(",") if part.strip())
            if name == "style":
                self.urls.extend(next(filter(None, found)) for found in CSS_URL.findall(value))
        if tag == "link" and LOADED_RELATIONS & set(values.get("rel", "").lower().split()):
            self.urls.append(values.get("href", ""))
        if tag == "meta" and values.get("http-equiv", "").lower() == "refresh":
            self.urls.append(values.get("content", "").partition("url=")[2])
        self.style = tag == "style"

    def handle_data(self, data: str) -> None:
        """Record urls in a <style> element."""
        if self.style:
            self.urls.extend(next(filter(None, found)) for found in CSS_URL.findall(data))

    def handle_endtag(self, tag: str) -> None:
        """Leave a <style> element."""
        if tag == "style":
            self.style = False


def foreign_host(url: str, own: str) -> str | None:
    """The host of an absolute URL that is not the site's own, or None for a relative or own-origin one."""
    parts = urlsplit(url.strip())
    if parts.scheme in {"", "data", "blob", "about", "javascript", "mailto"} and not parts.netloc:
        return None
    host = parts.netloc.rpartition("@")[2]
    return None if not host or host == own else host


def unlisted(hosts: set[str]) -> list[str]:
    """The hosts that THIRD_PARTY_HOSTS does not list."""
    return sorted(host for host in hosts if host not in THIRD_PARTY_HOSTS)


def static_privacy(site: Path, own: str) -> None:
    """Every built page and stylesheet may load only its own origin or a listed host; no JavaScript is trusted here."""
    found: dict[str, str] = {}
    for path in site.rglob("*"):
        if path.suffix == ".html":
            parser = LoadedUrls()
            parser.feed(path.read_text(errors="replace"))
            urls = parser.urls
        elif path.suffix == ".css":
            urls = [next(filter(None, match)) for match in CSS_URL.findall(path.read_text(errors="replace"))]
        else:
            continue
        for url in urls:
            if (host := foreign_host(url, own)) is not None:
                found.setdefault(host, f"{path.relative_to(site)} -> {url}")
    bad = unlisted(set(found))
    require(
        not bad,
        "Third-party loads in the built site (list them in THIRD_PARTY_HOSTS with a reason): "
        + "; ".join(f"{h} ({found[h]})" for h in bad),
    )


def legal_pages_name_hosts(site: Path) -> None:
    """A listed third-party host must be named in the privacy page, and the legal pages must exist."""
    for name in LEGAL_PAGES:
        require((site / name / "index.html").is_file(), f"The {name} page is part of the site")
    for host in THIRD_PARTY_HOSTS:
        require(
            host in (site / "privacy" / "index.html").read_text(), f"{host} is listed but not named in the privacy page"
        )
