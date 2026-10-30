"""The `pydantic-ai` option: a model API reached through Pydantic AI, with a key (ADR 0050, 0057).

Three kinds of API, by configuration in `[ai.access]`:

- a vendor's own API: `model = "anthropic:claude-sonnet-4-5"` or `"openai:gpt-5"`, as Pydantic AI names it;
- an OpenAI-compatible endpoint: `base_url = "http://localhost:11434/v1"` (Ollama), LM Studio, vLLM or OpenRouter,
  with `model` the name the endpoint knows (`llama3.1:8b`); the key is optional;
- (with a ChatGPT login, a follow-up.)

The key is the credential `ai.access/api-key` in the keychain (`hz system credential set ai.access api-key`) or the
vendor's own variable, e.g. `ANTHROPIC_API_KEY`; it goes only over https, or over http to this machine. The vendor's
client comes with an extra (`heinzel[anthropic]`, `heinzel[openai]`; endpoints use OpenAI's); the core itself imports
only `pydantic_ai`, never a vendor SDK. `status` reads configuration and the last listing only. `test` lists the
models the API offers (kept for the picker) and sends one short request, capped at one request and a few tokens.
"""

import asyncio
import concurrent.futures
import importlib.util
import os
import time
import urllib.parse
from collections.abc import Callable
from typing import Any, cast

from heinzel import AccessStatus, AccessTest, Config, CredentialLet, Credentials, CurrentUser, HeinzelError, Settings
from heinzel.kernel import clean_text
from heinzel.modules.ai.access._discovery import Fetch, ModelCache, fetch, is_local, listing, safe_for_key
from heinzel.modules.ai.access._models import AccessSettings, Bindings

NAME = "pydantic-ai"
TITLE = "Model API"
SUMMARY = "A model API through Pydantic AI: Anthropic's or OpenAI's with your key, or an OpenAI-compatible endpoint."
ENDPOINT = "openai-compatible"
"""The vendor of a configured `base_url`: any server that speaks OpenAI's API."""
VENDOR_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google-gla": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}
"""The variable each vendor's client reads its key from."""
KNOWN_VENDORS = frozenset(
    {"anthropic", "openai", "google-gla", "google-vertex", "groq", "mistral", "cohere", "bedrock"}
)
"""Model prefixes Pydantic AI knows as vendors."""
VENDOR_PACKAGES = {"anthropic": "anthropic", "openai": "openai", ENDPOINT: "openai", "google-gla": "google.genai"}
"""The client package Pydantic AI needs for a vendor; `heinzel[<extra>]` installs it."""
EXTRAS = {"anthropic": "anthropic", "openai": "openai", ENDPOINT: "openai"}
TEST_INSTRUCTIONS = "Reply with the single word OK."
TEST_OUTPUT_TOKENS = 16
DETAIL_LIMIT = 300
SET_MODEL = "hz system config set ai.access.model anthropic:claude-sonnet-4-5"
SET_KEY = "hz system credential set ai.access api-key"

ModelFactory = Callable[[str, str | None, str | None], Any]
"""Builds a Pydantic AI model from the model name, the key and the endpoint; tests pass one returning `TestModel`."""


def build_model(model: str, key: str | None, base_url: str | None = None) -> Any:  # noqa: ANN401 - lazily imported
    """The Pydantic AI model: an OpenAI-compatible one at `base_url`, else the vendor's, with `key` when given."""
    if base_url is not None:
        from pydantic_ai.models.openai import OpenAIChatModel  # noqa: PLC0415 - a heavy import, only on a test
        from pydantic_ai.providers.openai import OpenAIProvider  # noqa: PLC0415

        client = None
        if is_local(base_url):  # never through a proxy: a key would cross the network in clear text
            import httpx2  # noqa: PLC0415 - Pydantic AI's HTTP client, only for a test

            client = httpx2.AsyncClient(trust_env=False)
        # A local server needs no key, but OpenAI's client insists on one.
        endpoint = OpenAIProvider(base_url=base_url, api_key=key or "none", http_client=client)
        return OpenAIChatModel(endpoint_model(model), provider=endpoint)
    from pydantic_ai.models import infer_model  # noqa: PLC0415
    from pydantic_ai.providers import infer_provider_class  # noqa: PLC0415

    if key is None:
        return infer_model(model)

    def provider(vendor: str) -> Any:  # noqa: ANN401 - a pydantic_ai Provider
        # The vendors' providers take `api_key`; the base class doesn't declare it.
        factory = cast("Callable[..., Any]", infer_provider_class(vendor))
        return factory(api_key=key)

    return infer_model(model, provider_factory=provider)


def build_reserved_openai(key: str) -> Any:  # noqa: ANN401 - optional provider
    """Construct the pinned direct client without ambient proxies, redirects, retries or custom headers.

    Raises:
        HeinzelError: If ambient SDK headers could alter the approved request.
    """
    if os.environ.get("OPENAI_CUSTOM_HEADERS"):
        msg = "Custom OpenAI headers are not supported for reserved turns"
        raise HeinzelError(msg)
    import httpx2  # noqa: PLC0415
    from pydantic_ai.models.openai import OpenAIResponsesModel  # noqa: PLC0415
    from pydantic_ai.providers.openai import OpenAIProvider  # noqa: PLC0415

    provider = OpenAIProvider(
        api_key=key,
        base_url="https://api.openai.com/v1",
        http_client=httpx2.AsyncClient(trust_env=False, follow_redirects=False),
    )
    client = provider.client.with_options(max_retries=0)
    return OpenAIResponsesModel("gpt-4.1-mini-2025-04-14", provider=OpenAIProvider(openai_client=client))


VENDOR_HOSTS = {
    "anthropic": "api.anthropic.com",
    "openai": "api.openai.com",
    "google-gla": "generativelanguage.googleapis.com",
    "groq": "api.groq.com",
    "mistral": "api.mistral.ai",
}


def vendor_of(settings: AccessSettings) -> str:
    """`openai-compatible` with a `base_url`, else the model's known vendor prefix, e.g. `anthropic`; else empty.

    An endpoint's own names have colons too (`llama3.1:8b`): only a vendor Pydantic AI knows counts as one.
    """
    if settings.base_url:
        return ENDPOINT
    prefix = (settings.model or "").partition(":")[0]
    return prefix if ":" in (settings.model or "") and prefix in KNOWN_VENDORS else ""


def endpoint_model(model: str) -> str:
    """The name an endpoint gets: without a vendor prefix someone left on it, e.g. `openai:gpt-5` is `gpt-5`."""
    prefix, _, rest = model.partition(":")
    return rest if rest and prefix in KNOWN_VENDORS else model


def model_hosts(settings: AccessSettings) -> list[str]:
    """The host the configured API is at, for the capability matrix; none when unknown."""
    if settings.base_url:
        return [urllib.parse.urlsplit(settings.base_url).hostname or ""]
    host = VENDOR_HOSTS.get(vendor_of(settings))
    return [host] if host else []


def _installed(package: str) -> bool:
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):
        return False


def _selected_access() -> str | None:
    return Config.load().settings(Bindings, "capabilities").ai_access


def _current_model_settings() -> AccessSettings:
    return Config.load().settings(AccessSettings, "ai.access")


class PydanticAiAccess:
    """The `pydantic-ai` option of `ai-access`."""

    def __init__(  # noqa: PLR0913 - the seams tests replace, all defaulted
        self,
        settings: AccessSettings,
        credentials: Credentials,
        kernel_settings: Settings,
        *,
        build: ModelFactory = build_model,
        installed: Callable[[str], bool] = _installed,
        get: Fetch = fetch,
        binding: Callable[[], str | None] = _selected_access,
        current_settings: Callable[[], AccessSettings] = _current_model_settings,
    ) -> None:
        """Create the option; settings and credentials are injected, the rest replace the world in tests."""
        self._settings = settings
        self._credentials = credentials
        self._build = build
        self._installed = installed
        self._get = get
        self._binding = binding
        self._current_settings = current_settings
        self._cache = ModelCache(kernel_settings.state_dir / "ai")

    @property
    def _source(self) -> str:
        """Which API the cached listing is of: the endpoint, or the vendor."""
        return (self._settings.base_url or "").rstrip("/") or vendor_of(self._settings)

    def _key(self) -> tuple[str | None, str]:
        """The key from the credential, else none (the vendor's variable then applies), and where it was found."""
        stored = self._credentials.get(API_KEY)
        if stored:
            return stored, "the credential ai.access/api-key"
        variable = VENDOR_KEYS.get(vendor_of(self._settings))
        if variable and os.environ.get(variable):
            return os.environ[variable], variable
        return None, ""

    def status(self) -> AccessStatus:  # noqa: PLR0911 - one answer per thing that can be missing
        """Whether a model is configured, its client installed and a key present where one is needed; offline."""
        settings = self._settings
        model, base, vendor = settings.model, settings.base_url, vendor_of(settings)
        listed = self._cache.load(self._source) if model else []
        models: list[str] = list(dict.fromkeys([*([model] if model else []), *listed]))
        status = AccessStatus(title=TITLE, summary=SUMMARY, installed=False, models=models)
        if model is None:
            return status.model_copy(update={"detail": "no model is configured", "fix": SET_MODEL})
        if not vendor:
            fix = f"hz system config set ai.access.model openai:{model}, or set ai.access.base_url to its endpoint"
            return status.model_copy(update={"detail": f"{model} names no vendor and no endpoint", "fix": fix})
        package = VENDOR_PACKAGES.get(vendor)
        if package and not self._installed(package):
            extra = EXTRAS.get(vendor)
            fix = (
                f"uv tool install --reinstall 'heinzel[{extra}]'" if extra else f"Install the Python package {package}"
            )
            return status.model_copy(update={"detail": f"the {vendor} client ({package}) is not installed", "fix": fix})
        key, source = self._key()
        if base is not None:
            if key and not safe_for_key(base):
                detail = f"a key goes only over https, or over http to this machine; {base} is neither"
                return status.model_copy(update={"installed": True, "signed_in": False, "detail": detail})
            where = f"; key from {source}" if source else "; no key"
            # A key is optional at an endpoint: whether it answers, only a test tells.
            return status.model_copy(update={"installed": True, "detail": f"{model} at {base}{where}"})
        if not source:
            variable = VENDOR_KEYS.get(vendor)
            also = f" or set {variable}" if variable else ""
            update = {
                "installed": True,
                "signed_in": False,
                "detail": f"no API key for {vendor}",
                "fix": SET_KEY + also,
            }
            return status.model_copy(update=update)
        detail = f"{model}; key from {source}"
        return status.model_copy(update={"installed": True, "signed_in": True, "detail": detail, "billing": "api"})

    def turn_model(self, current: CurrentUser, expected_model: str) -> Any:  # noqa: ANN401 - optional Pydantic model
        """Build only the installation owner's exactly configured model using the declared credential.

        This factory does not grant the turn, enforce its limits, or validate any third-party tool.

        Raises:
            HeinzelError: If identity, model configuration or credentials cannot be established safely.
        """
        key = self._turn_key(current, expected_model)
        return self._build(expected_model, key, self._settings.base_url)

    def priced_turn_model(self, current: CurrentUser, expected_model: str) -> Any:  # noqa: ANN401 - optional provider
        """Build the host-owned pinned direct client for the internal reserved path.

        Raises:
            HeinzelError: If the configured binding is not the pinned direct model.
        """
        key = self._turn_key(current, expected_model)
        if expected_model != "openai:gpt-4.1-mini-2025-04-14" or self._settings.base_url is not None or not key:
            msg = "This model binding is not supported for reserved turns"
            raise HeinzelError(msg)
        return build_reserved_openai(key)

    def _turn_key(self, current: CurrentUser, expected_model: str) -> str | None:
        if current.principal() != current.owner():
            msg = "This AI credential belongs to the installation owner"
            raise HeinzelError(msg)
        if self._binding() != NAME:
            msg = "The selected AI access no longer matches the configured model binding"
            raise HeinzelError(msg)
        settings = self._settings
        latest = self._current_settings()
        if (latest.model, latest.base_url) != (settings.model, settings.base_url):
            msg = "The configured model or endpoint changed; reload the access service"
            raise HeinzelError(msg)
        if not settings.model or settings.model != expected_model:
            msg = "The approved model no longer matches the configured model"
            raise HeinzelError(msg)
        status = self.status()
        if not status.installed or status.signed_in is False:
            msg = "The configured model or credential is unavailable"
            raise HeinzelError(msg)
        key, _ = self._key()
        if settings.base_url is not None:
            if key and not safe_for_key(settings.base_url):
                msg = "The configured model endpoint cannot safely receive credentials"
                raise HeinzelError(msg)
            if key is None and not is_local(settings.base_url):
                msg = "A remote model endpoint requires a configured credential"
                raise HeinzelError(msg)
        elif key is None:
            msg = "The configured model credential is unavailable"
            raise HeinzelError(msg)
        return key

    def _discover(self, key: str | None) -> str:
        """List the API's models and keep them; a note on what happened, empty when they were listed."""
        vendor = vendor_of(self._settings)
        try:
            found = listing(vendor, self._settings.base_url, key, self._get)
        except (OSError, ValueError) as exc:
            return f"; models not listed: {self._redact(f'{type(exc).__name__}: {exc}', key)}"
        if found:
            self._cache.keep(self._source, found)
        return f"; {len(found)} models listed" if found else ""

    def _redact(self, text: str, key: str | None) -> str:
        variable = VENDOR_KEYS.get(vendor_of(self._settings))
        for secret in (key, os.environ.get(variable) if variable else None):
            if secret:
                text = text.replace(secret, "***")
        return clean_text(text)[:DETAIL_LIMIT]

    def test(self) -> AccessTest:
        """List the API's models, then one request with a few output tokens; a failure is a result."""
        settings = self._settings
        model, base = settings.model, settings.base_url
        if model is None:
            return AccessTest(ok=False, detail="no model is configured", fix=SET_MODEL)
        key, _ = self._key()
        if base is not None and key and not safe_for_key(base):
            return AccessTest(ok=False, detail=f"a key goes only over https, or over http to this machine: {base}")
        if not (found := self.status()).installed:  # nothing reaches the network for an option that can't run
            return AccessTest(ok=False, detail=found.detail, fix=found.fix)
        started = time.monotonic()
        listed = self._discover(key)
        try:
            import pydantic_ai  # noqa: PLC0415 - a heavy import, only on a test
            from pydantic_ai.usage import UsageLimits  # noqa: PLC0415

            pydantic_ai.BANNER_ENABLED = False  # hz owns its output: stdout may be the MCP head's protocol
            agent = pydantic_ai.Agent(self._build(model, key, base), instructions=TEST_INSTRUCTIONS)
            run = agent.run(
                "Are you there?",
                model_settings={"max_tokens": TEST_OUTPUT_TOKENS},
                usage_limits=UsageLimits(request_limit=1, output_tokens_limit=TEST_OUTPUT_TOKENS * 2),
            )
            # Its own thread and event loop: a head may call this from a thread whose loop is already running.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(asyncio.run, run).result()
        except Exception as exc:  # noqa: BLE001 - a vendor's or the library's error is a failed check
            detail = self._redact(f"{type(exc).__name__}: {exc}", key) + listed
            return AccessTest(ok=False, detail=detail[:DETAIL_LIMIT], duration_seconds=time.monotonic() - started)
        answer = clean_text(str(result.output)).strip()
        used = result.usage
        return AccessTest(
            ok=bool(answer),
            detail=((answer or "the model returned nothing") + listed)[:DETAIL_LIMIT],
            model=model,
            duration_seconds=time.monotonic() - started,
            input_tokens=used.input_tokens,
            output_tokens=used.output_tokens,
        )


API_KEY = CredentialLet(
    name="api-key",
    purpose="An API key for the model API `[ai.access]` names: from the vendor's console (e.g. console.anthropic.com, "
    "platform.openai.com) or the endpoint's own; optional for a local server",
)
