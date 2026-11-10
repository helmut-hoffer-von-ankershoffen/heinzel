"""What `hz system update` says about a plugin it did not move: only what it knows."""

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from heinzel.kernel import CorePin
from heinzel.modules.system import _render
from heinzel.modules.system._models import PluginUpdate, UpdateResult
from heinzel.modules.system._render import render_update, standing
from heinzel.modules.system._update import _pinned, _plugin_step

pytestmark = pytest.mark.unit


def _unchanged(**fields: object) -> PluginUpdate:
    return PluginUpdate(distribution="heinzel-acme", status="unchanged", before="1.0.0", after="1.0.0", **fields)


def test_a_plugin_that_follows_a_branch_or_the_index_is_newest():
    assert standing(_unchanged()) == "already newest (1.0.0)"
    assert _plugin_step(_unchanged()) == ("done", "Already newest (1.0.0)")


def test_a_pinned_plugin_is_kept_never_called_newest():
    said = standing(_unchanged(pinned="v1.0.0"))
    assert said.startswith("kept at 1.0.0 (pinned to v1.0.0")
    assert "newest" not in said


def test_a_plugin_the_update_never_reached_is_not_updated_with_a_warning():
    update = _unchanged(checked=False)
    assert standing(update).startswith("not updated (1.0.0)")
    assert _plugin_step(update)[0] == "warn"


@pytest.mark.parametrize(
    ("kind", "ref", "pinned"),
    [
        ("release", "v1.0.0", "v1.0.0"),
        ("commit", "a" * 40, "a" * 40),
        ("index", "==1.0.0", "==1.0.0"),
        ("index", None, None),
        ("index", ">=1", None),
        ("default", None, None),
        ("ref", "main", None),
    ],
)
def test_pinned_reads_the_install_spec(kind, ref, pinned):
    assert _pinned(CorePin(kind=kind, requirement="heinzel-acme", ref=ref, name="heinzel-acme")) == pinned


@pytest.mark.parametrize("known", [{}, {"checked": True}, {"pinned": None}])
def test_an_older_hz_updating_itself_renders_with_its_own_models(known):
    """Incomplete legacy plugin models only establish that the version is unchanged."""
    old_plugin = SimpleNamespace(
        distribution="heinzel-acme", status="unchanged", before="1.0.0", after="1.0.0", **known
    )
    old_result = SimpleNamespace(message="Updated hz 0.15.0 → 0.16.0.", updated=True, plugins=[old_plugin])

    text = render_update(cast("UpdateResult", old_result)).plain

    assert text == "Updated hz 0.15.0 → 0.16.0.\nPlugins:\n  heinzel-acme: unchanged (1.0.0)"


def test_the_render_module_imports_no_model_an_older_hz_lacks():
    source = Path(_render.__file__).read_text(encoding="utf-8")
    runtime = source.split("if TYPE_CHECKING:")[0]
    assert "AutomaticUpdates" not in runtime
    assert "PluginUpdate" not in runtime


def test_the_stated_check_interval_is_the_web_servers():
    from heinzel.heads.web._auto_update import CHECK_EVERY
    from heinzel.modules.system import AUTOMATIC_CHECK_MINUTES

    assert CHECK_EVERY.total_seconds() == AUTOMATIC_CHECK_MINUTES * 60
