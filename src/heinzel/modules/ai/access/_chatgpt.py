"""The `chatgpt` option: a ChatGPT plan through OpenAI's "Sign in with ChatGPT", with Pydantic AI (ADR 0057).

OpenAI lets open-source apps use a person's ChatGPT plan instead of an API key: the person signs in in their browser
(`hz ai access login`), approves hz by name, and can limit it in ChatGPT's settings; requests count toward the plan.
The sign-in's credentials, a refresh token among them, are kept as one credential in the keychain
(`ai.access/chatgpt-login`) and refreshed there; the installation's id (`<state_dir>/ai/host-id`, not a secret) lets a
later sign-in reuse the registration. `status` reads the keychain only; `test` sends one short request. Pydantic AI's
`openai-chatgpt` provider does the protocol; hz never uses the Codex CLI's stored credentials.
"""

import asyncio
import concurrent.futures
import dataclasses
import importlib.util
import json
import time
import urllib.parse
import uuid
import webbrowser
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, cast

from heinzel import AccessStatus, AccessTest, CredentialLet, Credentials, HeinzelError, Progress, Settings
from heinzel.kernel import clean_text
from heinzel.modules.ai.access._models import AccessSettings

NAME = "chatgpt"
TITLE = "ChatGPT plan"
SUMMARY = "Your ChatGPT Plus or Pro plan, through OpenAI's Sign in with ChatGPT; requests count toward the plan."
APP_NAME = "Heinzelmännchen"
"""What people see when they approve hz in ChatGPT."""
HOSTS = ("api.openai.com", "auth.openai.com")
LOGIN = "hz ai access login"
SET_MODEL = "hz system config set ai.access.chatgpt_model <model>"
SIGN_IN_TIMEOUT = 300.0
TEST_INSTRUCTIONS = "Reply with the single word OK."
TEST_TIMEOUT = 60.0
DETAIL_LIMIT = 300
HOST_ID_FILE = "host-id"

CHATGPT_LOGIN = CredentialLet(
    name="chatgpt-login",
    purpose="The ChatGPT sign-in hz keeps for your plan: run `hz ai access login`, which signs in in your browser",
)

Run = Callable[[Coroutine[Any, Any, Any]], Any]
"""Runs a coroutine to its end on its own event loop; tests replace it."""


def run_coroutine(coroutine: Coroutine[Any, Any, Any]) -> Any:  # noqa: ANN401 - whatever the coroutine returns
    """`asyncio.run` here, so Ctrl-C cancels it at once; on a thread of its own when a loop already runs here."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def _installed(package: str) -> bool:
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):
        return False


def _load(credentials: Credentials) -> dict[str, Any] | None:
    """The stored sign-in as its fields; none when there is none or it doesn't read."""
    try:
        text = credentials.get(CHATGPT_LOGIN)
    except HeinzelError:
        return None
    if not text:
        return None
    try:
        data: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    fields = {str(key): value for key, value in cast("dict[object, Any]", data).items()}
    needed = ("client_id", "access_token", "refresh_token")
    return fields if all(isinstance(fields.get(name), str) for name in needed) else None


def writable(credentials: Credentials) -> bool:
    """Whether credentials can be stored: not when they come from the environment (no keychain)."""
    return getattr(credentials.backend, "name", "") != "environment"


class KeychainCredentialSource:
    """Pydantic AI's credential source for the ChatGPT sign-in, kept in the keychain so refreshes outlive a process.

    Without a keychain (credentials from the environment) a refresh lives in memory for the process only.
    """

    def __init__(self, credentials: Credentials) -> None:
        """Read and write the sign-in through `credentials`."""
        self._credentials = credentials

    async def load(self) -> Any:  # noqa: ANN401 - Pydantic AI's credentials, imported lazily
        """The stored sign-in.

        Raises:
            HeinzelError: If there is none.
        """
        from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentials  # noqa: PLC0415 - heavy

        fields = _load(self._credentials)
        if fields is None:
            msg = f"Not signed in to ChatGPT: run `{LOGIN}`."
            raise HeinzelError(msg)
        known = {field.name for field in dataclasses.fields(OpenAIChatGPTCredentials)}
        return OpenAIChatGPTCredentials(**{key: value for key, value in fields.items() if key in known})

    async def save(self, credentials: Any) -> None:  # noqa: ANN401 - Pydantic AI's credentials
        """Store `credentials`, replacing the earlier sign-in; nothing without a keychain."""
        if writable(self._credentials):
            self._credentials.set(CHATGPT_LOGIN, json.dumps(dataclasses.asdict(credentials)))


def _without_hint(url: str) -> str:
    """`url` without its `id_token_hint` parameter."""
    parts = urllib.parse.urlsplit(url)
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parts.query) if key != "id_token_hint"]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def host_id(root: Path) -> str:
    """This installation's stable, opaque id for OpenAI (`urn:uuid:...`), created once in `root`.

    Raises:
        HeinzelError: If it can't be kept there.
    """
    path = root / HOST_ID_FILE
    try:
        found = path.read_text(encoding="utf-8").strip()
        if found.startswith("urn:uuid:"):
            return found
    except OSError:
        pass
    made = uuid.uuid4().urn
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(made, encoding="utf-8")
    except OSError as exc:
        msg = f"Can't keep this installation's id for ChatGPT in {path}: {exc}"
        raise HeinzelError(msg) from None
    return made


class ChatGptAccess:
    """The `chatgpt` option of `ai-access`."""

    def __init__(  # noqa: PLR0913 - the seams tests replace, by keyword
        self,
        settings: AccessSettings,
        credentials: Credentials,
        kernel_settings: Settings,
        *,
        run: Run = run_coroutine,
        build: Callable[[str, KeychainCredentialSource], Any] | None = None,
        installed: Callable[[str], bool] = _installed,
    ) -> None:
        """Create the option; settings and credentials are injected, `run` and `build` replace the world in tests."""
        self._settings = settings
        self._credentials = credentials
        self._root = kernel_settings.state_dir / "ai"
        self._run = run
        self._build = build or _model
        self._installed = installed

    def status(self) -> AccessStatus:
        """Whether OpenAI's client is installed, a sign-in with plan usage is kept and a model is set; offline."""
        model = self._settings.chatgpt_model
        status = AccessStatus(title=TITLE, summary=SUMMARY, installed=True, models=[model] if model else [])
        if not self._installed("openai"):
            fix = "uv tool install --reinstall 'heinzel[openai]'"
            return status.model_copy(
                update={"installed": False, "detail": "OpenAI's client is not installed", "fix": fix}
            )
        fields = _load(self._credentials)
        if fields is None:
            return status.model_copy(update={"signed_in": False, "detail": "not signed in", "fix": LOGIN})
        if "chatgpt.tokens.use.direct" not in str(fields.get("scope", "")).split():
            detail = "signed in, but plan usage wasn't granted to hz"
            return status.model_copy(update={"signed_in": False, "detail": detail, "fix": LOGIN})
        if model is None:
            return status.model_copy(
                update={"signed_in": True, "detail": "signed in; no model is set", "fix": SET_MODEL}
            )
        kept = "" if writable(self._credentials) else "; refreshed tokens last this process only (no keychain)"
        return status.model_copy(update={"signed_in": True, "detail": f"signed in; {model}{kept}"})

    def login(self, progress: Progress, open_browser: Callable[[str], bool] = webbrowser.open) -> AccessStatus:
        """Sign in in the browser and keep the credentials in the keychain; then the option's status.

        Raises:
            HeinzelError: If OpenAI's client is missing, or the sign-in fails, is declined or takes too long.
        """
        if not self._installed("openai"):
            msg = "Signing in to ChatGPT needs OpenAI's client: uv tool install --reinstall 'heinzel[openai]'"
            raise HeinzelError(msg)
        if not writable(self._credentials):  # before the browser: a sign-in nobody can keep would be wasted
            msg = (
                "Signing in to ChatGPT needs a keychain to keep the sign-in, and credentials come from the "
                "environment here; run it where a keychain is available (`hz system credential list` says which)."
            )
            raise HeinzelError(msg)
        from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTOAuthFlow  # noqa: PLC0415 - heavy

        earlier = _load(self._credentials) or {}
        flow = OpenAIChatGPTOAuthFlow(
            app_name=APP_NAME,
            host_id=host_id(self._root),
            client_id=earlier.get("client_id"),
            id_token_hint=earlier.get("id_token"),
        )
        url = flow.authorization_url()
        # Shown only when no browser opened, and then without the earlier sign-in's id token it carries as a hint.
        where = "in the browser that opened" if open_browser(url) else f"at {_without_hint(url)}"
        minutes = int(SIGN_IN_TIMEOUT // 60)

        async def signed_in() -> Any:  # noqa: ANN401 - Pydantic AI's credentials
            return await asyncio.wait_for(flow.exchange_code_from_callback(), SIGN_IN_TIMEOUT)

        try:
            with progress.task(f"Sign in to ChatGPT {where}; waiting up to {minutes} minutes"):
                credentials = self._run(signed_in())
        except Exception as exc:  # noqa: BLE001 - declined, timed out, or the exchange failed
            msg = f"Signing in to ChatGPT didn't finish: {clean_text(f'{type(exc).__name__}: {exc}')[:DETAIL_LIMIT]}"
            raise HeinzelError(msg) from None
        try:
            self._credentials.set(CHATGPT_LOGIN, json.dumps(dataclasses.asdict(credentials)))
        except HeinzelError as exc:
            msg = f"Signed in to ChatGPT, but keeping the sign-in failed: {exc}"
            raise HeinzelError(msg) from None
        status = self.status()
        if not credentials.plan_usage_granted:
            detail = "signed in, but plan usage wasn't granted: sign in again and allow hz to use your plan"
            return status.model_copy(update={"signed_in": False, "detail": detail, "fix": LOGIN})
        return status

    def test(self) -> AccessTest:
        """One request with a few output tokens through the plan; a failure is a result."""
        found = self.status()
        if not found.signed_in or self._settings.chatgpt_model is None:
            return AccessTest(ok=False, detail=found.detail, fix=found.fix)
        started = time.monotonic()
        try:
            import pydantic_ai  # noqa: PLC0415 - a heavy import, only on a test
            from pydantic_ai.usage import UsageLimits  # noqa: PLC0415

            pydantic_ai.BANNER_ENABLED = False  # hz owns its output: stdout may be the MCP head's protocol
            model = self._build(self._settings.chatgpt_model, KeychainCredentialSource(self._credentials))
            agent = pydantic_ai.Agent(model, instructions=TEST_INSTRUCTIONS)
            # One request, within a minute; the plan's models take no output cap, and reasoning counts as output.
            asked = agent.run("Are you there?", usage_limits=UsageLimits(request_limit=1))
            result = self._run(asyncio.wait_for(asked, TEST_TIMEOUT))
        except Exception as exc:  # noqa: BLE001 - OpenAI's or the library's error is a failed check
            detail = clean_text(f"{type(exc).__name__}: {exc}")[:DETAIL_LIMIT]
            return AccessTest(ok=False, detail=detail, duration_seconds=time.monotonic() - started)
        answer = clean_text(str(result.output)).strip()
        return AccessTest(
            ok=bool(answer),
            detail=answer[:DETAIL_LIMIT] or "the model returned nothing",
            model=self._settings.chatgpt_model,
            duration_seconds=time.monotonic() - started,
        )


def _model(name: str, source: KeychainCredentialSource) -> Any:  # noqa: ANN401 - Pydantic AI's model
    """OpenAI's Responses model on the plan, its credentials read and refreshed through the keychain."""
    from pydantic_ai.models.openai import OpenAIResponsesModel  # noqa: PLC0415 - heavy, needs heinzel[openai]
    from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTProvider  # noqa: PLC0415

    return OpenAIResponsesModel(name, provider=OpenAIChatGPTProvider(credential_source=source))
