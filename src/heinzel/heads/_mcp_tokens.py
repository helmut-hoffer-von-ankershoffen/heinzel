"""Bearer tokens for the streamable-HTTP MCP head: one per agent, scope `read` or `write` (ADR 0142).

`hz system serve mcp-token create` makes a token for a named actor and shows it once; only its SHA-256 is kept, in the
state directory (0700, the file 0600), with the actor, the scope, when it was made, when it expires and when it was
last used. A token is 32 random bytes, so a plain SHA-256 makes the stored file useless to whoever reads it; lookups
compare digests with `hmac.compare_digest`. This module imports no MCP code, so the CLI stays fast.
"""

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, TypeAdapter, ValidationError

from heinzel.kernel import HeinzelError

PREFIX = "hzmcp_"
"""MCP tokens start with this, so they are told apart from other tokens at a glance (and in a secret scan)."""
MAX_TOKENS = 50
ACTOR = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}\Z")
LIFETIME = timedelta(days=90)
TOUCH_EVERY = timedelta(hours=1)
"""`last_used` is written at most this often, so every request doesn't rewrite the file."""
PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600

Scope = Literal["read", "write"]
READ = "hz:read"
WRITE = "hz:write"
SCOPES: dict[Scope, tuple[str, ...]] = {"read": (READ,), "write": (READ, WRITE)}
"""What each token scope grants, as MCP access-token scopes; the head requires READ and checks WRITE per tool."""


class McpToken(BaseModel):
    """One agent's credential for the streamable-HTTP MCP head; the token itself is never stored."""

    id: str = Field(description="Short identifier, for `hz system serve mcp-token revoke`.")
    actor: str = Field(description="The agent the token belongs to, e.g. `codex-desktop`; logged with every call.")
    scope: Scope = Field(description="`read`: read-only tools only; `write`: every tool the head serves.")
    created: AwareDatetime = Field(description="When it was made.")
    expires: AwareDatetime = Field(description="When it stops working.")
    last_used: AwareDatetime | None = Field(default=None, description="When it was last used, to the hour.")
    sha256: str = Field(description="SHA-256 of the token.")


_TOKENS = TypeAdapter(list[McpToken])


def digest(token: str) -> str:
    """The SHA-256 of a token, hex."""
    return hashlib.sha256(token.encode()).hexdigest()


class McpTokens:
    """The MCP tokens of this machine, in `directory`/mcp-tokens.json."""

    def __init__(self, directory: Path, clock: Callable[[], datetime] | None = None) -> None:
        """Keep tokens in `directory`; perform no I/O yet."""
        self.directory = directory
        self.path = directory / "mcp-tokens.json"
        self._lock = directory / "mcp-tokens.lock"
        self._clock = clock or (lambda: datetime.now(UTC))

    def tokens(self) -> list[McpToken]:
        """Every token, oldest first; a missing or damaged file is none, so nothing is accepted."""
        try:
            return _TOKENS.validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            return []

    def active(self) -> list[McpToken]:
        """The tokens that have not expired."""
        now = self._clock()
        return [token for token in self.tokens() if token.expires > now]

    def create(self, actor: str, scope: Scope, lifetime: timedelta = LIFETIME) -> tuple[McpToken, str]:
        """Make a token for `actor`; the record and the token, which is shown this once.

        Raises:
            HeinzelError: If the actor name or lifetime is invalid, or MAX_TOKENS exist already.
        """
        if not ACTOR.fullmatch(actor):
            msg = "An actor is 1-40 lower-case letters, digits, dots, dashes or underscores, like `codex-desktop`"
            raise HeinzelError(msg)
        if lifetime <= timedelta(0):
            msg = "A token's lifetime must be positive, like 30d"
            raise HeinzelError(msg)
        token = PREFIX + secrets.token_urlsafe(32)
        now = self._clock()
        record = McpToken(
            id=secrets.token_hex(4), actor=actor, scope=scope, created=now, expires=now + lifetime, sha256=digest(token)
        )
        with self._locked():
            existing = self.tokens()
            if len(existing) >= MAX_TOKENS:
                msg = f"{MAX_TOKENS} MCP tokens exist already; revoke one with `hz system serve mcp-token revoke ID`"
                raise HeinzelError(msg)
            self._write([*existing, record])
        return record, token

    def check(self, token: str) -> McpToken | None:
        """The unexpired record this token belongs to, or None; notes when it was used, to the hour."""
        found, _ = self.verify(token)
        return found

    def verify(self, token: str) -> tuple[McpToken | None, Literal["ok", "unknown", "expired"]]:
        """The unexpired record of this token and `ok`, or None and why: `unknown` (or revoked) or `expired`."""
        if not token.startswith(PREFIX):
            return None, "unknown"
        presented = digest(token)
        found = None
        for record in self.tokens():  # every digest is compared, so timing doesn't tell which one matched
            if hmac.compare_digest(record.sha256, presented):
                found = record
        if found is None:
            return None, "unknown"
        if found.expires <= self._clock():
            return None, "expired"
        if found.last_used is None or self._clock() - found.last_used > TOUCH_EVERY:
            self._touch(found.id)
        return found, "ok"

    def revoke(self, which: str) -> list[McpToken]:
        """Remove the token `which` (an id), or every one with `all`; the ones removed."""
        with self._locked():
            existing = self.tokens()
            gone = [token for token in existing if which in {"all", token.id}]
            if gone:
                self._write([token for token in existing if token not in gone])
        return gone

    def _touch(self, which: str) -> None:
        try:
            with self._locked():
                now = self._clock()
                self._write([t.model_copy(update={"last_used": now}) if t.id == which else t for t in self.tokens()])
        except OSError:
            return  # only the hint of when it was used is lost

    def _write(self, tokens: list[McpToken]) -> None:
        partial = self.path.with_name(f".{self.path.name}.{os.getpid()}.{secrets.token_hex(4)}")
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, PRIVATE_FILE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(_TOKENS.dump_python(tokens, mode="json"), indent=1) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        partial.replace(self.path)

    @contextmanager
    def _locked(self) -> Generator[None]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR)
        self.directory.chmod(PRIVATE_DIR)
        descriptor = os.open(self._lock, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, PRIVATE_FILE)
        with os.fdopen(descriptor, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def tokens_for_user() -> McpTokens:
    """The MCP tokens in the user's state directory: a project's `hz.toml` can't move them.

    Raises:
        HeinzelError: If the configuration cannot be read.
    """
    from heinzel.kernel import KERNEL_SECTION, Config, Settings  # noqa: PLC0415 - only when serving or managing

    settings = Config.load(Path.home()).settings(Settings, KERNEL_SECTION)
    return McpTokens(settings.state_dir / "mcp")


LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def require_reachable_only_as_asked(host: str, *, expose: bool, allowed_hosts: tuple[str, ...] = ()) -> None:
    """Refuse an interface other than loopback unless asked for with `--expose` and the names it answers to.

    Raises:
        HeinzelError: If `host` is not loopback and `expose` is false, or `expose` names no `--allowed-host`.
    """
    if host not in LOOPBACK and not expose:
        msg = (
            f"MCP over HTTP listens on this machine only; {host} would reach other devices. Use Tailscale Serve "
            "to 127.0.0.1, or pass --expose if you mean it"
        )
        raise HeinzelError(msg)
    if expose and not allowed_hosts:
        msg = (
            "--expose needs the names other devices use to reach it, or every request is refused: "
            "--allowed-host name-or-ip:PORT (or name-or-ip:*)"
        )
        raise HeinzelError(msg)
