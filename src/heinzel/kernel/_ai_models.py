"""The `ai-models` interface: a plugin tells hz which models it has and hands over a Pydantic AI model (ADR 0159).

This is the whole contract between a plugin and hz for AI models, version `AI_MODELS_INTERFACE`:

- `models()` lists the models on offer as `ModelOffer`s; only `id` is required.
- `model(model_id)` returns a ready `pydantic_ai.models.Model`, built however the plugin likes.
- `test(model_id)` is optional: a plugin's own live check of one model.

How the plugin reaches its models, and how it signs in, is its own business: hz never sees it. hz runs the model
with Pydantic AI itself: its `Agent`, streaming, message history and usage. A plugin never runs an agent.

An adapter of `ai-models` is an AI access option of the same name without more code: the kernel derives the
`ai-access` adapter, so `hz ai access use <name>`, Settings and the chat's model picker work.

Compatibility: within interface 1.x, changes are additive only. `ModelOffer` gains optional fields with defaults
and ignores fields it doesn't know. The protocol gains optional methods only, which hz detects before calling.
`AI_MODELS_INTERFACE` names the version a Core implements; a plugin may check it at import.
"""

import contextlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from heinzel.kernel._agents import Effort
from heinzel.kernel._config import ModuleSettings
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._logging import log_event
from heinzel.kernel._text import clean_text

if TYPE_CHECKING:
    from heinzel.kernel._ai import AiAccess

AI_MODELS_INTERFACE = "1.0"
"""The `ai-models` interface this Core implements, `major.minor`: within a major version, changes are additive."""

MODEL_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$"
"""What a model id may be: what endpoints and proxies use (`gpt-5`, `claude-sonnet-4-5`, `team/llama3.1:8b`), with no
spaces, quotes or control characters, so it is safe in a command line, a log line and a configuration value."""

ModelId = Annotated[str, StringConstraints(pattern=MODEL_ID_PATTERN)]

Thinking = Literal["adaptive", "budget"]
"""How a model is asked to think harder, in Anthropic's Messages API: `adaptive` (`thinking: {type: adaptive}` plus
`output_config: {effort}`, Pydantic AI's `anthropic_effort`) for newer models, `budget` (`thinking: {type: enabled,
budget_tokens}`) for older ones. A plugin whose models speak another API leaves it unset and maps effort itself."""

Level = Literal["low", "medium", "high"]
"""A model's tier in its plugin's list: hz picks a model by level with `model_for_level` (ADR 0159)."""

EFFORT_BUDGETS: dict[Effort, int] = {"low": 2048, "medium": 8192, "high": 16384, "xhigh": 32768, "max": 63999}
"""The thinking budget in tokens hz asks a `budget` model for at each effort."""


class ModelOffer(BaseModel):
    """One model a plugin offers: what the chat's picker and Settings show, and what `model` takes.

    Only `id` is required. Frozen, and fields hz doesn't know are ignored, so a newer plugin works with an older hz.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    id: ModelId = Field(description="The id hz passes to `model()` and keeps in settings, e.g. `claude-sonnet-4-5`.")
    title: str = Field(
        default="", max_length=200, description="What the picker and a reply's header show; the id when empty."
    )
    description: str = Field(default="", max_length=500, description="One line on what it is good for; optional.")
    aliases: list[ModelId] = Field(
        default_factory=list[str], description="Other ids people may know it by; shown, never passed to `model()`."
    )
    context_window: int | None = Field(
        default=None, ge=1, description="How many tokens it reads at most, when the plugin knows; none when not."
    )
    efforts: list[Effort] = Field(
        default_factory=list[Effort],
        description="The efforts it takes, low to max; empty when it takes none. Only with `thinking` set.",
    )
    tools: bool = Field(
        default=True, description="Whether it calls tools; chat and drafts need it, so hz runs only models that do."
    )
    thinking: Thinking | None = Field(
        default=None,
        description="How hz asks it for an effort: `adaptive` or `budget` (Anthropic's two styles); none when hz "
        "passes no effort and the plugin's model keeps its own settings.",
    )
    available: bool = Field(
        default=True,
        description="False when it didn't answer when the plugin last checked: hz keeps it in the picker, marked "
        "unavailable, and refuses a turn on it before starting, naming others to pick.",
    )
    level: Level | None = Field(
        default=None,
        description="Its tier, `low`, `medium` or `high`, when the plugin marks one: hz picks the model of a level "
        "by it. At most one offer per level; none when unmarked.",
    )

    @property
    def label(self) -> str:
        """What people see: its title, else its id."""
        return self.title or self.id


class ModelUnavailableError(HeinzelError):
    """A model isn't offered or doesn't answer now; the message names models to pick instead (ADR 0159).

    Core raises it before a turn on a model `models()` doesn't list or marks unavailable. A plugin's `model()` raises
    it for an id it doesn't know or can't serve now.
    """

    def __init__(self, model_id: str, others: list[str] | None = None, reason: str = "") -> None:
        """Say which model is unavailable, why in a few words, and which to pick instead."""
        self.model_id = model_id
        self.others = list(others or [])
        why = f" ({reason})" if reason else ""
        if self.others:
            also = f" (also offered: {', '.join(self.others[1:5])})" if len(self.others) > 1 else ""
            instead = f" Pick another: hz system config set ai.access.plugin_model {self.others[0]}{also}"
        else:
            instead = " No other model is offered now."
        super().__init__(f"The model {model_id} is unavailable{why}.{instead}")


class AiModels(Protocol):
    """A plugin's models, run by hz's Pydantic AI engine (capability `ai-models`, interface 1.0, ADR 0159).

    Provide it with `module.provides("ai-models", modes={"read"}, hosts=[...], name="acme")`. Optional plain
    attributes `title` and `summary` (str) name the AI access option; the adapter's name is used when absent.
    Optional method: `test(model_id: str) -> AccessTest`, the plugin's own live check; without it, hz sends one short
    request through `model()`.
    """

    def models(self) -> list[ModelOffer]:
        """The models on offer now, the default first. Mode: read.

        Answer from your own cache, in milliseconds. hz calls it off the event loop, at most one call at a time, with
        a 5-second timeout, and reuses an answer for a few seconds. When a call fails or times out, hz shows the last
        good list and says so. A row that isn't a valid `ModelOffer` is skipped, not the list.
        """
        ...

    def model(self, model_id: str) -> Any:  # noqa: ANN401 - a pydantic_ai Model; the kernel doesn't import the SDK
        """A ready `pydantic_ai.models.Model` for `model_id`, built however you like. Mode: read.

        Build it without calling it. hz calls this only with an id `models()` listed and didn't mark unavailable, off
        the event loop, once per turn. For an id you don't know or can't serve now, raise `ModelUnavailableError`.
        hz runs the model: its requests, tools, streaming, history, limits and usage records.
        """
        ...


UNAUTHORIZED = frozenset({401, 403})
TOO_LONG_STATUS = frozenset({400, 413})
TOO_LONG = re.compile(
    r"prompt is too long|context[ _-]?(length|window)|maximum context|too many (input )?tokens|request too large",
    re.IGNORECASE,
)
"""What a provider says when a request is longer than the model reads; matched, never shown."""
BUSY = frozenset({429, 503, 529})


class PluginModelError(HeinzelError):
    """A plugin's model failed a request; the message says what kind of failure, never the provider's words."""

    def __init__(self, message: str, *, before_response: bool = False) -> None:
        """Keep whether it failed before any response of the turn: then nothing was answered and no tool ran."""
        super().__init__(message)
        self.before_response = before_response


def model_failure(exc: Exception, model: str, *, before_response: bool = False) -> HeinzelError:
    """What hz says about a failed request on a plugin's model, from its kind and HTTP status only (ADR 0159).

    Never the provider's response body, the exception's text or a header: a proxy's error may echo a token. For hz's
    engine on every path: chat, drafts, schedules and the engine runner.
    """
    from pydantic_ai.exceptions import (  # noqa: PLC0415 - only when a request failed
        ModelAPIError,
        ModelConnectionError,
        ModelHTTPError,
        ModelRateLimitError,
        ModelServerError,
    )
    from pydantic_ai.exceptions import ModelUnavailableError as ProviderUnavailableError  # noqa: PLC0415

    status = exc.status_code if isinstance(exc, ModelHTTPError) else None
    code = f" ({status})" if status is not None else ""
    if status in TOO_LONG_STATUS and isinstance(exc, ModelHTTPError) and TOO_LONG.search(str(exc.body or "")):
        message = (
            f"The conversation is longer than the model can read{code}: start a new chat, or pick a model with a "
            "larger context window."
        )
    elif status in UNAUTHORIZED:
        message = f"The provider refused the request{code}: check the plugin's sign-in."
    elif status == 404 or (isinstance(exc, ProviderUnavailableError) and status not in BUSY):  # noqa: PLR2004
        return ModelUnavailableError(model, [], "the provider doesn't know it or can't serve it now")
    elif status in BUSY or isinstance(exc, ModelRateLimitError):
        message = f"The provider is busy or rate-limited{code}; try again shortly."
    elif isinstance(exc, ModelConnectionError):
        message = "Couldn't reach the plugin's model; check the network and the plugin's endpoint."
    elif isinstance(exc, ModelServerError) or (status is not None and status >= 500):  # noqa: PLR2004
        message = f"The provider failed the request{code}; try again later."
    elif isinstance(exc, ModelAPIError):
        message = f"The provider rejected the request{code}."
    else:
        message = f"The plugin's model failed ({type(exc).__name__})."
    return PluginModelError(message, before_response=before_response)


OUTPUT_TOKENS = 16384
"""The most a plugin's model may write in one response besides its thinking, hz's cap on that path (ADR 0159)."""


PROMPT_CACHE: dict[str, Any] = {"anthropic_cache_instructions": True, "anthropic_cache_tool_definitions": True}
"""Pydantic AI's own Anthropic prompt-cache settings: a multi-request turn reads its instructions and tool definitions
from the cache. Only Anthropic's models read `anthropic_*` settings; others ignore them."""


def effort_settings(offer: ModelOffer, effort: Effort | None) -> dict[str, Any]:
    """Model settings for one request on `offer`: hz's output cap, prompt caching, Anthropic's settings for `effort`.

    The output cap leaves room for a `budget` model's thinking, which Anthropic counts within `max_tokens`. With no
    effort, or a model that takes none, no thinking settings: the plugin's model keeps its own.
    """
    if effort is None or offer.thinking is None:
        return {"max_tokens": OUTPUT_TOKENS, **PROMPT_CACHE}
    if offer.thinking == "budget":
        budget = EFFORT_BUDGETS[effort]
        thinking = {"type": "enabled", "budget_tokens": budget}
        return {"max_tokens": budget + OUTPUT_TOKENS, "anthropic_thinking": thinking, **PROMPT_CACHE}
    adaptive = {"anthropic_thinking": {"type": "adaptive"}, "anthropic_effort": effort}
    return {"max_tokens": OUTPUT_TOKENS, **adaptive, **PROMPT_CACHE}


@dataclass(frozen=True, slots=True)
class AiModelsSource:
    """An installed `ai-models` adapter with where it comes from, for hz's own engine and the derived option."""

    name: str
    """The adapter's name: also the AI access option's, e.g. `acme`."""
    module: str
    """The qualified name of the module providing it."""
    distribution: str
    """The plugin's distribution, e.g. `heinzel-acme`."""
    version: str
    """The plugin's version: a change makes approved schedules ask again (their access fingerprint changes)."""
    adapter: AiModels
    """The adapter itself: only hz's trusted core code holds it, never a requester's handle."""
    kept: Path | None = None
    """Where the last good list's ids are kept across a restart, a private file in the profile's state; none: memory
    only."""
    unanswered: Path | None = None
    """Where hz keeps the ids of models that didn't answer `hz ai access test --all`, a private file in the profile's
    state; none: nothing is kept and no model is marked."""


class AiModelOptions:
    """Turns an `ai-models` adapter into its AI access option; the `ai` module provides the subclass (ADR 0159).

    A base class rather than a protocol, so the kernel can resolve it by type, like `UsageLog`.
    """

    def option(self, name: str, source: Callable[[], AiModelsSource]) -> "AiAccess":
        """The `ai-access` adapter of the `ai-models` adapter `name`, which `source` builds on first use.

        Raises:
            NotImplementedError: Always, here; the subclass builds it.
        """
        raise NotImplementedError


LIST_TIMEOUT = 5.0
"""How long hz waits for a plugin's `models()`; it should answer from its cache in milliseconds."""
LIST_TTL = 3.0
"""How long a list is reused before hz asks the plugin again, so Settings polling doesn't hit it on every read."""
MAX_OFFERS = 500
NONE_CHOSEN = "(none chosen)"
DETAIL_LIMIT = 300

logger = logging.getLogger(__name__)


@dataclass
class _Listing:
    """One plugin's list: the last good one, when it was got, what the last call said, and the call in flight."""

    offers: list[ModelOffer] | None = None
    at: float = 0.0
    skipped: str = ""
    problem: str = ""
    done: threading.Event | None = None
    saved: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


_listings: dict[int, tuple[Callable[[], object | None], _Listing]] = {}
"""Per adapter object, by identity: the entry holds a weak reference (or, for an adapter that can't have one, the
adapter itself) and is checked against it, so a rebuilt adapter never gets a freed one's list, and two equal adapter
objects never share one. A weakly referenced adapter's entry goes away with it."""
_listings_lock = threading.RLock()  # re-entrant: a freed adapter's callback may run while it is held


def _validated(found: object) -> tuple[list[ModelOffer], str]:
    """The valid offers `models()` returned, without repeated ids, the plugin's order kept, and what was skipped.

    One bad row is skipped and logged, never the whole list: the list comes from a database hz can't see, and a
    newer plugin may send a value an older hz doesn't know.

    Raises:
        TypeError: If it isn't a list at all.
    """
    if not isinstance(found, list):
        msg = f"models() returned {type(found).__name__}, not a list of ModelOffer"
        raise TypeError(msg)
    rows = cast("list[object]", found)
    offers: dict[str, ModelOffer] = {}
    skipped: list[str] = []
    for item in rows[:MAX_OFFERS]:
        try:
            offer = ModelOffer.model_validate(item.model_dump() if isinstance(item, ModelOffer) else item)
        except ValidationError as exc:
            skipped.append(clean_text(str(exc.errors()[0]["msg"]))[:80])
            continue
        offers.setdefault(offer.id, offer)
    notes = [f"{len(skipped)} offers skipped: {skipped[0]}"] if skipped else []
    if len(rows) > MAX_OFFERS:
        notes.append(f"only the first {MAX_OFFERS} of {len(rows)} offers are shown")
    if notes:
        log_event(logger, "ai.models.offers_skipped", level=logging.WARNING, skipped=len(skipped), rows=len(rows))
    return list(offers.values()), "; ".join(notes)


def _fetch(source: AiModelsSource, listing: _Listing, done: threading.Event) -> None:
    """Ask the plugin once, on a daemon thread, and keep what it said; never raises."""
    try:
        offers, skipped = _validated(source.adapter.models())
    except Exception as exc:  # noqa: BLE001 - a plugin's failure is a note, never a crash of the listing
        problem = clean_text(f"its model list failed ({type(exc).__name__}: {exc})")[:DETAIL_LIMIT]
        with listing.lock:
            listing.problem = problem
    else:
        with listing.lock:
            listing.offers, listing.skipped, listing.problem, listing.at = offers, skipped, "", time.monotonic()
        if source.kept is not None:
            _keep(source.kept, offers, listing)
    finally:
        with listing.lock:
            listing.done = None
        done.set()


KEPT_FIELDS = frozenset({"id", "level", "available", "tools"})
"""What hz keeps of a good list across a restart: ids and what picking a model needs, never titles or descriptions."""


def _keep(path: Path, offers: list[ModelOffer], listing: _Listing) -> None:
    """Keep `offers`' ids in `path`, mode 0600, replaced atomically when they changed; never raises."""
    text = json.dumps([o.model_dump(include=set(KEPT_FIELDS)) for o in offers])
    if text == listing.saved:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".json")  # mode 0600
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                file.write(text)
            Path(temporary).replace(path)
        finally:
            with contextlib.suppress(OSError):
                Path(temporary).unlink(missing_ok=True)
    except OSError as exc:
        log_event(logger, "ai.models.keep_failed", level=logging.WARNING, error=type(exc).__name__)
        return
    listing.saved = text


def _kept(path: Path | None) -> list[ModelOffer]:
    """The list kept in `path` by an earlier process; none when missing, unreadable or corrupt."""
    if path is None:
        return []
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as file:
            rows: object = json.loads(file.read(MAX_OFFERS * 400))
        offers, _skipped = _validated(rows)
    except (OSError, ValueError, TypeError):
        return []
    return offers


def _listing_of(adapter: object) -> _Listing:
    """The listing of `adapter`, made on first use; the caller holds `_listings_lock`."""
    key = id(adapter)
    found = _listings.get(key)
    if found is not None and found[0]() is adapter:
        return found[1]
    try:
        ref: Callable[[], object | None] = weakref.ref(adapter, lambda _gone: _forget(key))
    except TypeError:  # can't be weakly referenced: keep the adapter itself, so its id can't be reused meanwhile
        ref = lambda: adapter  # noqa: E731 - a strong reference with the same shape as a weak one
    listing = _Listing()
    _listings[key] = (ref, listing)
    return listing


def _forget(key: int) -> None:
    """Drop a freed adapter's listing, unless another adapter took its id meanwhile."""
    with _listings_lock:
        found = _listings.get(key)
        if found is not None and found[0]() is None:
            del _listings[key]


def list_offers(
    source: AiModelsSource, timeout: float = LIST_TIMEOUT, *, raw: bool = False
) -> tuple[list[ModelOffer], str]:
    """What `source` offers now, and a note on what was skipped, or that this is the last good list (ADR 0159).

    One call per plugin at a time, on a daemon thread, so a hung plugin holds one thread and never blocks exit; a
    second caller waits for that call, at most `timeout`. A list got in the last few seconds is reused. With no
    good list yet, a failure is an empty list and the note says why. A model that didn't answer the last
    `hz ai access test --all` is marked unavailable, unless `raw`: the plugin's own flags alone.
    """
    offers, note = _listed(source, timeout)
    if raw or source.unanswered is None or not (unanswered := _unanswered(source.unanswered)):
        return offers, note
    return [o.model_copy(update={"available": False}) if o.id in unanswered else o for o in offers], note


def _listed(source: AiModelsSource, timeout: float) -> tuple[list[ModelOffer], str]:
    """`list_offers` without the marks of `hz ai access test --all`."""
    with _listings_lock:
        listing = _listing_of(source.adapter)
    with listing.lock:
        fresh = listing.offers is not None and not listing.problem and time.monotonic() - listing.at < LIST_TTL
        if fresh:
            return list(listing.offers or []), listing.skipped
        done = listing.done
        if done is not None and listing.offers is not None:  # a call in flight: the last good list, at once
            return list(listing.offers), "its model list is being read again; showing the last list"
        if done is None:
            done = listing.done = threading.Event()
            name = f"ai-models-{source.name}"
            threading.Thread(target=_fetch, args=(source, listing, done), name=name, daemon=True).start()
    finished = done.wait(timeout)
    with listing.lock:
        kept, skipped, problem = listing.offers, listing.skipped, listing.problem
    if not finished:
        problem = f"its model list took longer than {timeout:g}s"
    if not problem:
        return list(kept or []), skipped
    if not kept and (earlier := _kept(source.kept)):
        return earlier, f"{problem}; showing the list kept from before the restart"
    return (list(kept), f"{problem}; showing the last list") if kept else ([], problem)


_unanswered_cache: dict[Path, tuple[tuple[int, int, int], frozenset[str]]] = {}
_unanswered_lock = threading.Lock()
_mark_lock = threading.Lock()


def _unanswered(path: Path) -> frozenset[str]:
    """The ids kept in `path` as not answering, re-read only when the file changed; none when missing or corrupt."""
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as file:
            stat = os.fstat(file.fileno())
            stamp = (stat.st_ino, stat.st_mtime_ns, stat.st_size)  # replaced atomically: a new file each time
            with _unanswered_lock:
                cached = _unanswered_cache.get(path)
            if cached is not None and cached[0] == stamp:
                return cached[1]
            rows: object = json.loads(file.read(MAX_OFFERS * 210))
    except (OSError, ValueError):
        return frozenset()
    found = frozenset(
        row for row in cast("list[object]", rows if isinstance(rows, list) else []) if isinstance(row, str)
    )
    with _unanswered_lock:
        _unanswered_cache[path] = (stamp, found)
    return found


def mark_answered(source: AiModelsSource, model_id: str, *, answered: bool) -> None:
    """Keep whether `model_id` answered a test, so every head's picker marks it; never raises (ADR 0159).

    Only a person's `hz ai access test --all` calls it. A model that didn't answer is marked unavailable until a later
    test answers; the plugin's own `available=False` is never cleared by it. Ids only, mode 0600, replaced atomically.
    """
    path = source.unanswered
    if path is None:
        return
    with _mark_lock:  # one writer at a time in this process; a later test wins
        before = _unanswered(path)
        after = (before - {model_id}) if answered else (before | {model_id})
        if after == before:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".json")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as file:
                    file.write(json.dumps(sorted(after)[:MAX_OFFERS]))
                Path(temporary).replace(path)
            finally:
                with contextlib.suppress(OSError):
                    Path(temporary).unlink(missing_ok=True)
        except OSError as exc:
            log_event(logger, "ai.models.mark_failed", level=logging.WARNING, error=type(exc).__name__)


def choose(offers: list[ModelOffer], wanted: str | None, effort: Effort | None = None) -> ModelOffer:
    """The offer hz runs: `wanted` when given, else the first that calls tools and isn't known to be unavailable.

    Raises:
        ModelUnavailableError: If `wanted` isn't offered, didn't answer when last probed or calls no tools; or, with
            nothing wanted, no offer would do.
        HeinzelError: If the model doesn't take `effort`.
    """
    usable = [o.id for o in offers if o.tools and o.available is not False]
    if wanted is None:
        if not usable:
            why = "the plugin offers no model that answers and calls tools"
            raise ModelUnavailableError(NONE_CHOSEN, [], why)
        wanted = usable[0]
    offer = next((o for o in offers if o.id == wanted), None)
    others = [i for i in usable if i != wanted]
    if offer is None:
        raise ModelUnavailableError(wanted, others, "the plugin doesn't offer it now")
    if offer.available is False:
        raise ModelUnavailableError(wanted, others, "it didn't answer when last checked")
    if not offer.tools:
        raise ModelUnavailableError(wanted, others, "it doesn't call tools, which chat and drafts need")
    if effort is not None and offer.thinking is not None and offer.efforts and effort not in offer.efforts:
        allowed = ", ".join(offer.efforts)
        msg = f"The model {wanted} doesn't take the effort {effort}; pick one of {allowed}, or none"
        raise HeinzelError(msg)
    return offer


def model_for_level(source: AiModelsSource, level: Level | None) -> ModelOffer:
    """The usable offer of `source` marked with `level`, else its first usable offer (ADR 0159).

    Usable means it calls tools and isn't marked unavailable. With several usable offers marked with `level`, the
    first, and a warning is logged; with none marked, or `level` None, the first usable offer.

    Raises:
        ModelUnavailableError: If the plugin offers no usable model.
    """
    offers, _note = list_offers(source)
    usable = [o for o in offers if o.tools and o.available is not False]
    if not usable:
        raise ModelUnavailableError(NONE_CHOSEN, [], "the plugin offers no model that answers and calls tools")
    marked = [o for o in usable if level is not None and o.level == level]
    if len(marked) > 1:
        log_event(
            logger, "ai.models.level_ambiguous", level=logging.WARNING, adapter=source.name, tier=level, n=len(marked)
        )
    return (marked or usable)[0]


def build_model(source: AiModelsSource, offer: ModelOffer) -> Any:  # noqa: ANN401 - a pydantic_ai Model
    """The plugin's ready Pydantic AI model for `offer`; building it must not call the model.

    Raises:
        HeinzelError: If the plugin fails to build it or returns something that isn't a Pydantic AI model.
    """
    from pydantic_ai.models import Model  # noqa: PLC0415 - only when a turn or a test builds one

    try:
        model: object = source.adapter.model(offer.id)
    except HeinzelError:
        raise
    except Exception as exc:  # noqa: BLE001 - a plugin's failure is a clear error, never a traceback
        detail = clean_text(f"{type(exc).__name__}: {exc}")[:DETAIL_LIMIT]
        msg = f"The plugin {source.distribution} couldn't build the model {offer.id}: {detail}"
        raise HeinzelError(msg) from None
    if not isinstance(model, Model):
        msg = (
            f"The plugin {source.distribution} returned {type(model).__name__} for {offer.id}, not a Pydantic AI model"
        )
        raise HeinzelError(msg)
    return cast("Model[Any]", model)  # type: ignore[redundant-cast]  # narrow the provider generic for Pyright


class PluginModelChoice(ModuleSettings):
    """The plugin model hz runs and its effort, as `[ai.access]` says: read by hz's engine before each turn.

    The `ai.access` module declares these fields for Settings and documentation; this reader is the kernel's own, so
    the engine doesn't depend on that module (ADR 0159).
    """

    plugin_model: str | None = Field(default=None, pattern=MODEL_ID_PATTERN)
    plugin_effort: Effort | None = None

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"plugin_model", "plugin_effort"})
