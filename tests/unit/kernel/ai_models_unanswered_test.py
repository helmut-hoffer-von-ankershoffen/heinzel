"""`hz ai access test --all` marks the models of a plugin that don't answer, across processes (ADR 0159)."""

import json
import stat

import pytest

from heinzel.kernel import AiModelsSource, ModelOffer, ModelUnavailableError
from heinzel.kernel._ai_models import choose, list_offers, mark_answered, model_for_level

pytestmark = pytest.mark.unit


class Models:
    """Three models: the plugin itself marks `gone` unavailable."""

    def models(self) -> list[ModelOffer]:
        return [ModelOffer(id="big"), ModelOffer(id="small"), ModelOffer(id="gone", available=False)]

    def model(self, model_id: str) -> object:
        raise NotImplementedError


def source(tmp_path, adapter=None) -> AiModelsSource:
    folder = tmp_path / "ai" / "plugin-models"
    return AiModelsSource(
        "acme",
        "acme",
        "heinzel-acme",
        "1.0.0",
        adapter or Models(),  # type: ignore[arg-type]
        folder / "acme.json",
        folder / "acme.unanswered.json",
    )


def available(found: AiModelsSource, *, raw: bool = False) -> dict[str, bool]:
    return {o.id: o.available for o in list_offers(found, raw=raw)[0]}


def test_an_unanswered_model_is_marked_for_every_reader_until_a_test_answers(tmp_path):
    acme = source(tmp_path)
    mark_answered(acme, "big", answered=False)
    assert available(acme) == {"big": False, "small": True, "gone": False}
    assert available(acme, raw=True) == {"big": True, "small": True, "gone": False}, "raw: the plugin's flags alone"
    restarted = source(tmp_path, Models())  # another process: a new adapter, the same profile
    assert available(restarted)["big"] is False, "the mark survives a restart"
    with pytest.raises(ModelUnavailableError, match="big is unavailable \\(it didn't answer when last checked\\)"):
        choose(list_offers(restarted)[0], "big")
    assert choose(list_offers(restarted)[0], None).id == "small", "the default skips it"
    assert model_for_level(restarted, None).id == "small"
    mark_answered(restarted, "big", answered=True)
    assert available(acme) == {"big": True, "small": True, "gone": False}


def test_a_test_never_clears_the_plugins_own_unavailable(tmp_path):
    acme = source(tmp_path)
    mark_answered(acme, "gone", answered=True)
    assert available(acme)["gone"] is False


def test_the_marks_are_private_ids_only_and_a_corrupt_file_marks_nothing(tmp_path):
    acme = source(tmp_path)
    mark_answered(acme, "small", answered=False)
    mark_answered(acme, "big", answered=False)
    path = acme.unanswered
    assert path is not None
    assert json.loads(path.read_text()) == ["big", "small"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    stamp = path.stat().st_mtime_ns
    mark_answered(acme, "big", answered=False)
    assert path.stat().st_mtime_ns == stamp, "an unchanged set isn't written again"
    path.write_text("{not json")
    assert available(acme) == {"big": True, "small": True, "gone": False}
    path.write_text(json.dumps({"big": 1}))
    assert available(acme)["big"] is True, "only a list of ids counts"


def test_without_a_place_to_keep_them_nothing_is_marked(tmp_path):
    acme = source(tmp_path)
    memory = AiModelsSource("acme", "acme", "heinzel-acme", "1.0.0", acme.adapter)
    mark_answered(memory, "big", answered=False)
    assert available(memory)["big"] is True
    assert not (tmp_path / "ai").exists()


def test_a_mark_that_cant_be_written_is_only_logged(tmp_path):
    blocker = tmp_path / "ai"
    blocker.write_text("a file where the folder should be")
    acme = source(tmp_path)
    mark_answered(acme, "big", answered=False)
    assert available(acme)["big"] is True
