import pytest
from pydantic import BaseModel

from heinzel import Module, __version__
from heinzel.modules.system import SystemService

pytestmark = pytest.mark.unit


class Section(BaseModel):
    answer: int


def test_info_collects_sections_and_isolates_broken_ones(make_kernel):
    good, model, broken = Module("good", "g"), Module("model", "m"), Module("broken", "b")
    good.info(lambda: {"ok": True})
    model.info(lambda: Section(answer=42))

    @broken.info
    def explode() -> dict[str, str]:
        msg = "boom"
        raise RuntimeError(msg)

    info = SystemService(make_kernel(good=good, model=model, broken=broken)).info()

    assert info.version == __version__
    assert info.sections == {"broken": {"error": "RuntimeError: boom"}, "good": {"ok": True}, "model": {"answer": 42}}


def test_modules_lists_lets_by_kind(make_kernel):
    module = Module("x", "The x module.")

    @module.operation
    def run() -> None:
        """Run."""

    [info] = SystemService(make_kernel(x=module)).modules()
    assert (info.qualname, info.summary, info.builtin, info.lets) == (
        "x",
        "The x module.",
        False,
        {"OperationLet": ["run"]},
    )
