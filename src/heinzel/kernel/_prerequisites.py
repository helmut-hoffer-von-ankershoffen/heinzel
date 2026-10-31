"""Implementation-free declarations for external tools, shared by Core and plugin modules (ADR 0112)."""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field

from heinzel.kernel._lets import Let
from heinzel.kernel._services import ServiceContract

PrerequisiteContext = Literal["install", "setup", "feature"]
PREREQUISITE_CONTEXTS: tuple[PrerequisiteContext, ...] = ("install", "setup", "feature")
"""From the narrowest context to the widest: a context also selects the declarations of the narrower ones."""
PrerequisiteStatus = Literal["available", "missing", "incompatible", "manual", "installed", "failed", "skipped"]
PREREQUISITE_READY = frozenset({"available", "installed", "skipped"})
"""Statuses after which a tool needs nothing from the person."""
_EXECUTABLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9@+_.-]*(?:/[a-z0-9][a-z0-9@+_.-]*){0,2}\Z")
_VERSION_WORD = re.compile(r"[A-Za-z0-9._=-]+\Z")
VERSION_PROBES = frozenset({"--version", "-version", "-V", "version"})
"""How a probe starts: it asks for a version and nothing else, so a declaration cannot make `check` run code."""
_HIDDEN = frozenset({"Cc", "Cf", "Zl", "Zp"})


def _plain(text: str, what: str) -> None:
    """Refuse control, format (bidi, zero-width) and line-separating characters in text a terminal or page will show.

    Raises:
        ValueError: If `text` has one.
    """
    if any(unicodedata.category(char) in _HIDDEN for char in text):
        msg = f"{what} must be one line of plain text, without control or invisible characters."
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Homebrew:
    """One named formula or cask; its dependencies are resolved by Homebrew, without a shell script."""

    package: str
    cask: bool = False

    def __post_init__(self) -> None:
        if not _PACKAGE.fullmatch(self.package):
            msg = "A Homebrew package must be a formula or cask name, optionally qualified by its tap."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ManualInstall:
    """Owner-provided instructions and an optional HTTPS link; these are displayed, never executed."""

    instructions: str
    url: str = ""

    def __post_init__(self) -> None:
        if not self.instructions.strip():
            msg = "Manual installation instructions must explain what the person needs to install."
            raise ValueError(msg)
        _plain(self.instructions, "Manual installation instructions")
        if self.url:
            if not (self.url.isascii() and self.url.isprintable()) or " " in self.url:
                msg = "A manual installation link must use HTTPS without embedded credentials."
                raise ValueError(msg)
            parsed = urlsplit(self.url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                msg = "A manual installation link must use HTTPS without embedded credentials."
                raise ValueError(msg)

    @property
    def text(self) -> str:
        """The instructions with the link, as one displayable line."""
        return f"{self.instructions.strip()} {self.url}".strip()


@dataclass(frozen=True, kw_only=True, slots=True)
class PrerequisiteLet(Let):
    """A module's external executable, its purpose, and the context in which the owner needs it."""

    purpose: str
    linux: ManualInstall
    macos: Homebrew | ManualInstall | None = None
    title: str = ""
    when: PrerequisiteContext = "setup"
    version_args: tuple[str, ...] = ("--version",)
    minimum_version: str | None = None
    condition: str = ""
    needed: Callable[..., bool] | None = None

    def __post_init__(self) -> None:
        _check_identity(self)
        _check_probe(self)
        _check_condition(self)


def _check_identity(let: PrerequisiteLet) -> None:
    """Reject a name that is not a bare command, a missing purpose or an unknown context.

    Raises:
        ValueError: If the declaration is invalid.
    """
    if not _EXECUTABLE.fullmatch(let.name):
        msg = "A prerequisite executable must be a command name, without a path or arguments."
        raise ValueError(msg)
    if not let.purpose.strip():
        msg = "A prerequisite must explain its purpose."
        raise ValueError(msg)
    for what, text in (("A prerequisite title", let.title), ("A purpose", let.purpose), ("A condition", let.condition)):
        _plain(text, what)
    if let.when not in PREREQUISITE_CONTEXTS:
        msg = "A prerequisite context must be install, setup or feature."
        raise ValueError(msg)


def _check_probe(let: PrerequisiteLet) -> None:
    """Reject a version probe that asks for anything but a version, and a minimum version that is no version.

    Raises:
        ValueError: If the declaration is invalid.
    """
    first, *rest = let.version_args or ("",)
    if first not in VERSION_PROBES or not all(_VERSION_WORD.fullmatch(word) for word in rest):
        msg = "A prerequisite probes its version with `--version`, `-version`, `-V` or `version` and plain words."
        raise ValueError(msg)
    if let.minimum_version is not None:
        try:
            Version(let.minimum_version)
        except InvalidVersion as error:
            msg = "A prerequisite minimum version must be a valid version."
            raise ValueError(msg) from error


def _check_condition(let: PrerequisiteLet) -> None:
    """Reject a predicate that has no wording for the documentation.

    Raises:
        ValueError: If the condition is not explained.
    """
    if let.needed is not None and not let.condition.strip():
        msg = "A conditional prerequisite needs an explanation for its documentation."
        raise ValueError(msg)


class PrerequisiteDefinition(BaseModel):
    """Portable metadata for people, setup and catalogs; it contains no executable condition callback."""

    module: str = Field(description="The qualified module that owns this requirement.")
    plugin: str | None = Field(default=None, description="Owning plugin distribution; none for Core.")
    name: str = Field(description="Executable name.")
    title: str = Field(description="Human-readable tool name.")
    purpose: str = Field(description="Why the owner needs this tool.")
    when: PrerequisiteContext = Field(description="Installation, setup or explicitly selected feature context.")
    condition: str = Field(default="", description="Human-readable condition, without executing provider code.")
    macos: Homebrew | ManualInstall | None = Field(description="macOS package recipe or manual guidance.")
    linux: ManualInstall = Field(description="Instructions Linux users carry out themselves.")
    minimum_version: str | None = Field(default=None, description="Oldest compatible version, when declared.")


class PrerequisiteCheck(BaseModel):
    """An observed tool state, separate from Python package installation and account authentication."""

    requirement: PrerequisiteDefinition = Field(description="The selected module-owned declaration.")
    status: PrerequisiteStatus = Field(description="Observed readiness or the outcome of an explicit action.")
    path: str | None = Field(default=None, description="Resolved usable executable, when present.")
    version: str | None = Field(default=None, description="Locally observed version, when recognized.")
    detail: str = Field(default="", description="What was checked or what remains to do.")
    automatic: bool = Field(
        default=False, description="Whether `install` would run a package manager for this tool on this machine."
    )


class PrerequisiteReport(BaseModel):
    """One context's actual readiness; instructions and installer exit codes do not establish success."""

    ready: bool = Field(description="Whether every selected requirement has a verified compatible executable.")
    checks: list[PrerequisiteCheck] = Field(description="Per-owner results, including pending manual work.")

    @property
    def unmet(self) -> list[PrerequisiteCheck]:
        """The checks whose tool still needs something from the person."""
        return [check for check in self.checks if check.status not in PREREQUISITE_READY]


class PrerequisiteService(ServiceContract):
    """Shared planning and explicit installation capability; the implementation belongs outside the kernel."""

    def definitions(self, module: str | None = None) -> list[PrerequisiteDefinition]:
        """Export owner metadata without probing tools or evaluating activation predicates."""
        raise NotImplementedError

    def check(self, module: str | None = None, *, context: PrerequisiteContext = "setup") -> PrerequisiteReport:
        """Check applicable tools locally without installation or authentication."""
        raise NotImplementedError

    def install(self, module: str | None = None, *, context: PrerequisiteContext = "setup") -> PrerequisiteReport:
        """In an authorized setup flow, install missing macOS tools or return Linux manual instructions."""
        raise NotImplementedError
