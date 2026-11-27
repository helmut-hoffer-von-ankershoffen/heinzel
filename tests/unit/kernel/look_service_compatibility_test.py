"""Look services preserve the published kernel imports and do not depend on the kernel."""

import ast
import importlib
import pickle
from pathlib import Path
from types import FunctionType, ModuleType
from typing import get_type_hints

import pytest

from heinzel import ThemeSignals
from heinzel.kernel.look import _theme_signals, _themes
from heinzel.services.look import _assets
from heinzel.services.look import _theme_signals as signal_service

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("legacy", "service", "owner"),
    [
        (_theme_signals.ThemeSignals, signal_service.ThemeSignals, _theme_signals),
        (_theme_signals.daypart, signal_service.daypart, _theme_signals),
        (_theme_signals._at, signal_service._at, _theme_signals),
        (_themes._read_regular, _assets._read_regular, _themes),
    ],
)
def test_moved_symbol_retains_one_identity_and_its_serialized_path(
    legacy: object, service: type | FunctionType, owner: ModuleType
) -> None:
    assert legacy is service
    assert importlib.import_module(service.__module__) is owner
    assert pickle.loads(pickle.dumps(service)) is legacy  # noqa: S301 - serialized by this test


def test_public_theme_signal_model_reaches_the_same_service_class() -> None:
    model = ThemeSignals(weather="rain")
    assert type(model) is signal_service.ThemeSignals
    assert pickle.loads(pickle.dumps(model)) == model  # noqa: S301 - serialized by this test


def test_legacy_signal_vocabulary_keeps_the_published_annotations() -> None:
    assert get_type_hints(_theme_signals) == {
        "DAYPARTS": tuple[_theme_signals.Daypart, ...],
        "WEATHERS": tuple[_theme_signals.Weather, ...],
    }
    assert _theme_signals.DAYPARTS is signal_service.DAYPARTS
    assert _theme_signals.WEATHERS is signal_service.WEATHERS


@pytest.mark.parametrize("service", [_assets, signal_service])
def test_look_service_leaves_do_not_import_kernel_layers(service: ModuleType) -> None:
    assert service.__file__ is not None
    tree = ast.parse(Path(service.__file__).read_text(encoding="utf-8"))
    imports = [
        *(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names),
        *(node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)),
    ]
    allowed = ("heinzel.ports", "heinzel.spec", "heinzel.services")
    assert not [
        name
        for name in imports
        if name.startswith("heinzel") and not any(name == layer or name.startswith(layer + ".") for layer in allowed)
    ]
