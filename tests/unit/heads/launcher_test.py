"""The package entry shim isolates Python before importing any application namespace."""

import os
import runpy
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("entry", "module"),
    [
        ("main", "heinzel.heads.bootstrap"),
        ("cli", "heinzel"),
        ("install", "heinzel.heads.install"),
    ],
)
def test_namespace_independent_entry_preserves_arguments_and_discards_stale_python_paths(monkeypatch, entry, module):
    main = runpy.run_path(str(Path(__file__).resolve().parents[3] / "src/_heinzel_launcher.py"))[entry]
    calls = []
    monkeypatch.setenv("PYTHONPATH", "/a/stale/checkout")
    monkeypatch.setenv("PYTHONHOME", "/another/python")
    monkeypatch.setenv("HZ_LOG_LEVEL", "INFO")
    monkeypatch.setattr(sys, "argv", ["heinzel", "system", "info", "--json"])
    monkeypatch.setattr(os, "execve", lambda *arguments: calls.append(arguments))
    main()
    executable, arguments, environment = calls[0]
    assert executable == sys.executable
    assert arguments == [sys.executable, "-I", "-m", module, "system", "info", "--json"]
    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment
    assert environment["HZ_LOG_LEVEL"] == "INFO"
