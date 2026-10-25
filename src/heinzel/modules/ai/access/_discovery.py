"""Which models a model API offers: its `models` listing, asked during `hz ai access test`, kept for the picker.

OpenAI-compatible endpoints (Ollama, LM Studio, vLLM, OpenRouter) and OpenAI answer `GET <base>/models` with
`{"data": [{"id": ...}]}`; Anthropic answers `GET /v1/models` the same way with its own headers. Listing models is free,
but it is a network call, so it runs only with the live test and its answer is kept in `<state_dir>/ai/models.json`
for `status`, which stays offline. A key is sent only over https, or over http to this machine.
"""

import contextlib
import ipaddress
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from heinzel.kernel import clean_text

TIMEOUT = 10.0
LIMIT = 8 * 1024 * 1024
"""Bytes of a listing read at most; a longer one is refused, not cut."""
KEEP = 200
"""Models kept of a listing at most."""
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
ANTHROPIC = "https://api.anthropic.com/v1"
OPENAI = "https://api.openai.com/v1"
FILE = "models.json"

Fetch = Callable[[str, dict[str, str]], bytes]
"""Reads a URL with headers; tests replace it so nothing reaches the network."""


def is_local(url: str) -> bool:
    """Whether `url` points at this machine: localhost or a loopback address."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    with contextlib.suppress(ValueError):
        return ipaddress.ip_address(host).is_loopback
    return False


def safe_for_key(url: str) -> bool:
    """Whether a key may go to `url`: https anywhere, http only to this machine."""
    scheme = urllib.parse.urlsplit(url).scheme
    return scheme == "https" or (scheme == "http" and is_local(url))


def fetch(url: str, headers: dict[str, str]) -> bytes:
    """GET `url` with `headers`, at most LIMIT bytes, within TIMEOUT.

    Raises:
        OSError: If the request fails.
        ValueError: If the URL isn't http or https.
    """
    if urllib.parse.urlsplit(url).scheme not in {"http", "https"}:
        msg = f"not an http or https URL: {url}"
        raise ValueError(msg)
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - the scheme is checked just above
    # A request to this machine never goes through a proxy: its key would cross the network in clear text.
    opener = LOCAL_OPENER if is_local(url) else OPENER
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            data = cast("bytes", response.read(LIMIT + 1))
    except urllib.error.HTTPError as exc:  # a status that isn't 200, a redirect among them: say which, keep nothing
        exc.close()
        msg = f"HTTP {exc.code} from {url}"
        raise OSError(msg) from None
    if len(data) > LIMIT:
        msg = f"the models listing is larger than {LIMIT} bytes"
        raise ValueError(msg)
    return data


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuses redirects: a key sent to one host never follows a redirect to another."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401, ARG002 - urllib's signature
        """Follow nothing; urllib then raises the redirect as an HTTPError."""
        return


OPENER = urllib.request.build_opener(_NoRedirect)
LOCAL_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def listing(vendor: str, base_url: str | None, key: str | None, get: Fetch = fetch) -> list[str]:
    """The model ids the API lists, cleaned, at most KEEP; empty when it lists none or can't be asked.

    Raises:
        OSError: If the request fails.
        ValueError: If the answer isn't a listing, or a key would go over plain http to another machine.
    """
    base = (base_url or (ANTHROPIC if vendor == "anthropic" else OPENAI if vendor == "openai" else "")).rstrip("/")
    if not base:
        return []
    headers = {"Accept": "application/json"}
    if key:
        if not safe_for_key(base):
            msg = f"a key goes only over https, or over http to this machine; not to {base}"
            raise ValueError(msg)
        if vendor == "anthropic" and base_url is None:
            headers |= {"x-api-key": key, "anthropic-version": "2023-06-01"}
        else:
            headers["Authorization"] = f"Bearer {key}"
    try:
        data: object = json.loads(get(f"{base}/models", headers))
    except json.JSONDecodeError as exc:
        msg = "the models listing isn't JSON"
        raise ValueError(msg) from exc
    items: object = cast("dict[str, object]", data).get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        msg = "the models listing has no `data` list"
        raise ValueError(msg)  # noqa: TRY004 - bad data from the API, like bad JSON, not a caller's mistake
    ids: list[str] = []
    for item in cast("list[object]", items):
        found = cast("dict[str, object]", item).get("id") if isinstance(item, dict) else None
        if isinstance(found, str) and MODEL_ID.fullmatch(model := clean_text(found)) and model not in ids:
            ids.append(model)
    return ids[:KEEP]


class ModelCache:
    """The last listing of each model API, in `<state_dir>/ai/models.json`, readable by this user only."""

    def __init__(self, root: Path) -> None:
        """Keep the file in `root`."""
        self.path = root / FILE

    def load(self, source: str) -> list[str]:
        """The models last listed for `source`; none when never listed or unreadable."""
        try:
            data: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        found: object = cast("dict[str, object]", data).get(source) if isinstance(data, dict) else None
        if not isinstance(found, list):
            return []
        models = cast("list[object]", found)
        return [model for model in models if isinstance(model, str) and MODEL_ID.fullmatch(model)]

    def keep(self, source: str, models: list[str]) -> None:
        """Record `models` for `source`, replacing the file atomically; never raises for a full or locked disk."""
        try:
            data: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data = cast("dict[str, Any]", data) | {source: models}
        with contextlib.suppress(OSError):
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            handle, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".models-", suffix=".json")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as file:
                    json.dump(data, file)
                Path(temporary).replace(self.path)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
