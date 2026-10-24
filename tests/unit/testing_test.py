"""heinzel.testing's test-suite policy: one level per test, fastest level first, each with its level's timeout."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from heinzel.testing import CATEGORIES, TIMEOUTS, pytest_collection_modifyitems

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
MARKERS = [f"{level}: description" for level in CATEGORIES]


@dataclass
class FakeItem:
    nodeid: str
    markers: dict[str, pytest.Mark] = field(default_factory=dict)

    def get_closest_marker(self, name: str) -> pytest.Mark | None:
        return self.markers.get(name)

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self.markers[marker.name] = marker.mark


def item(nodeid: str, *levels: str, timeout: int | None = None) -> FakeItem:
    markers = {level: getattr(pytest.mark, level).mark for level in levels}
    if timeout is not None:
        markers["timeout"] = pytest.mark.timeout(timeout).mark
    return FakeItem(nodeid, markers)


def config(markers: list[str], *, timeout_plugin: bool = True) -> pytest.Config:
    plugins = SimpleNamespace(hasplugin=lambda name: timeout_plugin and name == "timeout")
    return SimpleNamespace(getini=lambda name: markers if name == "markers" else [], pluginmanager=plugins)  # type: ignore[return-value]


def test_orders_tests_by_level_and_gives_each_its_timeout_unless_set():
    items = [item("e", "e2e"), item("i", "integration", timeout=60), item("u", "unit")]
    pytest_collection_modifyitems(config(MARKERS), items)  # type: ignore[arg-type]
    assert [i.nodeid for i in items] == ["u", "i", "e"]
    assert [i.markers["timeout"].args[0] for i in items] == [TIMEOUTS["unit"], 60, TIMEOUTS["e2e"]]


def test_refuses_tests_without_exactly_one_level():
    items = [item("none"), item("two", "unit", "e2e"), item("ok", "unit")]
    with pytest.raises(pytest.UsageError, match=r"none\n  two$"):
        pytest_collection_modifyitems(config(MARKERS), items)  # type: ignore[arg-type]


def test_leaves_projects_alone_that_dont_declare_the_levels():
    items = [item("b"), item("a", "e2e")]
    pytest_collection_modifyitems(config(["slow: something else"]), items)  # type: ignore[arg-type]
    assert [i.nodeid for i in items] == ["b", "a"]
    assert all("timeout" not in i.markers for i in items)


def test_sets_no_timeout_without_pytest_timeout():
    items = [item("u", "unit")]
    pytest_collection_modifyitems(config(MARKERS, timeout_plugin=False), items)  # type: ignore[arg-type]
    assert "timeout" not in items[0].markers


def test_this_suite_runs_with_the_policy(request):
    marker = request.node.get_closest_marker("timeout")
    assert marker
    assert marker.args[0] == TIMEOUTS["unit"]


def test_coding_standards_state_the_enforced_timeouts():
    """The tests table in docs/CODING_STANDARDS.md (shared with plugins) says what TIMEOUTS enforces."""
    standards = (REPO / "docs" / "CODING_STANDARDS.md").read_text(encoding="utf-8")
    documented = {
        level: int(seconds)
        for level, seconds in re.findall(r"^\| `(\w+)` \|.*\| (\d+) s\b[^|]*\|$", standards, re.MULTILINE)
    }
    assert documented == TIMEOUTS


@pytest.mark.parametrize("pyproject", ["pyproject.toml", "template/pyproject.toml.jinja"])
def test_marker_descriptions_state_the_enforced_timeouts(pyproject):
    text = (REPO / pyproject).read_text(encoding="utf-8")
    described = {level: int(seconds) for level, seconds in re.findall(r'"(\w+): [^"]*Timeout (\d+) s\b', text)}
    assert described == TIMEOUTS


def test_plugins_get_the_policy_from_heinzel_testing():
    """The template's conftest keeps fixtures only; a copy of the policy would drift (it had no timeouts)."""
    conftest = (REPO / "template" / "tests" / "conftest.py.jinja").read_text(encoding="utf-8")
    assert "pytest_collection_modifyitems" not in conftest
    assert "CATEGORIES" not in conftest


class Chatter:
    """A test-only chat adapter that reads and posts."""

    def messages(self, channel: str, window: object) -> list[object]:
        return [channel, window]

    def post(self, channel: str, text: str, thread: str | None = None) -> object:
        return (channel, text, thread)


def test_assert_adapter_accepts_a_class_or_an_instance_that_implements_its_modes():
    from heinzel.testing import assert_adapter

    assert_adapter(Chatter, "chat", modes={"read", "write"})
    assert_adapter(Chatter(), "chat")


def test_assert_adapter_names_missing_methods_and_other_parameters():
    from heinzel.testing import assert_adapter

    class Partial:
        def messages(self, channel: str) -> list[object]:
            return [channel]

        def transcripts(self, window: object) -> list[object]:
            return [window]

    with pytest.raises(AssertionError, match=r"messages takes \(channel\), the protocol \(channel, window\)"):
        assert_adapter(Partial, "chat")
    with pytest.raises(AssertionError, match=r"ended is missing \(mode read\); transcript is missing \(mode read\)"):
        assert_adapter(Partial(), "meetings")
    with pytest.raises(
        AssertionError, match=r"^Partial doesn't implement chat \(Chat\): post is missing \(mode write\)$"
    ):
        assert_adapter(Partial, "chat", modes={"write"})


def test_assert_adapter_compares_parameter_defaults_too():
    from heinzel.testing import assert_adapter

    class Poster:
        def post(self, channel: str, text: str, thread: str) -> object:
            return (channel, text, thread)

    with pytest.raises(
        AssertionError, match=r"post takes \(channel, text, thread\), the protocol \(channel, text, thread=None\)"
    ):
        assert_adapter(Poster, "chat", modes={"write"})


def test_assert_adapter_refuses_unknown_capabilities():
    from heinzel.testing import assert_adapter

    with pytest.raises(ValueError, match="Unknown capability 'spreadsheets'"):
        assert_adapter(Chatter, "spreadsheets")


def test_isolation_forces_the_environment_backend_and_drops_credential_variables(monkeypatch):
    from heinzel.testing import isolate_credentials

    monkeypatch.setenv("HZ_SYSTEM_CREDENTIAL__BACKEND", "keyring")
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_API_TOKEN", "from-the-developer")
    isolate_credentials(monkeypatch)
    assert os.environ["HZ_SYSTEM_CREDENTIAL__BACKEND"] == "environment"
    assert "HZ_CREDENTIAL_EXAMPLE_API_TOKEN" not in os.environ


def test_the_terminal_is_pinned_to_ci_width_and_no_colour():
    """CI runs at 80 columns with FORCE_COLOR set; a developer's terminal has neither, so tests pin both."""
    from heinzel.testing import pin_terminal

    environ = {"FORCE_COLOR": "1", "NO_COLOR": "1", "COLUMNS": "200"}
    pin_terminal(environ)
    assert environ["COLUMNS"] == environ["TERMINAL_WIDTH"] == "80"
    assert environ["_TYPER_FORCE_DISABLE_TERMINAL"] == "1"
    assert not {"FORCE_COLOR", "NO_COLOR"} & environ.keys()


def test_every_test_runs_in_the_pinned_terminal():
    assert os.environ["COLUMNS"] == "80"
    assert "FORCE_COLOR" not in os.environ


def test_help_wraps_at_the_pinned_width(hz):
    widest = max(len(line) for line in hz("--help", json=False).output.splitlines())
    assert widest <= 80


def test_no_test_checks_for_updates():
    """Every `hz` a test starts, in-process or not, inherits this (ADR 0031): the suite never asks GitHub."""
    assert os.environ["HZ_UPDATE_CHECK"] == "false"
