"""Constructing the authenticated web and CLI heads does not initialize the model execution runtime."""

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


def test_local_heads_defer_model_runtime_until_an_explicit_model_operation(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("")
    environment = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "HZ_CONFIG": str(config),
        "HZ_CONFIG_HOME": str(tmp_path / "config"),
        "HZ_STATE_DIR": str(tmp_path / "state"),
        "HZ_DATA_DIR": str(tmp_path / "data"),
        "HZ_CACHE_DIR": str(tmp_path / "cache"),
        "HZ_LOG_DIR": str(tmp_path / "log"),
        "HZ_PLUGINS_ENABLED": "false",
        "HZ_SYSTEM_NOTIFY__NATIVE": "false",
        "HZ_TEST": "1",
    }
    script = """
import sys
from heinzel.kernel import boot
from heinzel.heads import cli, web
from typer.main import get_command
kernel = boot()
get_command(cli.build(kernel))
web.build(kernel, port=8737, token='t' * 43, static=None)
loaded = any(name == 'pydantic_ai' or name.startswith('pydantic_ai.') for name in sys.modules)
assert not loaded, 'Model runtime loaded during local head startup'
from heinzel.modules.heinzel._interactive_turn import InteractiveTurn, ToolActivity, interactive_turn_id
assert 'pydantic_ai' in sys.modules
from heinzel.modules.heinzel._turn_activity import ToolActivity as Activity, interactive_turn_id as identity
assert Activity is ToolActivity
assert identity is interactive_turn_id
assert interactive_turn_id('request') == 'interactive-' + __import__('hashlib').sha256(b'request').hexdigest()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=environment, timeout=12, check=False
    )
    assert result.returncode == 0, result.stderr
