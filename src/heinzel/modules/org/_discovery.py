"""Who you are, found rather than asked: your GitHub and Google logins, this machine's time zone, your coding agent.

The vendor CLIs are the transport and own authentication (ADR 0028): `gh` for GitHub, `gws` for Google. Each lookup
is what you could run yourself, reads only your own account, and gives up quietly when the CLI is missing, signed out
or slow; then the identity step asks instead. Each function is a seam tests replace.
"""

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import available_timezones

from pydantic import BaseModel, Field, ValidationError

from heinzel.modules.org._models import CodingAgent, email_problem, github_problem

TIMEOUT = 5.0
LOCALTIME = Path("/etc/localtime")
MAX_NAME = 200


@dataclass(frozen=True, slots=True)
class Login:
    """An account a CLI is signed in with: the login or email, and the name on it."""

    id: str
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Discovered:
    """What could be found about you, and where each part came from."""

    github: Login | None = None
    google: Login | None = None
    timezone: str | None = None
    coding_agent: CodingAgent = "none"
    sources: dict[str, str] = field(default_factory=dict[str, str])


def discover() -> Discovered:
    """Everything that can be found about you on this machine and through your signed-in CLIs."""
    # gh and gws may each take a few seconds; asking both at once halves the wait in the wizard.
    with ThreadPoolExecutor(max_workers=2) as pool:
        github_future, google_future = pool.submit(github_login), pool.submit(google_login)
        timezone = local_timezone()
        github, google = github_future.result(), google_future.result()
    agent: CodingAgent = "claude-code" if claude_installed() else "none"
    sources = {
        key: source
        for key, source, found in (
            ("github", "your gh login", github),
            ("google", "your gws login", google),
            ("timezone", "this machine's clock", timezone),
            ("coding_agent", "claude on your PATH", agent == "claude-code"),
        )
        if found
    }
    return Discovered(github=github, google=google, timezone=timezone, coding_agent=agent, sources=sources)


def github_login() -> Login | None:
    """The GitHub account `gh` is signed in with: `gh api user`, else the login gh stored, read offline."""
    if (gh := shutil.which("gh")) is None:
        return None
    user = _parse(_GitHubUser, _run([gh, "api", "user"]))
    if user is not None and github_problem(user.login) is None:
        return Login(id=user.login, name=clean_name(user.name))
    stored = (_run([gh, "config", "get", "--host", "github.com", "user"]) or "").strip()
    return Login(id=stored) if stored and github_problem(stored) is None else None


def google_login() -> Login | None:
    """The Google account `gws` is signed in with, from your own People profile: primary email and name."""
    if (gws := shutil.which("gws")) is None:
        return None
    params = json.dumps({"resourceName": "people/me", "personFields": "names,emailAddresses"})
    person = _parse(_Person, _run([gws, "people", "people", "get", "--params", params]))
    if person is None:
        return None
    email = _primary([(e.metadata.primary, e.value) for e in person.email_addresses])
    if email is None or email_problem(email) is not None:
        return None
    return Login(id=email, name=clean_name(_primary([(n.metadata.primary, n.display_name) for n in person.names])))


def clean_name(name: str | None) -> str | None:
    """A display name as stored: printable characters only, at most `MAX_NAME` of them; None when nothing is left."""
    if name is None:
        return None
    printable = "".join(c for c in name if c.isprintable()).strip()
    return printable[:MAX_NAME] or None


def local_timezone() -> str | None:
    """The IANA name of this machine's time zone, from where /etc/localtime points."""
    try:
        target = str(LOCALTIME.resolve(strict=True))
    except OSError:
        return None
    _, found, name = target.partition("zoneinfo/")
    return name if found and name in available_timezones() else None


def claude_installed() -> bool:
    """Whether Claude Code is on PATH."""
    return shutil.which("claude") is not None


def _run(args: list[str]) -> str | None:
    """A CLI's standard output, or None when it fails, hangs or isn't signed in."""
    try:
        # stdin is closed: a signed-out CLI must not start a login flow or read the wizard's keystrokes.
        done = subprocess.run(  # noqa: S603 - resolved executable, fixed arguments
            args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError):  # a broken or hanging CLI means nothing was found, not a failure
        return None
    return done.stdout if done.returncode == 0 else None


class _GitHubUser(BaseModel):
    login: str
    name: str | None = None


class _Metadata(BaseModel):
    primary: bool = False


class _Email(BaseModel):
    metadata: _Metadata = _Metadata()
    value: str = ""


class _Name(BaseModel):
    metadata: _Metadata = _Metadata()
    display_name: str = Field(default="", alias="displayName")


class _Person(BaseModel):
    """The part of a People API person that names you."""

    email_addresses: list[_Email] = Field(default_factory=list[_Email], alias="emailAddresses")
    names: list[_Name] = Field(default_factory=list[_Name])


def _parse[M: BaseModel](model: type[M], text: str | None) -> M | None:
    """`text` as `model`, or None when there is none or it says something else (an error, another shape)."""
    if not text:
        return None
    try:
        return model.model_validate_json(text)
    except ValidationError:
        return None


def _primary(entries: list[tuple[bool, str]]) -> str | None:
    """The value marked primary in a People API list, else the first one."""
    chosen = next((value for primary, value in entries if primary), entries[0][1] if entries else "")
    return chosen.strip() or None
