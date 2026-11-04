"""Every file has one lane, and runtime markers are checked even after another hook deselects its cases."""

from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def reporter(request, monkeypatch, tmp_path):
    plugin = request.config.pluginmanager.get_plugin(str(Path(__file__).resolve().parents[1] / "conftest.py"))
    monkeypatch.setattr(plugin, "_ROOT", tmp_path)
    monkeypatch.setattr(plugin, "_LEVEL_LAYOUT", True)
    return plugin


def configuration(expression="(integration) and not perf", *, full=False, layout=True):
    return SimpleNamespace(
        stash={},
        getoption=lambda name: expression if name == "markexpr" else full,
        getini=lambda name: (["tests"] if layout else []) if name == "testpaths" else ["*_test.py"],
    )


@pytest.mark.parametrize("level", ["unit", "integration", "e2e"])
@pytest.mark.parametrize("full", [False, True])
def test_only_other_lanes_test_files_are_skipped(reporter, tmp_path, level, full):
    path = tmp_path / "tests" / level / "example_test.py"
    path.parent.mkdir(parents=True)
    path.touch()
    expected = True if level != "integration" and not full else None
    assert reporter.pytest_ignore_collect(path, configuration(full=full)) is expected
    assert reporter.pytest_ignore_collect(path.parent, configuration(full=full)) is None


@pytest.mark.parametrize(
    "path",
    [
        "unit/steps_signal_test.py",
        "unit/modules/autonomous_schedule_test.py",
        "unit/modules/user_chat_test.py",
        "unit/modules/scheduled_notifications_test.py",
    ],
)
def test_legacy_integration_files_keep_their_paths_and_scoped_fixtures(reporter, tmp_path, path):
    target = tmp_path / "tests" / path
    target.parent.mkdir(parents=True)
    target.touch()
    assert reporter._path_level(target) == "integration"
    assert reporter.pytest_ignore_collect(target, configuration()) is None


@pytest.mark.parametrize("expression", ["unit", "integration", "e2e", "(unit or integration) and not perf"])
def test_other_marker_modes_keep_normal_collection(reporter, tmp_path, expression):
    path = tmp_path / "tests" / "unit" / "example_test.py"
    path.parent.mkdir(parents=True)
    path.touch()
    assert reporter.pytest_ignore_collect(path, configuration(expression)) is None
    assert reporter.pytest_ignore_collect(path, configuration(layout=False)) is None


@pytest.mark.parametrize("full", [False, True])
def test_marker_reclassification_is_checked_even_after_deselection(reporter, tmp_path, full):
    markers = {"unit": object()}
    item = SimpleNamespace(
        path=tmp_path / "tests" / "unit" / "example_test.py",
        get_closest_marker=markers.get,
    )
    items = [item]
    wrapper = reporter.pytest_collection_modifyitems_layout(configuration(full=full), items)
    next(wrapper)
    markers.clear()
    markers["integration"] = object()
    items.clear()
    with pytest.raises(pytest.UsageError, match=r"tests/unit/example_test.py: expected unit"):
        next(wrapper)


def test_paths_without_a_lane_are_collected_then_rejected(reporter, tmp_path):
    path = tmp_path / "tests" / "unowned_test.py"
    path.parent.mkdir()
    path.touch()
    assert reporter.pytest_ignore_collect(path, configuration()) is None
    item = SimpleNamespace(path=path, get_closest_marker=lambda name: object() if name == "integration" else None)
    with pytest.raises(pytest.UsageError, match="expected a unit, integration or e2e directory"):
        list(reporter.pytest_collection_modifyitems_layout(configuration(), [item]))


def test_missing_or_mixed_markers_fail_before_a_lane_can_omit_them(reporter, tmp_path):
    for levels in (set(), {"unit", "integration"}):
        item = SimpleNamespace(
            path=tmp_path / "tests" / "unit" / "example_test.py",
            get_closest_marker=lambda name, levels=levels: object() if name in levels else None,
        )
        with pytest.raises(pytest.UsageError, match="exactly one level"):
            list(reporter.pytest_collection_modifyitems_layout(configuration(), [item]))
