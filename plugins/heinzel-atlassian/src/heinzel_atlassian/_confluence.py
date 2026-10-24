"""The Confluence adapter of the `documents` capability (ADR 0028), read mode only.

A page comes from Confluence Cloud's REST API v2, `GET /wiki/api/v2/pages/{id}?body-format=storage`
(ADR 0034), and its storage format becomes Markdown-flavoured text.
References are page ids or page URLs on the configured site; URLs anywhere else are refused before any request.
"""

import re
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from heinzel import Credentials, Document, HeinzelError
from heinzel_atlassian._client import clean, connect
from heinzel_atlassian._models import SITE_EXAMPLE, AtlassianSettings
from heinzel_atlassian._storage import storage_to_text
from heinzel_atlassian._transport import Transport

_ID = re.compile(r"^[0-9]{1,20}$")
_PAGE_PATH = re.compile(r"^/wiki/spaces/[^/]+/pages/(?:edit-v2/|edit/)?([0-9]{1,20})(?:/.*)?$")
_VIEWPAGE = "/wiki/pages/viewpage.action"


class Confluence:
    """Reads Confluence pages of the configured site, by page id or page URL."""

    def __init__(self, settings: AtlassianSettings, credentials: Credentials, transport: Transport) -> None:
        """Build the adapter; the kernel injects the settings, the credentials and the transport."""
        self._settings = settings
        self._credentials = credentials
        self._transport = transport

    def read(self, ref: str) -> Document:
        """The text of the page `ref`: a page id such as `123456`, or its URL on the configured site.

        Raises:
            HeinzelError: If `ref` is neither, or names another site, the site, email or token is missing, or
                Confluence fails.
        """
        page_id = page_id_of(ref, self._settings.site_url)
        client = connect(self._settings, self._credentials, self._transport)
        site = cast("str", self._settings.site_url)  # connect() checked it
        page = client.get("Confluence", f"/wiki/api/v2/pages/{page_id}", {"body-format": "storage"}, f"page {page_id}")
        return _document(ref, site, page_id, page)


def page_id_of(ref: str, site_url: str | None) -> str:
    """The page id in `ref`, a page id or a page URL on `site_url`.

    Raises:
        HeinzelError: If `ref` is neither, or a URL on another site or not over https.
    """
    text = ref.strip()
    if _ID.fullmatch(text):
        return text
    parts = urlsplit(text)
    if not parts.scheme:
        msg = f"{ref!r} is neither a Confluence page id nor a page URL; pass e.g. 123456 or {SITE_EXAMPLE}/wiki/..."
        raise HeinzelError(msg)
    site = urlsplit(site_url or "")
    if site_url is None or parts.scheme != "https" or parts.netloc.lower() != site.netloc:
        where = site_url or "the configured site (atlassian.site_url is not set)"
        msg = f"{ref!r} is not a page on {where}; the adapter reads pages of that site only."
        raise HeinzelError(msg)
    if found := _PAGE_PATH.fullmatch(parts.path):
        return found.group(1)
    if parts.path == _VIEWPAGE:
        ids = parse_qs(parts.query).get("pageId", [])
        if len(ids) == 1 and _ID.fullmatch(ids[0]):
            return ids[0]
    if parts.path.startswith("/wiki/x/"):
        msg = f"{ref!r} is a short link; open it and pass the page URL it leads to, or the page id."
        raise HeinzelError(msg)
    msg = (
        f"{ref!r} is not a Confluence page URL; pass one like {site_url}/wiki/spaces/WID/pages/123456, or the page id."
    )
    raise HeinzelError(msg)


def _document(ref: str, site: str, page_id: str, page: dict[str, Any]) -> Document:
    try:
        title = str(page["title"])
        body = cast("dict[str, Any]", cast("dict[str, Any]", page["body"])["storage"])
        storage = str(body["value"])
        version = page.get("version")
        number = cast("dict[str, Any]", version).get("number") if isinstance(version, dict) else None
        links = page.get("_links")
        webui = cast("dict[str, Any]", links).get("webui") if isinstance(links, dict) else None
    except (KeyError, TypeError):
        msg = "Confluence's answer lacks the title or body of a page; is atlassian.site_url an Atlassian Cloud site?"
        raise HeinzelError(msg) from None
    # Built on the configured site, never taken whole from the answer.
    path = clean(webui) if isinstance(webui, str) and webui.startswith("/") and not webui.startswith("//") else None
    url = f"{site}/wiki{path}" if path else f"{site}{_VIEWPAGE}?pageId={page_id}"
    return Document(
        ref=ref,
        title=clean(title),
        text=clean(storage_to_text(storage)),
        url=url,
        revision=str(number) if number is not None else None,
    )
