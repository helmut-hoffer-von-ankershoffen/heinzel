"""What `hz system update` says about a plugin it did not move: only what it knows."""

import pytest

from heinzel.kernel import CorePin
from heinzel.modules.system._models import PluginUpdate
from heinzel.modules.system._update import _pinned, _plugin_step

pytestmark = pytest.mark.unit


def _unchanged(**fields: object) -> PluginUpdate:
    return PluginUpdate(distribution="heinzel-acme", status="unchanged", before="1.0.0", after="1.0.0", **fields)


def test_a_plugin_that_follows_a_branch_or_the_index_is_newest():
    assert _unchanged().standing() == "already newest (1.0.0)"
    assert _plugin_step(_unchanged()) == ("done", "Already newest (1.0.0)")


def test_a_pinned_plugin_is_kept_never_called_newest():
    standing = _unchanged(pinned="v1.0.0").standing()
    assert standing.startswith("kept at 1.0.0 (pinned to v1.0.0")
    assert "newest" not in standing.replace("newer", "")


def test_a_plugin_the_update_never_reached_is_not_updated_with_a_warning():
    update = _unchanged(checked=False)
    assert update.standing().startswith("not updated (1.0.0)")
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
