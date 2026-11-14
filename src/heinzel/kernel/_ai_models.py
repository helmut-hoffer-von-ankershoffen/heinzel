"""The AI models capability: a plugin brings its own models, and hz runs them (ADR 0159).

A company plugin reaches its models its own way: an internal endpoint or a company proxy, its own authentication,
its own list of models from wherever it keeps them. It does not run an agent: it hands hz a ready Pydantic AI model
(`pydantic_ai.models.Model`), and hz's own engine runs chat, drafts and schedules on it, with hz's tools, limits and
usage records. The plugin owns the endpoint, the token, refreshing it, and caching the list; hz never sees a token.

An adapter of `ai-models` becomes an AI access option of the same name without more code: the kernel derives an
`ai-access` adapter from it, so `hz ai access use <name>` and the web picker work. A plugin that also provides
`ai-access` under that name keeps its own instead (for a `status` or `test` of its own).
"""

import concurrent.futures
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, Protocol, cast

from pydantic import BaseModel, Field, StringConstraints

from heinzel.kernel._agents import Effort
from heinzel.kernel._config import ModuleSettings
from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._text import clean_text

if TYPE_CHECKING:
    from heinzel.kernel._ai import AiAccess

MODEL_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$"
"""What a model id may be: what endpoints and proxies use (`gpt-5`, `claude-sonnet-4-5`, `team/llama3.1:8b`), with no
spaces, quotes or control characters, so it is safe in a command line, a log line and a configuration value."""

ModelId = Annotated[str, StringConstraints(pattern=MODEL_ID_PATTERN)]

Thinking = Literal["adaptive", "budget"]
"""How a model is asked to think harder, in Anthropic's Messages API: `adaptive` (`thinking: {type: adaptive}` plus
`output_config: {effort}`, Pydantic AI's `anthropic_effort`) for newer models, `budget` (`thinking: {type: enabled,
budget_tokens}`) for older ones. A plugin whose models speak another API leaves it unset and maps effort itself."""

EFFORT_BUDGETS: dict[Effort, int] = {"low": 2048, "medium": 8192, "high": 16384, "xhigh": 32768, "max": 63999}
"""The thinking budget in tokens hz asks a `budget` model for at each effort."""


class ModelOffer(BaseModel):
    """One model a plugin offers: what the picker shows and what `model` takes."""

    id: ModelId = Field(description="The id `model` takes and settings keep, e.g. `gpt-5` or `team/llama3.1:8b`.")
    title: str = Field(default="", max_length=200, description="What people pick it by; the id when empty.")
    context_window: int | None = Field(
        default=None, ge=1, description="How many tokens it reads at most, when the plugin knows; none when not."
    )
    efforts: list[Effort] = Field(
        default_factory=list[Effort], description="The efforts it can be asked for, low to max; empty when none."
    )
    tools: bool = Field(
        default=True, description="Whether it calls tools; chat and drafts need it, so hz offers only models that do."
    )
    thinking: Thinking | None = Field(
        default=None,
        description="How hz asks it for an effort: `adaptive` or `budget` (Anthropic's two styles), mapped from the "
        "chosen effort; none when hz passes no effort and the plugin's model decides.",
    )
    available: bool | None = Field(
        default=None,
        description="Whether it answered when the plugin last probed it: false keeps it in pickers, marked "
        "unavailable, and makes hz refuse it before a turn, naming others to pick; none when not probed.",
    )

    @property
    def label(self) -> str:
        """What people see: its title, else its id."""
        return self.title or self.id


class ModelUnavailableError(HeinzelError):
    """The chosen model isn't offered or doesn't answer now; the message names models to pick instead (ADR 0159)."""

    def __init__(self, model_id: str, others: list[str], reason: str = "") -> None:
        """Say which model is unavailable, why in a few words, and which to pick instead."""
        self.model_id = model_id
        self.others = others
        why = f" ({reason})" if reason else ""
        instead = (
            f" Pick another: hz system config set ai.access.plugin_model {others[0]}"
            + (f" (also offered: {', '.join(others[1:5])})" if len(others) > 1 else "")
            if others
            else " No other model is offered now."
        )
        super().__init__(f"The model {model_id} is unavailable{why}.{instead}")


def effort_settings(offer: ModelOffer, effort: Effort | None) -> dict[str, Any]:
    """Anthropic's native model settings for `effort` on `offer`, as Pydantic AI passes them through (ADR 0159).

    Empty when no effort is chosen or the model takes none: then the plugin's model keeps its own settings.
    """
    if effort is None or offer.thinking is None:
        return {}
    if offer.thinking == "budget":
        return {"anthropic_thinking": {"type": "enabled", "budget_tokens": EFFORT_BUDGETS[effort]}}
    return {"anthropic_thinking": {"type": "adaptive"}, "anthropic_effort": effort}


class AiModels(Protocol):
    """Models a plugin reaches its own way, run by hz's Pydantic AI engine (capability `ai-models`, ADR 0159)."""

    def models(self) -> list[ModelOffer]:
        """The models offered now, the default first; fast, from the plugin's own cache. Mode: read.

        hz calls it for listings and before each turn, with a timeout, and shows the last good list when it fails.
        """
        ...

    def model(self, model_id: str) -> Any:  # noqa: ANN401 - a pydantic_ai Model; the kernel doesn't import the SDK
        """A ready `pydantic_ai.models.Model` for `model_id`, its client carrying the plugin's own auth. Mode: read.

        Building it must not call the model. hz runs it: one turn at a time, with its own tools, limits and records.
        """
        ...


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
MAX_OFFERS = 500
NONE_CHOSEN = "(none chosen)"
DETAIL_LIMIT = 300

_last_good: dict[str, list[ModelOffer]] = {}
_last_good_lock = threading.Lock()


def _validated(found: object) -> list[ModelOffer]:
    """The offers `models()` returned, validated and without repeated ids, the plugin's order kept.

    Raises:
        TypeError: If it isn't a list of offers (or of their fields).
    """
    if not isinstance(found, list):
        msg = f"models() returned {type(found).__name__}, not a list of ModelOffer"
        raise TypeError(msg)
    offers: dict[str, ModelOffer] = {}
    for item in cast("list[object]", found)[:MAX_OFFERS]:
        offer = ModelOffer.model_validate(item.model_dump() if isinstance(item, ModelOffer) else item)
        offers.setdefault(offer.id, offer)
    return list(offers.values())


def list_offers(source: AiModelsSource, timeout: float = LIST_TIMEOUT) -> tuple[list[ModelOffer], str]:
    """What `source` offers now, and a note when that is the last good list because asking failed (ADR 0159).

    The call runs on a thread of its own with `timeout`: a slow or broken plugin never stalls a listing or a turn.
    With no last good list, a failure is an empty list and the note says why.
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ai-models-{source.name}")
    try:
        offers = _validated(pool.submit(source.adapter.models).result(timeout=timeout))
    except concurrent.futures.TimeoutError:
        problem = f"its model list took longer than {timeout:g}s"
    except Exception as exc:  # noqa: BLE001 - a plugin's failure is a note, never a crash of the listing
        problem = clean_text(f"its model list failed ({type(exc).__name__}: {exc})")[:DETAIL_LIMIT]
    else:
        with _last_good_lock:
            _last_good[source.name] = offers
        return offers, ""
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    with _last_good_lock:
        kept = _last_good.get(source.name, [])
    return kept, f"{problem}; showing the last list" if kept else problem


def choose(offers: list[ModelOffer], wanted: str | None) -> ModelOffer:
    """The offer hz runs: `wanted` when given, else the first that calls tools and isn't known to be unavailable.

    Raises:
        ModelUnavailableError: If `wanted` isn't offered, didn't answer when last probed or calls no tools; or, with
            nothing wanted, no offer would do.
    """
    usable = [o.id for o in offers if o.tools and o.available is not False]
    if wanted is None:
        if not usable:
            why = "the plugin offers no model that answers and calls tools"
            raise ModelUnavailableError(NONE_CHOSEN, [], why)
        return next(o for o in offers if o.id == usable[0])
    offer = next((o for o in offers if o.id == wanted), None)
    others = [i for i in usable if i != wanted]
    if offer is None:
        raise ModelUnavailableError(wanted, others, "the plugin doesn't offer it now")
    if offer.available is False:
        raise ModelUnavailableError(wanted, others, "it didn't answer when the plugin last checked")
    if not offer.tools:
        raise ModelUnavailableError(wanted, others, "it doesn't call tools, which chat and drafts need")
    return offer


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
    return model


class PluginModelChoice(ModuleSettings):
    """The plugin model hz runs and its effort, as `[ai.access]` says: read by hz's engine before each turn.

    The `ai.access` module declares these fields for Settings and documentation; this reader is the kernel's own, so
    the engine doesn't depend on that module (ADR 0159).
    """

    plugin_model: str | None = Field(default=None, pattern=MODEL_ID_PATTERN)
    plugin_effort: Effort | None = None

    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"plugin_model", "plugin_effort"})
