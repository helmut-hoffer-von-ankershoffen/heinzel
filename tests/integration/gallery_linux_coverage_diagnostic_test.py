"""Bounded Linux diagnostic of PR 319's existing focused Python selection.

This emits raw coverage and counts; it is not a required gate or evidence of passing CI.
"""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(240)]

PRODUCTION = {
    "src/heinzel/heads/web/__init__.py": "8edf9ccde93c498615e451a4f59eb19ec4497061",
    "src/heinzel/modules/system/_github.py": "90122a55c714f83a967db56f90fa5c6dbe7f4e62",
    "src/heinzel/modules/system/plugin/__init__.py": "ce26c8d91c3ed2c916291daf7a022dee8ece236f",
    "src/heinzel/modules/system/plugin/_feed.py": "ee00ae50274911b5e0b430385b603295c6a3a62e",
    "src/heinzel/modules/system/plugin/_models.py": "db75e5937400548ef981ad062ad2b5f1d5d5ec57",
    "src/heinzel/modules/system/plugin/_service.py": "a623b70661d3aeac6e78769d0c6c94bc5598e3f2",
    "src/heinzel/modules/system/plugin/_showcase.py": "81784647b00f3ad25b2cd7fbcfd6c9a029cb1345",
    "src/heinzel/modules/system/plugin/_sources.py": "6a7e9a86337d6198e37bf051eb3999bc1cb46ddf",
}
CONFIG = "168f304d46388ad482e10da5edcf8fd497a3745f"
SELECTION = [
    "tests/unit/modules/plugin_feed_test.py",
    "tests/unit/modules/plugin_showcase_test.py",
    "tests/unit/modules/plugin_sources_test.py",
    "tests/unit/modules/plugin_service_test.py",
    "tests/unit/modules/system_service_test.py",
    "tests/unit/modules/plugin_needs_test.py",
    "tests/integration/theme_test.py",
    "tests/integration/plugin_extensions_test.py",
    "tests/integration/plugin_web_test.py",
    "tests/integration/web_pwa_test.py",
]


def test_capture_unchanged_linux_focused_coverage(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from scripts import focused_coverage, focused_job, focused_plan

    assert sys.platform == "linux"
    assert {path: focused_plan.git_blob("HEAD", path) for path in PRODUCTION} == PRODUCTION
    assert focused_plan.git_blob("HEAD", "pyproject.toml") == CONFIG
    scope = SimpleNamespace(production=PRODUCTION, config_blob=CONFIG)
    output = Path(os.environ["FOCUSED_OUT"]).resolve() / "linux-diagnostic"
    output.mkdir(parents=True, exist_ok=False)
    command = focused_job.plan("pytest", " ".join(SELECTION), output)[-1]
    env = {key: os.environ[key] for key in focused_coverage.ENV_KEYS if key in os.environ}
    env.update(UV_FROZEN="1", HZ_SCREENSHOTS=str(output / "screenshots"))
    focused_coverage._instrument(scope, command, output, env)
    config = focused_plan.git("cat-file", "blob", CONFIG)
    counts = focused_coverage.coverage_counts(scope, config, (output / "coverage.json").read_bytes(), enforce=False)
    (output / "counts.json").write_text(json.dumps(counts, sort_keys=True, indent=2) + "\n", encoding="utf-8")
