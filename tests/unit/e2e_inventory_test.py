"""Only complete current-session E2E collection can supply the partition guard; partial roots fail closed."""

from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def inventory(request, monkeypatch, tmp_path):
    plugin = request.config.pluginmanager.get_plugin(str(Path(__file__).resolve().parents[1] / "conftest.py"))
    monkeypatch.setattr(plugin, "_ROOT", tmp_path)
    directory = tmp_path / "tests/e2e"
    directory.mkdir(parents=True)
    source = directory / "example_test.py"
    source.write_text("# The unit test supplies collector state without starting another pytest session.\n")
    node = "tests/e2e/example_test.py::test_one[darwin-0]"
    options = {"ignore": [], "ignore_glob": [], "pyargs": False, "deselect": [], "lf": False, "doctestmodules": False}
    patterns = ["test_*.py", "*_test.py"]
    config = SimpleNamespace(
        args=["tests"],
        invocation_params=SimpleNamespace(dir=tmp_path),
        getoption=options.__getitem__,
        getini={
            "python_files": patterns,
            "python_functions": ["test"],
            "python_classes": ["Test"],
            "testpaths": ["tests"],
        }.__getitem__,
        stash={plugin._E2E_FILES: {source}, plugin._E2E_NODES: (node,)},
    )
    return plugin, config, options, patterns, node


def test_complete_inventory_is_config_local_and_does_not_reuse_another_config(inventory):
    plugin, config, _options, _patterns, node = inventory
    assert plugin._complete_e2e_inventory(config) == (node,)
    fresh = SimpleNamespace(**{**vars(config), "stash": {}})
    with pytest.raises(AssertionError, match="file collection is incomplete"):
        plugin._complete_e2e_inventory(fresh)
    assert plugin._complete_e2e_inventory(config) == (node,)
    plugin.pytest_sessionstart(SimpleNamespace(config=config))
    with pytest.raises(AssertionError, match="file collection is incomplete"):
        plugin._complete_e2e_inventory(config)


@pytest.mark.parametrize(
    ("arguments", "option", "value", "message"),
    [
        (["tests/integration/e2e_lane_partition_test.py"], None, None, "Collect the whole tests/e2e"),
        (["tests/e2e/example_test.py::test_one"], None, None, "Collect the whole tests/e2e"),
        (["tests/e2e/nested"], None, None, "Collect the whole tests/e2e"),
        (["tests"], "ignore", ["tests/e2e/example_test.py"], "without --ignore"),
        (["tests"], "ignore_glob", ["*_test.py"], "without --ignore-glob"),
        (["tests"], "pyargs", True, "directory-based collection"),
        (["tests"], "lf", True, "without --lf"),
        (["tests"], "doctestmodules", True, "normal non-doctest collection"),
        (["tests"], "deselect", ["tests/e2e/example_test.py::test_one"], "without --deselect"),
    ],
)
def test_inventory_rejects_partial_roots_and_collection_filters(inventory, arguments, option, value, message):
    plugin, config, options, _patterns, _node = inventory
    config.args = arguments
    if option:
        options[option] = value
    with pytest.raises(AssertionError, match=message):
        plugin._complete_e2e_inventory(config)


@pytest.mark.parametrize("missing", ["module", "nodes", "patterns"])
def test_inventory_requires_every_actual_module_and_normal_discovery(inventory, missing):
    plugin, config, _options, patterns, _node = inventory
    message = "file collection is incomplete"
    if missing == "module":
        config.stash[plugin._E2E_FILES] = set()
    elif missing == "nodes":
        config.stash[plugin._E2E_NODES] = ()
        message = "actual collected nodes"
    else:
        patterns.clear()
        message = "normal file patterns"
    with pytest.raises(AssertionError, match=message):
        plugin._complete_e2e_inventory(config)


@pytest.mark.parametrize(
    ("setting", "message"),
    [("python_functions", "normal function patterns"), ("python_classes", "normal class patterns")],
)
def test_inventory_rejects_node_discovery_overrides(inventory, setting, message):
    plugin, config, _options, _patterns, _node = inventory
    original = config.getini
    config.getini = lambda name: ["selected_subset"] if name == setting else original(name)
    with pytest.raises(AssertionError, match=message):
        plugin._complete_e2e_inventory(config)
