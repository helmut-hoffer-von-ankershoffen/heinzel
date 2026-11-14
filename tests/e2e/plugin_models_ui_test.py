"""A plugin's own AI models in the real Settings, on an isolated `hz ui serve` with a made-up plugin (ADR 0159)."""

import tomllib
from pathlib import Path

import pytest

from tests.e2e.web_ui_test import drive_ui_script, environment  # noqa: F401 - pytest fixture

pytestmark = pytest.mark.e2e
REPO = Path(__file__).resolve().parents[2]


@pytest.mark.timeout(600)
def test_a_plugins_models_are_picked_as_ai_access_and_a_model_in_settings(environment, tmp_path):  # noqa: F811
    """Pick the plugin's option, then one of its models: both land in the user's configuration and it answers."""
    env, _recorded = environment
    metadata = tmp_path / "models-plugin/heinzel_models_demo-1.0.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel-models-demo\nVersion: 1.0.0\n")
    (metadata / "entry_points.txt").write_text("[heinzel.modules]\nmodels-demo = heinzel_models_demo\n")
    env = env | {
        "PYTHONPATH": ":".join((env["PYTHONPATH"], str(metadata.parent), str(REPO / "tests/fixtures/ai_models")))
    }

    result = drive_ui_script(env, tmp_path, "plugin-models.mjs")

    assert result == {"picked": True, "unavailableMarked": True, "answered": True}
    config = tomllib.loads((Path(env["HZ_CONFIG_HOME"]) / "config.toml").read_text(encoding="utf-8"))
    assert config["capabilities"]["ai_access"] == "demo-models"
    assert config["ai"]["access"]["plugin_model"] == "demo-deep"
