"""Deferred built-in renderers retain their typed signatures and delegate the same value and result."""

import importlib
import inspect
from typing import get_type_hints

import pytest
from rich.text import Text

from heinzel import OperationLet

pytestmark = pytest.mark.unit
OWNERS = frozenset(
    {
        "org",
        "heinzel",
        "graph",
        "system",
        "team",
        "telemetry",
        "system.notify",
        "system.config",
        "system.claude",
        "system.capability",
        "system.plugin",
        "system.state",
        "system.layer",
        "system.daemon",
        "system.connector",
        "system.plugin.docs",
    }
)


def test_registered_renderers_keep_runtime_annotations_and_forward_unchanged(make_kernel, monkeypatch):
    kernel = make_kernel(builtins=True)
    checked = set()
    for _record, operation in kernel.lets(OperationLet):
        renderer = operation.render
        if renderer is None or renderer.__module__ not in {"heinzel.modules." + name for name in OWNERS}:
            continue
        if renderer in checked:
            continue
        implementation = importlib.import_module(renderer.__module__ + "._render")
        original = getattr(implementation, renderer.__name__)
        assert inspect.signature(renderer) == inspect.signature(original)
        assert get_type_hints(renderer) == get_type_hints(original)
        value, returned, seen = object(), Text("rendered fixture"), []

        def render(argument, seen=seen, returned=returned):
            seen.append(argument)
            return returned

        with monkeypatch.context() as patch:
            patch.setattr(implementation, renderer.__name__, render)
            assert renderer(value) is returned
        assert seen == [value]
        checked.add(renderer)
    assert len(checked) == 49  # system.plugin adds render_bind, render_checklist and render_result (ADR 0145)
