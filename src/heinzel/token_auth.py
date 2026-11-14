"""Bearer tokens from a command, for an HTTP client: `CommandTokenAuth` (ADR 0159).

A company endpoint or proxy often takes a short-lived bearer token that a helper command prints, such as a single
sign-on tool's `token` subcommand. `CommandTokenAuth` runs that command (an argument list, never a shell, with a
timeout), keeps the token in memory, runs it again shortly before the token's JWT `exp` claim, and once more when the
endpoint answers 401. A helper whose own sign-in has expired prints nothing: that raises `TokenCommandError` with
the hint the helper left on stderr, and no request goes out without a token. The token is never logged, shown in an
error or in `repr`.

It is an `httpx2.Auth`, the HTTP client Pydantic AI uses, so a plugin passes it to a provider's client:

```python
from httpx2 import AsyncClient
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from heinzel.token_auth import CommandTokenAuth

# Anthropic's client wants a key; the proxy takes only the bearer token, so the placeholder key is dropped.
auth = CommandTokenAuth(["example-sso", "token"], drop=["x-api-key"])
client = AsyncClient(auth=auth)
provider = AnthropicProvider(api_key="unused", base_url="https://ai-proxy.example.com", http_client=client)
model = AnthropicModel("claude-sonnet-4-5", provider=provider)
```

Works on Python 3.12 and later. Imported on its own, so `import heinzel` doesn't load the HTTP client.
"""

import asyncio
import base64
import binascii
import json
import os
import subprocess
import threading
import time
from collections.abc import AsyncGenerator, Callable, Generator, Mapping, Sequence
from typing import Any

import httpx2

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._text import clean_text

HINT_LIMIT = 300
MAX_TOKEN = 16 * 1024
"""The longest token taken; a helper printing more printed something else."""

Run = Callable[..., "subprocess.CompletedProcess[str]"]


class TokenCommandError(HeinzelError):
    """The token command gave no usable token: sign in again with the helper, or fix the command.

    The message carries the helper's own hint from stderr, cleaned and cut short; never the token.
    """


def _expiry(token: str) -> float | None:
    """The `exp` claim of a JWT, in seconds since the epoch; none when the token isn't a JWT or has none.

    The signature isn't checked: the endpoint checks it. This only decides when to ask for a new token.
    """
    parts = token.split(".")
    if len(parts) != 3:  # noqa: PLR2004 - header, claims, signature
        return None
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims: object = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error):
        return None
    if not isinstance(claims, dict):
        return None
    exp: object = claims.get("exp")  # pyright: ignore[reportUnknownMemberType]
    return float(exp) if isinstance(exp, int | float) and not isinstance(exp, bool) else None


class CommandTokenAuth(httpx2.Auth):
    """An `httpx2.Auth` that sends `Authorization: Bearer <token>` with a token a command prints."""

    def __init__(  # noqa: PLR0913 - the command and its keyword-only knobs
        self,
        command: Sequence[str],
        *,
        timeout: float = 30.0,
        margin: float = 300.0,
        env: Mapping[str, str] | None = None,
        header: str = "Authorization",
        scheme: str = "Bearer",
        drop: Sequence[str] = (),
        run: Run = subprocess.run,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Get tokens from `command`.

        Args:
            command: The helper's argument list, e.g. `["example-sso", "token"]`; run without a shell.
            timeout: Seconds the helper may take before it counts as failed.
            margin: Seconds before the token's `exp` when a new one is fetched, so none expires in flight.
            env: The helper's environment; this process's when none.
            header: The header that carries the token.
            scheme: What goes before the token in the header; empty for the bare token.
            drop: Headers to remove from every request, such as the `x-api-key` a vendor's client insists on
                sending when the endpoint takes only the bearer token.
            run: Runs the command; tests replace it.
            clock: The time now, in seconds since the epoch; tests replace it.

        Raises:
            ValueError: If the command is empty or the timeout or margin is negative.
        """
        if not command or not all(command):
            msg = "The token command must be a non-empty list of non-empty arguments"
            raise ValueError(msg)
        if timeout <= 0 or margin < 0:
            msg = "The token command's timeout must be positive and its margin not negative"
            raise ValueError(msg)
        self._command = list(command)
        self._timeout = timeout
        self._margin = margin
        self._env = dict(env) if env is not None else None
        self._header = header
        self._scheme = scheme
        self._drop = tuple(drop)
        self._run = run
        self._clock = clock
        self._token: str | None = None
        self._expires: float | None = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        """The command, never the token."""
        return f"CommandTokenAuth({self._command[0]!r}, ...)"

    def _fetch(self) -> str:
        try:
            done = self._run(
                self._command,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=self._env if self._env is not None else dict(os.environ),
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            msg = f"The token command {self._command[0]} took longer than {self._timeout:g}s"
            raise TokenCommandError(msg) from None
        except OSError as exc:
            msg = f"The token command {self._command[0]} couldn't run: {clean_text(exc.strerror or str(type(exc)))}"
            raise TokenCommandError(msg) from None
        hint = clean_text(done.stderr or "").strip()[:HINT_LIMIT]
        said = f": {hint}" if hint else ""
        if done.returncode != 0:
            msg = f"The token command {self._command[0]} failed (exit {done.returncode}){said}"
            raise TokenCommandError(msg)
        token = (done.stdout or "").strip()
        if not token:
            msg = f"The token command {self._command[0]} printed no token; sign in with it again{said}"
            raise TokenCommandError(msg)
        if len(token) > MAX_TOKEN or any(c.isspace() or not c.isprintable() for c in token):
            msg = f"The token command {self._command[0]} printed more than one token on one line"
            raise TokenCommandError(msg)
        return token

    def _fresh(self) -> bool:
        if self._token is None:
            return False
        return self._expires is None or self._clock() < self._expires - self._margin

    def token(self, *, renew: bool = False) -> str:
        """The current token: the kept one while it is fresh, else a new one from the command.

        Args:
            renew: Fetch a new one even when the kept one looks fresh, as after a 401.

        Raises:
            TokenCommandError: If the command gives no usable token.
        """
        with self._lock:
            if renew or not self._fresh():
                self._token = None
                token = self._fetch()
                self._token, self._expires = token, _expiry(token)
            return self._token  # type: ignore[return-value]  # set just above or kept fresh

    def _authorize(self, request: httpx2.Request, token: str) -> None:
        for name in self._drop:
            request.headers.pop(name, None)
        request.headers[self._header] = f"{self._scheme} {token}" if self._scheme else token

    def sync_auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        """Send with the token; on a 401, once more with a new one.

        Yields:
            The request, with the token; again with a new one after a 401.
        """
        self._authorize(request, self.token())
        response = yield request
        if response.status_code == 401:  # noqa: PLR2004 - Unauthorized
            self._authorize(request, self.token(renew=True))
            yield request

    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        """The same, with the command run off the event loop.

        Yields:
            The request, with the token; again with a new one after a 401.
        """
        self._authorize(request, await asyncio.to_thread(self.token))
        response = yield request
        if response.status_code == 401:  # noqa: PLR2004 - Unauthorized
            self._authorize(request, await asyncio.to_thread(self.token, renew=True))
            yield request

    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, Any]:
        """The synchronous flow, for clients that call the generic one."""
        return self.sync_auth_flow(request)


__all__ = ["CommandTokenAuth", "TokenCommandError"]
