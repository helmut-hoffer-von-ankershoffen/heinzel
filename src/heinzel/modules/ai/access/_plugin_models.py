"""The AI access option of a plugin's `ai-models` adapter, derived by the kernel (ADR 0159).

`status` asks the plugin for its models (with a timeout, the last good list when that fails) and reports them; it
never calls a model. `test` builds the chosen model through the plugin and sends one short request through Pydantic
AI, capped at one request. The plugin owns its endpoint, its authentication and its tokens; this option never sees
them, and keeps only what the plugin says in a failure message, cleaned and cut short.
"""

import asyncio
import concurrent.futures
import time
from collections.abc import Callable

from heinzel import AccessStatus, AccessTest, Config, HeinzelError
from heinzel.kernel import AiModelOptions, AiModelsSource, clean_text
from heinzel.kernel._ai_models import ModelUnavailableError, build_model, choose, effort_settings, list_offers
from heinzel.modules.ai.access._models import AccessSettings

TEST_INSTRUCTIONS = "Reply with the single word OK."
TEST_TIMEOUT = 60.0
DETAIL_LIMIT = 300
SET_MODEL = "hz system config set ai.access.plugin_model <model>"
ENGINE_RUNNER = "engine"
"""The agent runner that runs coding handoffs on hz's engine, so choosing a plugin's models binds it when installed."""


def _current_settings() -> AccessSettings:
    """`[ai.access]` as the files say now: `hz ui serve` keeps running while a terminal picks another model."""
    return Config.load().settings(AccessSettings, "ai.access")


def _text(value: object, fallback: str, limit: int) -> str:
    """A plugin's optional `title` or `summary` attribute, cleaned, or `fallback`."""
    return clean_text(value)[:limit] if isinstance(value, str) and value.strip() else fallback


class PluginModelsAccess:
    """The `ai-access` option of one plugin's `ai-models` adapter: listing and a live check, never the token."""

    def __init__(
        self,
        name: str,
        source: Callable[[], AiModelsSource],
        settings: Callable[[], AccessSettings] = _current_settings,
    ) -> None:
        """Create the option of the adapter `name`, which `source` builds on first use."""
        self._name = name
        self._source = source
        self._settings = settings

    def status(self) -> AccessStatus:
        """The plugin's models, the one hz uses and whether it can; asks the plugin's cache, calls no model."""
        try:
            source = self._source()
        except HeinzelError as exc:
            return AccessStatus(title=self._name, summary="", installed=False, detail=str(exc)[:DETAIL_LIMIT])
        adapter = source.adapter
        title = _text(getattr(adapter, "title", None), self._name, 64)
        summary = _text(
            getattr(adapter, "summary", None), f"Models {source.distribution} reaches its own way, run by hz.", 200
        )
        offers, note = list_offers(source)
        status = AccessStatus(
            title=title,
            summary=summary,
            installed=bool(offers),
            models=[o.id for o in offers],
            unavailable_models=[o.id for o in offers if o.available is False],
            efforts=sorted({e for o in offers for e in o.efforts}, key=["low", "medium", "high", "xhigh", "max"].index),
            billing="api",
            core_engine=True,
            runner=ENGINE_RUNNER,
        )
        if not offers:
            return status.model_copy(update={"detail": note or "the plugin offers no models"})
        wanted = self._settings().plugin_model
        try:
            offer = choose(offers, wanted)
        except ModelUnavailableError as exc:
            fix = f"hz system config set ai.access.plugin_model {exc.others[0]}" if exc.others else ""
            return status.model_copy(update={"detail": str(exc), "fix": fix})
        detail = f"{offer.label}; {len(offers)} models" + (f"; {note}" if note else "")
        return status.model_copy(update={"detail": detail, "model": offer.id})

    def test(self) -> AccessTest:
        """One short request to the chosen model through the plugin's own client; a failure is a result."""
        started = time.monotonic()
        note = ""
        try:
            source = self._source()
            offers, note = list_offers(source)
            settings = self._settings()
            offer = choose(offers, settings.plugin_model)
            model = build_model(source, offer)
        except ModelUnavailableError as exc:
            fix = f"hz system config set ai.access.plugin_model {exc.others[0]}" if exc.others else ""
            return AccessTest(ok=False, detail=str(exc)[:DETAIL_LIMIT], fix=fix)
        except HeinzelError as exc:
            detail = str(exc) + (f"; {note}" if note else "")
            return AccessTest(ok=False, detail=detail[:DETAIL_LIMIT])
        try:
            import pydantic_ai  # noqa: PLC0415 - a heavy import, only on a test
            from pydantic_ai.settings import ModelSettings  # noqa: PLC0415
            from pydantic_ai.usage import UsageLimits  # noqa: PLC0415

            pydantic_ai.BANNER_ENABLED = False  # hz owns its output: stdout may be the MCP head's protocol
            agent = pydantic_ai.Agent(model, instructions=TEST_INSTRUCTIONS)
            extra = effort_settings(offer, settings.plugin_effort)
            run = agent.run(
                "Are you there?",
                model_settings=ModelSettings(**extra) if extra else None,  # type: ignore[typeddict-item]
                usage_limits=UsageLimits(request_limit=1),
            )
            # Its own thread and event loop: a head may call this from a thread whose loop is already running.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(asyncio.run, asyncio.wait_for(run, TEST_TIMEOUT)).result()
        except Exception as exc:  # noqa: BLE001 - the plugin's or the library's error is a failed check
            detail = clean_text(f"{type(exc).__name__}: {exc}")[:DETAIL_LIMIT]
            return AccessTest(ok=False, detail=detail, model=offer.id, duration_seconds=time.monotonic() - started)
        answer = clean_text(str(result.output)).strip()
        used = result.usage
        return AccessTest(
            ok=bool(answer),
            detail=(answer or "the model returned nothing")[:DETAIL_LIMIT],
            model=offer.id,
            duration_seconds=time.monotonic() - started,
            input_tokens=used.input_tokens,
            output_tokens=used.output_tokens,
        )


class PluginModelOptions(AiModelOptions):
    """Builds the AI access option of each plugin's `ai-models` adapter, for the kernel (ADR 0159)."""

    def option(self, name: str, source: Callable[[], AiModelsSource]) -> PluginModelsAccess:
        """The option of the `ai-models` adapter `name`."""
        return PluginModelsAccess(name, source)
