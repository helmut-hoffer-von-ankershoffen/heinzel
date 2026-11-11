"""Sign-in lets: a connector runs its vendor's own sign-in for the person and says what it was granted (ADR 0136).

The vendor's tool still owns the sign-in and its secrets (ADR 0035, ADR 0055). A sign-in provider only starts that
tool's supported flow, reports the vendor's consent page (and a device code, when the vendor offers one), and reads
back who is signed in and which of the connector's declared grants the vendor confirms. The person's click on the
vendor's own consent screen stays the only manual step; nothing here can or may make it for them.
"""

import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, Field, field_validator, model_validator

from heinzel.kernel._capabilities import MODES, Mode
from heinzel.kernel._lets import NAME_PATTERN, Let
from heinzel.kernel._text import clean_text

GrantState = Literal["granted", "missing", "unknown"]
"""Whether the vendor confirms a grant: granted, confirmed absent, or unknown because nothing says (ADR 0136)."""
FlowState = Literal["starting", "waiting", "succeeded", "failed", "cancelled", "timed-out"]
"""Where a sign-in is: its tool starting, waiting for the person's consent, or ended one of four ways."""
FLOW_ENDED: frozenset[FlowState] = frozenset({"succeeded", "failed", "cancelled", "timed-out"})
Tool = Literal["ready", "missing", "unsupported"]
"""Whether the vendor's tool can run the sign-in: ready, not installed, or a version this connector can't drive."""
FinishOn = Literal["host", "anywhere"]
"""Where the consent page must be opened: `host`, the machine hz runs on, when the vendor sends the browser back to a
local address there; `anywhere` when it doesn't (a device code)."""
SIGN_IN_TEXT = 300
"""Characters kept of a sign-in's plain words: a sentence, never a paragraph or a tool's whole output."""
URL_TEXT = 4096
"""Characters a consent page's address may have: OAuth addresses carry their scopes, so they are long."""
_HTTPS = re.compile(r"^https://[^\s/?#@\\]+(?:[/?#]\S*)?$", re.ASCII)
"""One https address whose authority is a host only: no userinfo (`https://vendor@elsewhere/`) and no backslash."""


def _plain(value: str) -> str:
    """One clean line of a tool's words: they are data, and a terminal or a page shows them (ADR 0028)."""
    return " ".join(clean_text(value).split())[:SIGN_IN_TEXT]


class Grant(BaseModel):
    """Something a connector's sign-in can be allowed to do, in plain words, with the vendor scopes it takes.

    A connector asks for its `default` grants on a first sign-in and for any other only when the person allows it,
    usually when a feature that needs it is first used: the minimum, and nothing silently (ADR 0136).
    """

    id: str = Field(description="Its name, lowercase words joined by dashes, e.g. `calendar-write`.")
    title: str = Field(description="A short label for people, e.g. `Change your calendar`.")
    explanation: str = Field(description="What it permits, in a sentence a person decides on.")
    capability: str | None = Field(default=None, description="The capability it serves, e.g. `calendar`; none.")
    modes: list[Mode] = Field(default_factory=list[Mode], description="The capability's modes it allows: read, write.")
    scopes: list[str] = Field(default_factory=list[str], description="The vendor's names for it, shown, never secret.")
    default: bool = Field(default=False, description="Whether a first sign-in asks for it.")

    @field_validator("id")
    @classmethod
    def _name(cls, value: str) -> str:
        if not NAME_PATTERN.fullmatch(value):
            msg = f"Grant {value!r} must be lowercase words joined by dashes, such as calendar-write"
            raise ValueError(msg)
        return value

    @field_validator("title", "explanation")
    @classmethod
    def _words(cls, value: str) -> str:
        if not (cleaned := _plain(value)):
            msg = "A grant needs a title and an explanation people can read"
            raise ValueError(msg)
        return cleaned

    @field_validator("modes")
    @classmethod
    def _modes(cls, value: list[Mode]) -> list[Mode]:
        return [mode for mode in MODES if mode in value]


class GrantStatus(BaseModel):
    """One declared grant and whether the vendor confirms it."""

    grant: Grant = Field(description="The grant, as the connector declares it.")
    state: GrantState = Field(description="Granted, missing, or unknown when nothing says.")
    needed: bool = Field(description="Whether what the person uses now needs it, e.g. a bound capability's reads.")


class SignInStatus(BaseModel):
    """Who a connector's tool is signed in as and what it was granted, from the evidence its provider has."""

    signed_in: bool | None = Field(description="Whether there is a sign-in; none when only the vendor could say.")
    account: str = Field(default="", description="Who, as the tool names them; never a secret. Empty if unsaid.")
    grants: list[GrantStatus] = Field(
        default_factory=list[GrantStatus], description="Every declared grant and its state."
    )
    tool: Tool = Field(default="ready", description="Whether the vendor's tool can run a sign-in.")
    install: str = Field(default="", description="How to install the tool through its supported path, if missing.")
    problem: str = Field(
        default="", description="What needs a new sign-in, such as an expired or revoked one; empty if nothing."
    )
    live: bool = Field(default=False, description="Whether the vendor was asked; else it is local evidence only.")

    @field_validator("account", "install", "problem")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _plain(value)


class SignInProgress(BaseModel):
    """What a running sign-in can tell the person: the vendor's consent page, and a device code if it has one."""

    url: str = Field(description="The vendor's consent page, an https address.")
    code: str = Field(default="", description="A code to type on that page, for a device sign-in; empty if none.")
    finish_on: FinishOn = Field(description="`host` when the page must be opened on the machine hz runs on.")

    @field_validator("url")
    @classmethod
    def _https(cls, value: str) -> str:
        value = value.strip()
        if len(value) > URL_TEXT or not _HTTPS.fullmatch(value):
            msg = "A consent page must be one https address"
            raise ValueError(msg)
        return value

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _plain(value)


class SignInOutcome(BaseModel):
    """How a provider's sign-in ended, in words for the person; never a token."""

    state: Literal["succeeded", "failed", "cancelled", "timed-out"] = Field(description="How it ended.")
    message: str = Field(description="What happened and, if it failed, what to do.")
    account: str = Field(default="", description="Who is signed in now, if the tool says.")

    @field_validator("message", "account")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _plain(value)

    @model_validator(mode="after")
    def _explained(self) -> Self:
        if not self.message:
            msg = "A sign-in outcome needs a message"
            raise ValueError(msg)
        return self


@runtime_checkable
class SignInProvider(Protocol):
    """A connector's sign-in, built by the kernel with its dependencies injected; the vendor's tool does the work."""

    def status(self, *, live: bool) -> SignInStatus:
        """Who is signed in and which grants hold; offline unless `live`, which may ask the vendor once."""
        ...

    def sign_in(
        self, grants: frozenset[str], progress: Callable[[SignInProgress], None], stop: threading.Event
    ) -> SignInOutcome:
        """Run the vendor's sign-in asking for exactly `grants`, reporting its consent page; end soon after `stop`.

        `grants` is the complete set to hold afterwards: a vendor that replaces its scopes gets every one to keep.
        Returning soon after `stop` is part of the contract: a sign-in asked again or cancelled waits for it.
        """
        ...

    def sign_out(self) -> str:
        """Remove the tool's sign-in through its own command; what happened, in plain words."""
        ...


@dataclass(frozen=True, kw_only=True, slots=True)
class SignInLet(Let):
    """A connector's sign-in provider and the grants it can ask for (ADR 0136), named after its module."""

    factory: Callable[..., SignInProvider]
    grants: tuple[Grant, ...]

    def __post_init__(self) -> None:
        """Refuse a sign-in without grants, or with two grants of one name.

        Raises:
            ValueError: If there is no grant, or a grant name repeats.
        """
        if not self.grants:
            msg = f"The sign-in of {self.name!r} declares no grants; declare what it can ask for"
            raise ValueError(msg)
        names = [grant.id for grant in self.grants]
        if len(set(names)) != len(names):
            msg = f"The sign-in of {self.name!r} declares a grant twice: {sorted(names)}"
            raise ValueError(msg)


def grants_of(declared: Iterable[Grant]) -> tuple[Grant, ...]:
    """The declared grants as a tuple, each validated."""
    return tuple(Grant.model_validate(grant) for grant in declared)
