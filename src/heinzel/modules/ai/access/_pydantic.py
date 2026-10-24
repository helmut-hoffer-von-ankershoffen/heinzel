"""The `pydantic-ai` option: a model API reached through Pydantic AI with an API key (ADR 0050, 0057).

`[ai.access] model` names the model as Pydantic AI does, `<vendor>:<model>`, e.g. `anthropic:claude-sonnet-4-5`. The
key is the credential `ai.access/api-key` (`hz system credential set ai.access api-key`) or the vendor's own variable,
e.g. `ANTHROPIC_API_KEY`. The vendor's client comes with an extra (`heinzel[anthropic]`, `heinzel[openai]`); the core
itself imports only `pydantic_ai`, never a vendor SDK. `status` reads configuration only; `test` sends one short
request, capped at one request and a few output tokens.
"""

import importlib.util
import os
import time
from collections.abc import Callable
from typing import Any, cast

from heinzel import AccessStatus, AccessTest, CredentialLet, Credentials
from heinzel.kernel import clean_text
from heinzel.modules.ai.access._models import AccessSettings

NAME = "pydantic-ai"
TITLE = "Model API"
SUMMARY = "A model API such as Anthropic's or OpenAI's, called directly through Pydantic AI with your API key."
VENDOR_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google-gla": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}
"""The variable each vendor's client reads its key from."""
VENDOR_PACKAGES = {"anthropic": "anthropic", "openai": "openai", "google-gla": "google.genai"}
"""The client package Pydantic AI needs for a vendor; `heinzel[<extra>]` installs it."""
EXTRAS = {"anthropic": "anthropic", "openai": "openai"}
TEST_INSTRUCTIONS = "Reply with the single word OK."
TEST_OUTPUT_TOKENS = 16
DETAIL_LIMIT = 300
SET_MODEL = "hz system config set ai.access.model anthropic:claude-sonnet-4-5"
SET_KEY = "hz system credential set ai.access api-key"

ModelFactory = Callable[[str, str | None], Any]
"""Builds a Pydantic AI model from the model name and the key; tests pass one returning `TestModel`."""


def build_model(model: str, key: str | None) -> Any:  # noqa: ANN401 - a pydantic_ai Model, imported lazily
    """The Pydantic AI model for `model`, its provider given `key` when there is one, else the vendor's variable."""
    from pydantic_ai.models import infer_model  # noqa: PLC0415 - a heavy import, only on a test
    from pydantic_ai.providers import infer_provider_class  # noqa: PLC0415

    if key is None:
        return infer_model(model)

    def provider(vendor: str) -> Any:  # noqa: ANN401 - a pydantic_ai Provider
        # The vendors' providers take `api_key`; the base class doesn't declare it.
        factory = cast("Callable[..., Any]", infer_provider_class(vendor))
        return factory(api_key=key)

    return infer_model(model, provider_factory=provider)


VENDOR_HOSTS = {
    "anthropic": "api.anthropic.com",
    "openai": "api.openai.com",
    "google-gla": "generativelanguage.googleapis.com",
    "groq": "api.groq.com",
    "mistral": "api.mistral.ai",
}


def model_hosts(settings: AccessSettings) -> list[str]:
    """The host the configured model's vendor API is at, for the capability matrix; none when unknown."""
    host = VENDOR_HOSTS.get((settings.model or "").partition(":")[0])
    return [host] if host else []


def _installed(package: str) -> bool:
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):
        return False


class PydanticAiAccess:
    """The `pydantic-ai` option of `ai-access`."""

    def __init__(
        self,
        settings: AccessSettings,
        credentials: Credentials,
        build: ModelFactory = build_model,
        installed: Callable[[str], bool] = _installed,
    ) -> None:
        """Create the option; settings and credentials are injected, `build` and `installed` replace them in tests."""
        self._settings = settings
        self._credentials = credentials
        self._build = build
        self._installed = installed

    def _key(self) -> tuple[str | None, str]:
        """The key from the credential, else none (the vendor's variable then applies), and where it was found."""
        stored = self._credentials.get(API_KEY)
        if stored:
            return stored, "the credential ai.access/api-key"
        vendor = (self._settings.model or "").partition(":")[0]
        variable = VENDOR_KEYS.get(vendor)
        if variable and os.environ.get(variable):
            return None, variable
        return None, ""

    def status(self) -> AccessStatus:
        """Whether a model is configured, its vendor's client installed and a key present; calls no model."""
        model = self._settings.model
        base = AccessStatus(title=TITLE, summary=SUMMARY, installed=False, models=[model] if model else [])
        if model is None:
            return base.model_copy(update={"detail": "no model is configured", "fix": SET_MODEL})
        vendor = model.partition(":")[0]
        package = VENDOR_PACKAGES.get(vendor)
        if package and not self._installed(package):
            extra = EXTRAS.get(vendor)
            fix = (
                f"uv tool install --reinstall 'heinzel[{extra}]'" if extra else f"Install the Python package {package}"
            )
            return base.model_copy(update={"detail": f"{vendor}'s client ({package}) is not installed", "fix": fix})
        _, source = self._key()
        if not source:
            variable = VENDOR_KEYS.get(vendor)
            also = f" or set {variable}" if variable else ""
            return base.model_copy(
                update={
                    "installed": True,
                    "signed_in": False,
                    "detail": f"no API key for {vendor}",
                    "fix": SET_KEY + also,
                }
            )
        return base.model_copy(update={"installed": True, "signed_in": True, "detail": f"{model}; key from {source}"})

    def test(self) -> AccessTest:
        """One request with a few output tokens; a failure is a result."""
        model = self._settings.model
        if model is None:
            return AccessTest(ok=False, detail="no model is configured", fix=SET_MODEL)
        key, _ = self._key()
        started = time.monotonic()
        try:
            import pydantic_ai  # noqa: PLC0415 - a heavy import, only on a test
            from pydantic_ai.usage import UsageLimits  # noqa: PLC0415

            pydantic_ai.BANNER_ENABLED = False  # hz owns its output: stdout may be the MCP head's protocol
            agent = pydantic_ai.Agent(self._build(model, key), instructions=TEST_INSTRUCTIONS)
            result = agent.run_sync(
                "Are you there?",
                model_settings={"max_tokens": TEST_OUTPUT_TOKENS},
                usage_limits=UsageLimits(request_limit=1, output_tokens_limit=TEST_OUTPUT_TOKENS * 2),
            )
        except Exception as exc:  # noqa: BLE001 - a vendor's or the library's error is a failed check
            text = f"{type(exc).__name__}: {exc}"
            variable = VENDOR_KEYS.get(model.partition(":")[0])
            for secret in (key, os.environ.get(variable) if variable else None):
                if secret:
                    text = text.replace(secret, "***")
            return AccessTest(
                ok=False, detail=clean_text(text)[:DETAIL_LIMIT], duration_seconds=time.monotonic() - started
            )
        answer = clean_text(str(result.output)).strip()
        return AccessTest(
            ok=bool(answer),
            detail=answer[:DETAIL_LIMIT] or "the model returned nothing",
            model=model,
            duration_seconds=time.monotonic() - started,
        )


API_KEY = CredentialLet(
    name="api-key",
    purpose="An API key for the model `[ai.access] model` names, from its vendor's console, e.g. console.anthropic.com",
)
