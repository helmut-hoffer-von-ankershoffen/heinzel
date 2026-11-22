"""Model lists are kept per adapter object, never per id, so a rebuilt adapter never gets a freed one's (ADR 0159)."""

import pytest

from heinzel import ModelOffer
from heinzel.kernel import AiModelsSource
from heinzel.kernel.ai._ai_models import list_offers

pytestmark = pytest.mark.unit


class A:
    def models(self) -> list[ModelOffer]:
        return [ModelOffer(id="old-model")]

    def model(self, model_id: str) -> object:
        raise NotImplementedError


class B:
    def models(self) -> list[ModelOffer]:
        return [ModelOffer(id="new-model")]

    def model(self, model_id: str) -> object:
        raise NotImplementedError


def _source(adapter: object) -> AiModelsSource:
    return AiModelsSource(name="acme", module="m", distribution="d", version="1", adapter=adapter)  # type: ignore[arg-type]


def test_probe_d3_a_new_adapter_never_gets_another_adapters_list():
    import time

    started = time.monotonic()
    reused = 0
    for _ in range(50):
        first = A()
        list_offers(_source(first))
        old = id(first)
        del first  # refcounting frees it at once (no cycle), so no gc.collect(): that took >5 s under xdist
        second = B()
        if id(second) != old:
            continue
        reused += 1
        offers, _note = list_offers(_source(second))
        assert [o.id for o in offers] == ["new-model"], "a rebuilt adapter got the previous adapter's cached list"
    assert reused, "no id reuse happened; rerun"
    assert time.monotonic() - started < 2.0
