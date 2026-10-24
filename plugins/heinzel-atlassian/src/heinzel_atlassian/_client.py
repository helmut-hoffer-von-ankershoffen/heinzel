"""Runs acli for the adapters and turns what it prints into JSON, or into errors people can act on.

Every failure becomes a `HeinzelError` that says what happened and what to do: acli missing or logged out names
`acli jira auth login` (or `acli confluence auth login`). The message is built from the adapter's own words only;
acli's error text decides which message, and goes to the debug log alone (ADR 0035, statement 5), cleaned of
controls and escape sequences, redacted of anything that looks like a credential, and only then cut, so a cut can't
leave the start of a secret behind.
"""

import json
import logging
import re
from collections.abc import Sequence
from typing import Any, Literal
from urllib.parse import urlsplit

from heinzel import HeinzelError, clean_text
from heinzel_atlassian._acli import MAX_BYTES, MINIMUM_VERSION, Acli, AcliTimeoutError, Completed
from heinzel_atlassian._models import SITE_EXAMPLE, AtlassianSettings

type Product = Literal["Jira", "Confluence"]

INSTALL = "https://developer.atlassian.com/cloud/acli/guides/install-acli/"
LOGIN: dict[Product, str] = {"Jira": "acli jira auth login", "Confluence": "acli confluence auth login"}
SWITCH: dict[Product, str] = {"Jira": "acli jira auth switch", "Confluence": "acli confluence auth switch"}
GATEWAY = "https://api.atlassian.com/ex/"
_SITE_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.atlassian\.net")
MESSAGE_CHARS = 300
"""Characters of acli's own error text logged, cut after redaction."""
DEBUG_HINT = "acli's own message is in the debug log: rerun with HZ_LOG_LEVEL=DEBUG"
_LOG = logging.getLogger(__name__)
SITE_FIX = f"hz system config set atlassian.site_url {SITE_EXAMPLE}"
_ESCAPES = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])")
_KEY = (
    r"[A-Za-z0-9_.-]*(?:token|password|passwd|secret|authorization|api[_-]?key|session|cookie)[A-Za-z0-9_]*"
    r"\\?[\"']?"
)
_SECRETS = (
    (re.compile(r"(?i)(https?://)[^/\s@]+@"), r"\1***@"),  # user information in a URL
    (re.compile(r"\bAT[A-Z]{2}T[A-Za-z0-9_\-=+/]{8,}"), "***"),  # Atlassian tokens: ATATT…, ATCTT…, ATOAT…
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), "***"),  # a JWT
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]+"), r"\1 ***"),
    (  # key = "value", also access_token, refreshToken, x-api-key, client_secret, JSESSIONID, and JSON escaped in JSON
        re.compile(rf"(?i)({_KEY}\s*[:=]\s*)(\\?[\"'])(.*?)\2"),
        r"\1\2***\2",
    ),
    (re.compile(rf"(?i)({_KEY}\s*[:=]\s*)[^\s\"',;}}\\]+"), r"\1***"),  # key = value
)
# HTTP statuses count only as whole numbers: `WID-403` or page 4013377 are no status.
_STATUS = r"(?<![\w-]){}(?![\w-])"
_UNAUTHORIZED = re.compile(rf"(?i)\bunauthori[sz]ed\b|\bnot (?:logged in|authenticated)\b|{_STATUS.format(401)}")
_RATE_LIMITED = re.compile(rf"(?i)\brate.?limit|\btoo many requests\b|{_STATUS.format(429)}")
_FORBIDDEN = re.compile(rf"(?i)\bforbidden\b|\b(?:do|does) not have permission\b|{_STATUS.format(403)}")
_NOT_FOUND = re.compile(rf"(?i)\bnot found\b|\bdoes(?:n't| not) exist\b|{_STATUS.format(404)}")


def redact(text: str) -> str:
    """`text` with anything shaped like a token, a JWT or an authorization value replaced by `***`."""
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def said(stream: bytes) -> str:
    """Acli's own words from `stream`: decoded, without escape sequences and controls, redacted, then cut."""
    text = _ESCAPES.sub("", stream.decode("utf-8", errors="replace"))
    text = redact(clean_text(text)).strip()
    text = text.removeprefix("✗").strip().removeprefix("Error:").strip()
    if len(text) > MESSAGE_CHARS:
        text = text[:MESSAGE_CHARS] + "…"
    return text or "no details"


def same_site(product: Product, site: str, reported: object, path: str) -> None:
    """Refuse an answer that says it came from another site than `site`, where links point and page URLs must be.

    `reported` is the answer's own link: a Jira issue's `self` (`path="/rest"`) or a Confluence page's `_links.base`
    (`path="/wiki"`). acli reads whichever site it is logged in to, so a mismatch means it is logged in elsewhere. An
    answer through Atlassian's gateway (`https://api.atlassian.com/ex/<product>/<cloud id>/...`, after a browser login)
    names no site and is accepted, as is an answer without the field.

    Raises:
        HeinzelError: If `reported` names another site; it names both fixes.
    """
    if not isinstance(reported, str) or not reported:
        return
    if reported == f"{site}{path}" or reported.startswith((f"{site}{path}/", f"{GATEWAY}{product.lower()}/")):
        return
    host = urlsplit(reported).hostname or ""
    if _SITE_HOST.fullmatch(host):  # only a host the setting would accept is suggested
        msg = (
            f"acli answered from {host}, not {site}: it is logged in to another site. Switch it with "
            f"`{SWITCH[product]}`, or name that site: hz system config set atlassian.site_url https://{host}"
        )
    else:
        msg = f"acli answered from another site than {site}. Switch it with `{SWITCH[product]}`."
    raise HeinzelError(msg)


def require_site(settings: AtlassianSettings) -> str:
    """The configured site; links are built on it.

    Raises:
        HeinzelError: If it isn't set; it names the command.
    """
    if settings.site_url is None:
        msg = f"The Atlassian adapter needs atlassian.site_url, the site acli is logged in to. Run: {SITE_FIX}"
        raise HeinzelError(msg)
    return settings.site_url


class AcliClient:
    """Runs one acli read and returns the JSON it prints."""

    def __init__(self, acli: Acli, timeout: float) -> None:
        """Run commands through `acli`, each stopped after `timeout` seconds."""
        self._acli = acli
        self._timeout = timeout

    def json(self, product: Product, args: Sequence[str], subject: str) -> list[Any]:
        """Run `acli <args>` and parse its standard output as one or more JSON documents, in order.

        Args:
            product: `Jira` or `Confluence`, for messages and the login command they name.
            args: The acli subcommand and its flags, ending in `--json`.
            subject: What was asked for, for messages, e.g. `project WID` or `page 123`.

        Raises:
            HeinzelError: If acli is missing, logged out, refuses, takes too long or prints something unexpected.
        """
        login = LOGIN[product]
        try:
            found = self._acli.version()
            if found is not None and found < MINIMUM_VERSION:
                msg = (
                    f"acli {_dotted(found)} is older than {_dotted(MINIMUM_VERSION)}, which the Atlassian adapter "
                    f"was written against; upgrade it ({INSTALL})."
                )
                raise HeinzelError(msg)
            done = self._acli.run(args, self._timeout)
        except FileNotFoundError:
            msg = f"acli, Atlassian's CLI, is not on PATH. Install it ({INSTALL}), then log in: {login}"
            raise HeinzelError(msg) from None
        except AcliTimeoutError:
            msg = (
                f"acli did not answer for {subject} within {self._timeout:g} seconds and was stopped; check the "
                "network, ask for a shorter window, or raise atlassian.timeout."
            )
            raise HeinzelError(msg) from None
        except (OSError, RuntimeError) as exc:  # RuntimeError: no home directory to run acli in
            msg = f"acli could not be started ({type(exc).__name__}); check its installation ({INSTALL})."
            raise HeinzelError(msg) from None
        if done.overflow:
            msg = f"acli printed more than {MAX_BYTES} bytes for {subject}; ask for a shorter window."
            raise HeinzelError(msg)
        if done.returncode != 0:
            raise HeinzelError(_problem(product, subject, done))
        try:
            return documents(done.stdout.decode("utf-8"))
        except ValueError:  # UnicodeDecodeError is one too
            msg = f"acli's answer for {subject} is not JSON; is acli up to date? Update it ({INSTALL})."
            raise HeinzelError(msg) from None


def _dotted(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def _skip_blank(text: str, position: int) -> int:
    """The first position at or after `position` that is not whitespace, or `len(text)`."""
    rest = text[position:]
    return position + len(rest) - len(rest.lstrip())


def documents(text: str) -> list[Any]:
    """The JSON documents in `text`, in order: one, or several back to back, as a paging CLI may print them.

    Raises:
        ValueError: If `text` holds no document, or anything that is not JSON.
    """
    decoder = json.JSONDecoder()
    found: list[Any] = []
    position = _skip_blank(text, 0)
    while position < len(text):
        value, position = decoder.raw_decode(text, position)
        found.append(value)
        position = _skip_blank(text, position)
    if not found:
        msg = "no JSON document"
        raise ValueError(msg)
    return found


def _problem(product: Product, subject: str, done: Completed) -> str:
    """What went wrong, for a person, in the adapter's own words; acli's words only choose them and go to the log."""
    words = said(done.stderr or done.stdout)
    _LOG.debug("acli failed for %s with exit status %s: %s", subject, done.returncode, words)
    if _UNAUTHORIZED.search(words):
        return f"acli is not logged in to {product}, or its login expired. Log in: {LOGIN[product]}"
    if _RATE_LIMITED.search(words):
        return f"Atlassian's rate limit is reached for {subject}; retry in a minute."
    if _FORBIDDEN.search(words):
        need = "Browse projects on it" if product == "Jira" else "View on its space"
        return f"{product} refused {subject}; the account acli is logged in as needs the permission {need}."
    if _NOT_FOUND.search(words):
        return (
            f"{product} has no {subject}, or the account acli is logged in as can't see it: check the spelling, or "
            "ask for access."
        )
    return f"acli failed for {subject} with exit status {done.returncode}; {DEBUG_HINT}."
