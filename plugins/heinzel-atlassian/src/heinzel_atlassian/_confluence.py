"""The Confluence adapter of the `documents` capability (ADR 0028), read mode only, through acli.

A page comes from `acli confluence page view --id <id> --body-format storage --json`, and its storage format becomes
Markdown-flavoured text. References are page ids or page URLs on the configured site; URLs anywhere else are refused
before acli runs.
"""

import re
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from heinzel import Document, HeinzelError, clean_text
from heinzel_atlassian._acli import Acli
from heinzel_atlassian._client import AcliClient, require_site, same_site
from heinzel_atlassian._models import SITE_EXAMPLE, AtlassianSettings
from heinzel_atlassian._storage import storage_to_text

_ID = re.compile(r"^[0-9]{1,20}$")
_PAGE_PATH = re.compile(r"^/wiki/spaces/[^/]+/pages/(?:edit-v2/|edit/)?([0-9]{1,20})(?:/.*)?$")
_VIEWPAGE = "/wiki/pages/viewpage.action"
_SPACE = re.compile(r"~?[A-Za-z0-9_-]{1,255}")
_TITLE_SLUG = re.compile(r"(?:/[A-Za-z0-9%+._~-]{0,255})?")


class Confluence:
    """Reads Confluence pages of the configured site, by page id or page URL."""

    def __init__(self, settings: AtlassianSettings, acli: Acli) -> None:
        """Build the adapter; the kernel injects the settings and acli."""
        self._settings = settings
        self._acli = acli

    def read(self, ref: str) -> Document:
        """The text of the page `ref`: a page id such as `123456`, or its URL on the configured site.

        Raises:
            HeinzelError: If `ref` is neither, or names another site, the site is not set, acli is missing or logged
                out, or Confluence fails.
        """
        page_id = page_id_of(ref, self._settings.site_url)
        site = require_site(self._settings)
        args = ["confluence", "page", "view", "--id", page_id, "--body-format", "storage", "--json"]
        answer = AcliClient(self._acli, self._settings.timeout).json("Confluence", args, f"page {page_id}")
        return _document(ref, site, page_id, answer[0] if len(answer) == 1 else answer)


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


def _document(ref: str, site: str, page_id: str, answer: object) -> Document:
    """The page in acli's answer, which has the shape of Confluence's v2 page: title, body.storage, version, links.

    `id`, `version` and `_links` are optional, because acli's exact shape isn't verified; when present, `id` must be
    the page asked for and `_links.base` the configured site.

    Raises:
        HeinzelError: If the title or the storage body is missing or not text, the answer is another page, or it
            came from another site.
    """
    unexpected = "acli's answer lacks the title or storage body of a Confluence page; is acli up to date?"
    if not isinstance(answer, dict):
        raise HeinzelError(unexpected)
    page = cast("dict[str, Any]", answer)
    try:
        title = page["title"]
        storage = cast("dict[str, Any]", cast("dict[str, Any]", page["body"])["storage"])["value"]
        version = page.get("version")
        number = cast("dict[str, Any]", version).get("number") if isinstance(version, dict) else None
        links = page.get("_links")
        webui = cast("dict[str, Any]", links).get("webui") if isinstance(links, dict) else None
        base = cast("dict[str, Any]", links).get("base") if isinstance(links, dict) else None
    except (KeyError, TypeError):
        raise HeinzelError(unexpected) from None
    if not isinstance(title, str) or not isinstance(storage, str):
        raise HeinzelError(unexpected)
    returned = str(page.get("id", page_id))
    if returned != page_id:
        msg = f"acli answered page {page_id} with page {clean_text(returned)[:40]!r}; is acli up to date?"
        raise HeinzelError(msg)
    same_site("Confluence", site, base, "/wiki")
    # Built on the configured site, never taken whole from the answer; only the page's own path is used.
    prefix = "/spaces/"
    path = None
    if isinstance(webui, str) and webui.startswith(prefix):
        space, _, rest = webui[len(prefix) :].partition("/pages/")
        linked_id, _, slug = rest.partition("/")
        if _SPACE.fullmatch(space) and linked_id == page_id and _TITLE_SLUG.fullmatch(f"/{slug}" if slug else ""):
            path = webui
    url = f"{site}/wiki{path}" if path else f"{site}{_VIEWPAGE}?pageId={page_id}"
    return Document(
        ref=ref,
        title=clean_text(title),
        text=clean_text(storage_to_text(storage)),
        url=url,
        revision=str(number) if isinstance(number, int) and not isinstance(number, bool) else None,
    )
