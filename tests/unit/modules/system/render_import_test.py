"""An hz updated in place renders with its old kernel still in memory, so the renderer imports no new kernel names."""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

RENDER = Path(__file__).resolve().parents[4] / "src/heinzel/modules/system/_render.py"


def test_the_update_renderer_does_not_import_the_tools_reference_at_run_time():
    tree = ast.parse(RENDER.read_text(encoding="utf-8"))
    runtime = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
    assert "heinzel.modules.system._tools" not in {node.module for node in runtime}
