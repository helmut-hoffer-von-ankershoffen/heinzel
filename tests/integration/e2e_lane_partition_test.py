"""Real collected E2E node IDs form the same complete disjoint union after CI allocation changes."""

import sys
from collections import Counter

import pytest

from tests._e2e_lanes import lane_commands

pytestmark = pytest.mark.integration


def selected(nodes: Counter[str], commands: list[list[str]]) -> Counter[str]:
    """Partition actual collected node IDs using the real recipe's file/node selectors; reject new selector syntax."""
    files = {node.partition("::")[0] for node in nodes}
    result: Counter[str] = Counter()
    for command in commands:
        assert command[:10] == [
            "uv",
            "run",
            "pytest",
            "-m",
            "e2e",
            "-n",
            "2" if sys.platform == "darwin" else "3",
            "-q",
            "--session-timeout=600",
            "-x",
        ]
        arguments = command[10:]
        assert len(arguments) == len(set(arguments))  # repeated file arguments must not hide duplicate collection
        targets = {arg for arg in arguments if not arg.startswith("-")}
        ignored = {arg.removeprefix("--ignore=") for arg in arguments if arg.startswith("--ignore=")}
        deselected = {arg.removeprefix("--deselect=") for arg in arguments if arg.startswith("--deselect=")}
        assert set(arguments) == targets | {f"--ignore={path}" for path in ignored} | {
            f"--deselect={node}" for node in deselected
        }
        # The existing Make recipes use whole files and one exact README node, with no expression matching.
        assert targets <= files | nodes.keys()
        assert ignored <= files
        assert not (targets and ignored)  # mixed explicit/ignored paths have additional pytest collection semantics
        assert not {target.partition("::")[0] for target in targets if "::" in target}.intersection(targets)
        assert deselected <= nodes.keys()
        result.update(
            {
                node: count
                for node, count in nodes.items()
                if (not targets or node in targets or node.partition("::")[0] in targets)
                and node.partition("::")[0] not in ignored
                and not node.startswith(tuple(deselected))
            }
        )
    return result


def test_ci_lanes_preserve_every_original_node_once_including_supervised_parameterizations(collected_e2e_nodes):
    groups = {suite: lane_commands(suite) for suite in ("", "cli", "supervised", "web-1", "web-2")}
    # Before the split, CLI ignored only browser files. Reconstruct that exact original selection.
    groups["original-cli"] = [
        [
            arg
            for arg in groups["cli"][-1]
            if arg not in {"--ignore=tests/e2e/ui_service_test.py", "--ignore=tests/e2e/offline_uninstall_test.py"}
        ]
    ]
    baseline = Counter(collected_e2e_nodes)
    lanes = {label: selected(baseline, commands) for label, commands in groups.items()}
    assert set(lanes) == set(groups)
    default = lanes.pop("")
    assert default == baseline
    old_cli = lanes.pop("original-cli")
    assert all(count == 1 for count in default.values())
    old_cli.update({"tests/e2e/docs_test.py::test_readme_instructions": 1})
    non_browser_supervised = Counter(
        {
            node: count
            for node, count in lanes["supervised"].items()
            if node.startswith(("tests/e2e/ui_service_test.py::", "tests/e2e/offline_uninstall_test.py::"))
        }
    )
    assert lanes["cli"] + non_browser_supervised == old_cli
    actual = sum(lanes.values(), Counter())
    assert actual == default
    assert all(count == 1 for count in actual.values())
    expected_supervised = {
        f"tests/e2e/ui_service_test.py::{name}[{platform}{suffix}]"
        for platform in ("linux", "darwin")
        for name, suffix in [
            ("test_installed_server_keeps_session_on_update_exec_and_pairing_after_cold_restart", ""),
            *[("test_fresh_installed_server_repeatedly_publishes_authenticated_control", f"-{i}") for i in range(5)],
        ]
    }
    expected_supervised.add(
        "tests/e2e/ui_service_test.py::test_installed_ui_group_is_inert_and_default_install_retains_profile_refusal"
    )
    expected_supervised.add(
        "tests/e2e/ui_service_test.py::test_installed_account_free_complete_profile_has_green_doctor"
    )
    expected_supervised.update(
        "tests/e2e/ui_service_browser_test.py::"
        f"test_installed_server_restarts_from_settings_and_reconnects_the_selected_profile[{platform}-{engine}]"
        for platform in ("linux", "darwin")
        for engine in ("chromium", "webkit")
    )
    expected_supervised.update(
        "tests/e2e/offline_uninstall_test.py::"
        f"test_installed_uninstall_needs_no_network_or_discovery_and_keeps_user_work[{change}]"
        for change in (
            "none",
            "old015-upgrade",
            "old015-web-migrate",
            "old015-web-retire",
            "old015-web-recovery",
            "old015-web-foreign",
            "recovery",
            "calendar",
            "calendar-recovery",
            "calendar-foreign",
            "tampered-receipt",
            "foreign-script",
            "missing-remover",
            "unsafe-remover",
        )
    )
    expected_supervised.add(
        "tests/e2e/offline_uninstall_test.py::test_network_denial_blocks_ip_but_preserves_local_unix_ipc"
    )
    assert set(lanes["supervised"]) == expected_supervised
    assert not set(lanes["supervised"]).intersection(lanes["cli"])
    assert "tests/e2e/tool_install_test.py::test_a_plugin_from_a_git_repository_is_added_with_confirm" in lanes["cli"]


@pytest.mark.parametrize(
    "arguments",
    [
        ["tests/e2e/sample_test.py", "tests/e2e/sample_test.py::test_one"],
        ["tests/e2e/sample_test.py", "tests/e2e/sample_test.py"],
        ["tests/e2e/sample_test.py", "--ignore=tests/e2e/sample_test.py"],
        ["--unknown-selector"],
        ["tests/e2e/missing_test.py"],
    ],
)
def test_partition_rejects_unmodeled_or_duplicate_collection(arguments):
    prefix = [
        "uv",
        "run",
        "pytest",
        "-m",
        "e2e",
        "-n",
        "2" if sys.platform == "darwin" else "3",
        "-q",
        "--session-timeout=600",
        "-x",
    ]
    with pytest.raises(AssertionError):
        selected(Counter({"tests/e2e/sample_test.py::test_one": 1}), [prefix + arguments])


def test_partition_matches_pytest_deselection_prefix_semantics():
    prefix = [
        "uv",
        "run",
        "pytest",
        "-m",
        "e2e",
        "-n",
        "2" if sys.platform == "darwin" else "3",
        "-q",
        "--session-timeout=600",
        "-x",
    ]
    nodes = Counter(
        {
            "tests/e2e/sample_test.py::test_one": 1,
            "tests/e2e/sample_test.py::test_one_extra": 1,
            "tests/e2e/sample_test.py::test_two": 1,
        }
    )
    assert selected(nodes, [[*prefix, "--deselect=tests/e2e/sample_test.py::test_one"]]) == Counter(
        {
            "tests/e2e/sample_test.py::test_two": 1,
        }
    )
