"""The detached helper that asks a scheduled turn's question in chat (ADR 0160), run as the process the tick starts."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from heinzel.kernel._self_chat import STATE
from heinzel.modules.heinzel._self_chat_questions import HELPER

pytestmark = pytest.mark.integration


def _helper(home: Path, *arguments: str) -> int:
    environment = {**os.environ, "HOME": str(home)}
    child = subprocess.Popen([sys.executable, "-P", "-m", HELPER, *arguments], cwd=home, env=environment)
    try:
        return child.wait(120)
    finally:
        if child.poll() is None:  # only the process this test started
            child.kill()
            child.wait(10)


def test_the_helper_boots_with_the_ticks_profile_and_leaves_nothing_without_a_chat_adapter(tmp_path):
    config = Path(os.environ["HZ_CONFIG_HOME"]) / "config.toml"
    config.write_text("[system.notify]\nchat_questions = true\n")
    assert _helper(tmp_path, "hz-" + "0" * 26, "interactive-" + "a" * 64) == 0
    ledger = Path(os.environ["HZ_STATE_DIR"]) / STATE / "questions.json"
    assert not ledger.exists() or json.loads(ledger.read_text()).get("live", {}) == {}, "no live ask: nothing was asked"


def test_the_helper_refuses_arguments_that_are_not_two_ids(tmp_path):
    assert _helper(tmp_path) == 2
    assert _helper(tmp_path, "hz-" + "0" * 26, "--turn") == 2
