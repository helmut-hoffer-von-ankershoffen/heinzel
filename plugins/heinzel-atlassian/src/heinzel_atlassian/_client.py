"""A small client for Atlassian Cloud's REST APIs: Basic auth, JSON, and errors people can act on.

Every failure becomes a `HeinzelError` that says what happened and what to do. Messages never contain the token or the
Basic credential built from it: headers are never part of a message, and Atlassian's own message text is redacted in
case it echoes either. Every string taken from an answer passes `clean` first.
"""

import base64
import json
import re
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from typing import Any, cast
from urllib.parse import urlencode

from heinzel import Credentials, HeinzelError
from heinzel_atlassian._models import SITE_EXAMPLE, AtlassianSettings
from heinzel_atlassian._module import TOKEN
from heinzel_atlassian._transport import MAX_BYTES, Response, Transport

_CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
MESSAGE_CHARS = 300
"""Characters of Atlassian's own error text shown, cut after redaction so no part of a secret survives the cut."""
TOKEN_FIX = "hz system credential set atlassian-token"  # noqa: S105 - a command, not a secret
SCOPED = (
    "a token created with scopes only works through api.atlassian.com, which this adapter doesn't use yet: "
    "create one without scopes"
)


def clean(text: str) -> str:
    """`text` without control characters (C0 but newline and tab, DEL, C1) and bidirectional overrides.

    Text from Atlassian ends up in terminals and in agents' prompts; whoever can edit an issue or a page could
    otherwise hide or reorder what they show.
    """
    return _CONTROLS.sub("", text)


def _user_agent() -> str:
    try:
        return f"heinzel-atlassian/{version('heinzel-atlassian')}"
    except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled source tree
        return "heinzel-atlassian"


class AtlassianClient:
    """GETs JSON objects from one Atlassian Cloud site as one account."""

    def __init__(self, site_url: str, email: str, token: str, transport: Transport, timeout: float) -> None:
        """Talk to `site_url` (no trailing slash) as `email` with `token`, through `transport`."""
        self._site_url = site_url
        pair = f"{email}:{token}"
        self._basic = base64.b64encode(pair.encode()).decode("ascii")
        self._secrets = (self._basic, pair, token)
        self._transport = transport
        self._timeout = timeout

    def get(self, product: str, path: str, params: Mapping[str, str], subject: str) -> dict[str, Any]:
        """GET `path` with `params` from the site and return the JSON object it answers.

        Args:
            product: `Jira` or `Confluence`, for messages.
            path: Absolute path on the site, e.g. `/rest/api/3/search/jql`.
            params: Query parameters.
            subject: What was asked for, for messages, e.g. `project WID` or `page 123`.

        Raises:
            HeinzelError: If the site can't be reached, refuses, rate-limits or answers unexpectedly.
        """
        url = f"{self._site_url}{path}?{urlencode(params)}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {self._basic}",
            "User-Agent": _user_agent(),
        }
        try:
            response = self._transport.get(url, headers, self._timeout)
        except OSError as exc:
            reason = getattr(exc, "reason", None) or type(exc).__name__
            msg = f"Could not reach {self._site_url}: {reason}. Check the network, or atlassian.site_url."
            raise HeinzelError(msg) from None
        if len(response.body) > MAX_BYTES:
            msg = f"{product}'s answer for {subject} is larger than {MAX_BYTES} bytes."
            raise HeinzelError(msg)
        if response.status != 200:  # noqa: PLR2004 - both endpoints answer 200 OK
            raise HeinzelError(self._problem(product, subject, response))
        data = _json(product, response.body)
        if not isinstance(data, dict):
            msg = f"{product} answered {subject} with something other than an object; is {self._site_url} an "
            msg += "Atlassian Cloud site?"
            raise HeinzelError(msg)
        return cast("dict[str, Any]", data)

    def _problem(self, product: str, subject: str, response: Response) -> str:
        """What went wrong, for a person: never a secret, never a header value besides Retry-After."""
        status, headers = response.status, response.headers
        said = self._redact(clean(_message(response.body)))[:MESSAGE_CHARS]  # redacted whole, then cut
        if status == 429:  # noqa: PLR2004 - Too Many Requests
            after = headers.get("retry-after", "")
            when = f"retry after {after} seconds" if after.isdigit() else "retry in a minute"
            return f"Atlassian's rate limit is reached for {subject}: {when}."
        if status == 401:  # noqa: PLR2004 - Unauthorized
            return (
                f"{product} rejected the email and token (401: {said}); check atlassian.email, replace the token with "
                f"`{TOKEN_FIX}`; {SCOPED}."
            )
        if status == 403:  # noqa: PLR2004 - Forbidden
            need = "Browse projects on it" if product == "Jira" else "View on its space"
            return f"{product} refused {subject} (403: {said}); the account needs the permission {need}."
        if status == 404:  # noqa: PLR2004 - Not Found
            return (
                f"{product} has no {subject}, or the account can't see it: check the spelling, or ask for access "
                f"for the account in atlassian.email."
            )
        if status == 400:  # noqa: PLR2004 - Bad Request: in Jira, an unknown or invisible project
            return f"{product} refused the request for {subject} (400: {said}); check that it exists and is visible."
        return f"{product} answered {subject} with {status}: {said}."

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


def _json(product: str, body: bytes) -> Any:  # noqa: ANN401 - free-form JSON
    try:
        return json.loads(body)
    except ValueError:
        msg = f"{product}'s answer is not JSON; is atlassian.site_url an Atlassian Cloud site?"
        raise HeinzelError(msg) from None


def _message(body: bytes) -> str:
    """Atlassian's own words from an error body, whole, or a placeholder; never the raw body, which could be anything.

    Jira says `{"errorMessages": [...], "errors": {...}}`, Confluence v2 `{"errors": [{"title": ...}]}`, older
    Confluence `{"message": ...}`.
    """
    try:
        data = json.loads(body)
    except ValueError:
        return "no details"
    if not isinstance(data, dict):
        return "no details"
    found: list[str] = []
    payload = cast("dict[str, Any]", data)
    messages = payload.get("errorMessages")
    if isinstance(messages, list):
        found += [str(m) for m in cast("list[object]", messages)]
    errors = payload.get("errors")
    if isinstance(errors, dict):
        found += [str(m) for m in cast("dict[str, object]", errors).values()]
    elif isinstance(errors, list):
        for error in cast("list[object]", errors):
            if isinstance(error, dict):
                entry = cast("dict[str, Any]", error)
                found.append(str(entry.get("detail") or entry.get("title") or ""))
    if payload.get("message"):
        found.append(str(payload["message"]))
    return "; ".join(m for m in found if m) or "no details"


def missing(settings: AtlassianSettings, credentials: Credentials) -> list[tuple[str, str]]:
    """What the adapters still need before they can send a request, each with the command that provides it."""
    needs: list[tuple[str, str]] = []
    if settings.site_url is None:
        needs.append(("atlassian.site_url", f"hz system config set atlassian.site_url {SITE_EXAMPLE}"))
    if settings.email is None:
        needs.append(("atlassian.email", "hz system config set atlassian.email <your Atlassian account email>"))
    if not credentials.has(TOKEN):
        needs.append(("the atlassian-token credential", TOKEN_FIX))
    return needs


def connect(settings: AtlassianSettings, credentials: Credentials, transport: Transport) -> AtlassianClient:
    """A client for the configured site and account.

    Raises:
        HeinzelError: Before any request, if the site, the email or the token is missing; it names the commands.
    """
    token = credentials.get(TOKEN)
    needs = missing(settings, credentials)
    if needs or settings.site_url is None or settings.email is None or not token:
        what = ", ".join(name for name, _ in needs) or "the atlassian-token credential"
        fixes = "; ".join(fix for _, fix in needs) or TOKEN_FIX
        msg = f"The Atlassian adapter needs {what}; there is no anonymous access. Run: {fixes}"
        raise HeinzelError(msg)
    return AtlassianClient(settings.site_url, settings.email, token, transport, settings.timeout)
