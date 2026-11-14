"""The AI access option of a plugin's `ai-models` adapter, derived by the kernel (ADR 0159).

`status` asks the plugin for its models (with a timeout, the last good list when that fails) and reports them; it
never calls a model. `test` builds the chosen model through the plugin and sends one short request through Pydantic
AI, capped at one request. The plugin owns its endpoint, its authentication and its tokens; this option never sees
them, and keeps only what the plugin says in a failure message, cleaned and cut short.
"""

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Any

from heinzel import AccessStatus, AccessTest, Config, HeinzelError
from heinzel.kernel import AiModelOptions, AiModelsSource, clean_text
from heinzel.kernel._ai_models import (
    ModelOffer,
    ModelUnavailableError,
    build_model,
    choose,
    effort_settings,
    list_offers,
    mark_answered,
)
from heinzel.modules.ai.access._models import AccessSettings

TEST_INSTRUCTIONS = "Reply with the single word OK."
TEST_TIMEOUT = 60.0
PROBE_TOKENS = 16
"""The most a model may write answering `hz ai access test --all`: one word is asked for."""
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
        efforts = offer.efforts if offer.thinking is not None else []
        return status.model_copy(update={"detail": detail, "model": offer.id, "efforts": efforts})

    def test(self) -> AccessTest:
        """One short request to the chosen model through the plugin's own client; a failure is a result."""
        started = time.monotonic()
        note = ""
        try:
            source = self._source()
            # The plugin's own flags only: a person's test of the chosen model may clear a mark an earlier test left.
            offers, note = list_offers(source, raw=True)
            settings = self._settings()
            offer = choose(offers, settings.plugin_model, settings.plugin_effort)
            own = getattr(source.adapter, "test", None)
            if callable(own):  # the plugin's own live check, when it has one (interface 1.0, optional)
                return self._cleared(source, self._own_test(own, offer.id, started))
            model = build_model(source, offer)
        except ModelUnavailableError as exc:
            fix = f"hz system config set ai.access.plugin_model {exc.others[0]}" if exc.others else ""
            return AccessTest(ok=False, detail=str(exc)[:DETAIL_LIMIT], fix=fix)
        except HeinzelError as exc:
            detail = str(exc) + (f"; {note}" if note else "")
            return AccessTest(ok=False, detail=detail[:DETAIL_LIMIT])
        result = _ask(model, offer.id, effort_settings(offer, settings.plugin_effort), TEST_TIMEOUT, started)
        return self._cleared(source, result)

    @staticmethod
    def _cleared(source: AiModelsSource, result: AccessTest) -> AccessTest:
        """`result`, after an answer cleared its model's mark; a failure of the single check marks nothing."""
        if result.ok and result.model and not result.skipped:
            mark_answered(source, result.model, answered=True)
        return result

    def test_model(self, model_id: str, timeout: float) -> AccessTest:
        """One tiny request to `model_id`, within `timeout` seconds, and keep whether it answered for the picker.

        A model the plugin marks unavailable, or no longer lists, is skipped: no request, nothing kept. A model that
        answers clears an earlier mark; one that fails or takes too long is marked unavailable (ADR 0159).
        """
        started = time.monotonic()
        try:
            source = self._source()
            offers, _note = list_offers(source, raw=True)
        except HeinzelError as exc:
            return AccessTest(ok=False, detail=str(exc)[:DETAIL_LIMIT], model=model_id, skipped=True)
        offer = next((o for o in offers if o.id == model_id), None)
        if offer is None or not offer.available:
            why = "the plugin doesn't offer it now" if offer is None else "the plugin marks it unavailable"
            return AccessTest(ok=False, detail=f"{why}; not tested", model=model_id, skipped=True)
        if _lingering() >= MAX_LINGERING:
            why = f"{MAX_LINGERING} earlier requests still haven't returned; try again later"
            return AccessTest(ok=False, detail=f"{why}; not tested", model=model_id, skipped=True)
        result = _bounded(lambda: self._probe(source, offer, timeout, started), timeout + 1.0)
        if result is None:
            result = AccessTest(
                ok=False,
                detail=f"no answer within {timeout:g}s",
                model=model_id,
                duration_seconds=time.monotonic() - started,
            )
        result = result.model_copy(update={"model": model_id})
        if not result.skipped:  # the plugin's own check may say it didn't ask: then nothing was learned
            mark_answered(source, model_id, answered=result.ok, offered=[o.id for o in offers])
        return result

    def _probe(self, source: AiModelsSource, offer: ModelOffer, timeout: float, started: float) -> AccessTest:
        """The plugin's own check of `offer`, else one request through its model capped at `PROBE_TOKENS`."""
        own = getattr(source.adapter, "test", None)
        if callable(own):
            return self._own_test(own, offer.id, started)
        try:
            model = build_model(source, offer)
        except HeinzelError as exc:
            return AccessTest(ok=False, detail=str(exc)[:DETAIL_LIMIT], model=offer.id)
        return _ask(model, offer.id, {"max_tokens": PROBE_TOKENS}, timeout, started)

    @staticmethod
    def _own_test(own: Callable[[str], object], model_id: str, started: float) -> AccessTest:
        """The plugin's own `test(model_id)`, held to `AccessTest`; a failure is a result."""
        try:
            found = own(model_id)
            result = AccessTest.model_validate(found.model_dump() if isinstance(found, AccessTest) else found)
        except Exception as exc:  # noqa: BLE001 - a plugin's failing check is a result
            detail = clean_text(f"{type(exc).__name__}: {exc}")[:DETAIL_LIMIT]
            return AccessTest(ok=False, detail=detail, model=model_id, duration_seconds=time.monotonic() - started)
        update: dict[str, object] = {
            "detail": clean_text(result.detail)[:DETAIL_LIMIT],
            "model": result.model or model_id,
        }
        if not result.duration_seconds:
            update["duration_seconds"] = time.monotonic() - started
        return result.model_copy(update=update)


MAX_LINGERING = 24
"""How many cut-off requests may still run before `test_model` sends no more: a hung plugin's threads stay bounded."""
_live = 0
_live_lock = threading.Lock()


def _lingering() -> int:
    """How many requests of earlier checks are still running."""
    with _live_lock:
        return _live


def _bounded(call: Callable[[], AccessTest], timeout: float) -> AccessTest | None:
    """`call()` on a daemon thread, or None when it takes longer than `timeout`: a hung plugin holds one thread only.

    The wait is interruptible, so Ctrl-C stops `hz ai access test --all` between and during models. A request cut off
    may still finish later and spend tokens hz never hears of; `MAX_LINGERING` bounds how many may run at once.
    """
    found: list[AccessTest] = []
    done = threading.Event()

    def run() -> None:
        global _live  # noqa: PLW0603 - one counter of the threads this module started
        with _live_lock:
            _live += 1
        try:
            found.append(call())
        except KeyboardInterrupt:
            raise
        except BaseException as exc:  # noqa: BLE001 - e.g. a plugin's SystemExit: a failed check, never a dead thread
            detail = clean_text(f"{type(exc).__name__}: {exc}")[:DETAIL_LIMIT]
            found.append(AccessTest(ok=False, detail=detail))
        finally:
            with _live_lock:
                _live -= 1
            done.set()

    threading.Thread(target=run, name="ai-models-test", daemon=True).start()
    done.wait(timeout)
    return found[0] if found else None


def _ask(model: Any, model_id: str, extra: dict[str, Any], timeout: float, started: float) -> AccessTest:  # noqa: ANN401
    """One short request to `model` through Pydantic AI, capped at one request; a failure is a result."""
    try:
        import pydantic_ai  # noqa: PLC0415 - a heavy import, only on a test
        from pydantic_ai.settings import ModelSettings  # noqa: PLC0415
        from pydantic_ai.usage import UsageLimits  # noqa: PLC0415

        pydantic_ai.BANNER_ENABLED = False  # hz owns its output: stdout may be the MCP head's protocol
        agent = pydantic_ai.Agent(model, instructions=TEST_INSTRUCTIONS)
        run = agent.run(
            "Are you there?",
            model_settings=ModelSettings(**extra) if extra else None,  # type: ignore[typeddict-item]
            usage_limits=UsageLimits(request_limit=1),
        )
        # Its own thread and event loop: a head may call this from a thread whose loop is already running.
        result = _bounded(lambda: asyncio.run(_answer(run, timeout)), timeout + 1.0)
    except Exception as exc:  # noqa: BLE001 - the plugin's or the library's error is a failed check
        result = AccessTest(ok=False, detail=clean_text(f"{type(exc).__name__}: {exc}")[:DETAIL_LIMIT])
    if result is None:
        result = AccessTest(ok=False, detail=f"no answer within {timeout:g}s")
    return result.model_copy(update={"model": model_id, "duration_seconds": time.monotonic() - started})


async def _answer(run: Any, seconds: float) -> AccessTest:  # noqa: ANN401 - a Pydantic AI run coroutine
    """Await `run` at most `seconds` and turn its answer, or its failure, into a result."""
    try:
        async with asyncio.timeout(seconds):
            result = await run
    except Exception as exc:  # noqa: BLE001 - the plugin's or the library's error is a failed check
        return AccessTest(ok=False, detail=clean_text(f"{type(exc).__name__}: {exc}")[:DETAIL_LIMIT])
    answer = clean_text(str(result.output)).strip()
    used = result.usage
    return AccessTest(
        ok=bool(answer),
        detail=(answer or "the model returned nothing")[:DETAIL_LIMIT],
        input_tokens=used.input_tokens,
        output_tokens=used.output_tokens,
    )


class PluginModelOptions(AiModelOptions):
    """Builds the AI access option of each plugin's `ai-models` adapter, for the kernel (ADR 0159)."""

    def option(self, name: str, source: Callable[[], AiModelsSource]) -> PluginModelsAccess:
        """The option of the `ai-models` adapter `name`."""
        return PluginModelsAccess(name, source)
