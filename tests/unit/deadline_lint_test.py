"""A wall-clock limit in a test goes through `heinzel.testing.scaled`, so a slow runner scales it (#885).

One concept needed eleven files in five directories when the macOS runners got slow (#885), and #919's first CI run
failed on one more. Every test file (unit, integration, end-to-end and every conftest.py; not the immutable fixtures)
may not hold a bare number of seconds: `timeout=20`, `thread.join(5)`, `event.wait(5)` and `time.sleep(0.1)` become
`scaled(20)` and so on. Waiting on a `FakeClock` is not a wall-clock limit and is not checked.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LINTED = sorted(
    path
    for pattern in ("*_test.py", "conftest.py")
    for path in (ROOT / "tests").rglob(pattern)
    if not path.relative_to(ROOT / "tests").as_posix().startswith("fixtures/")
)
LEGACY = {
    "tests/e2e/ai_access_install_ownership_test.py": 2,
    "tests/e2e/audio_visualizer_test.py": 1,
    "tests/e2e/auto_update_test.py": 4,
    "tests/e2e/chat_only_workshop_test.py": 2,
    "tests/e2e/chat_profile_color_test.py": 3,
    "tests/e2e/conftest.py": 4,
    "tests/e2e/connected_read_test.py": 3,
    "tests/e2e/crash_schedule_admission_test.py": 6,
    "tests/e2e/daemon_browser_proof_test.py": 2,
    "tests/e2e/daemon_install_ui_test.py": 1,
    "tests/e2e/daemon_legacy_recovery_test.py": 7,
    "tests/e2e/docs_test.py": 5,
    "tests/e2e/doctor_notice_recovery_test.py": 6,
    "tests/e2e/draft_workflow_test.py": 2,
    "tests/e2e/durable_five_minute_journey_test.py": 4,
    "tests/e2e/footer_panes_mobile_test.py": 2,
    "tests/e2e/generic_agent_chat_test.py": 3,
    "tests/e2e/isolated_build_cache_test.py": 2,
    "tests/e2e/notifier_face_test.py": 1,
    "tests/e2e/notifier_instances_test.py": 5,
    "tests/e2e/offline_uninstall_test.py": 17,
    "tests/e2e/plugin_dependency_cache_test.py": 1,
    "tests/e2e/plugin_search_test.py": 1,
    "tests/e2e/private_skill_mobile_test.py": 2,
    "tests/e2e/pypi_release_test.py": 1,
    "tests/e2e/read_set_approval_prototype_test.py": 3,
    "tests/e2e/release_test.py": 1,
    "tests/e2e/scheduled_connected_test.py": 4,
    "tests/e2e/scheduled_notification_review_test.py": 4,
    "tests/e2e/source_suggestion_test.py": 3,
    "tests/e2e/template_scheduled_test.py": 6,
    "tests/e2e/tool_install_test.py": 12,
    "tests/e2e/ui_service_test.py": 6,
    "tests/e2e/uv_tool_collision_test.py": 1,
    "tests/e2e/web_durable_composer_test.py": 4,
    "tests/e2e/web_ui_test.py": 58,
    "tests/integration/activity_import_test.py": 1,
    "tests/integration/apple_activity_catalog_test.py": 4,
    "tests/integration/apple_audio_samples_test.py": 2,
    "tests/integration/apple_browser_clear_test.py": 2,
    "tests/integration/apple_local_service_test.py": 2,
    "tests/integration/apple_prompt_dispatch_test.py": 1,
    "tests/integration/apple_prompt_lifecycle_test.py": 2,
    "tests/integration/apple_widget_enrollment_transport_test.py": 1,
    "tests/integration/approval_journal_process_test.py": 4,
    "tests/integration/auto_update_real_uv_test.py": 3,
    "tests/integration/changelog_test.py": 2,
    "tests/integration/chat_test.py": 2,
    "tests/integration/cli_test.py": 4,
    "tests/integration/collection_lanes_test.py": 1,
    "tests/integration/confidentiality_cli_test.py": 8,
    "tests/integration/confidentiality_hooks_test.py": 3,
    "tests/integration/config_list_test.py": 10,
    "tests/integration/connectors_mcp_stdio_test.py": 4,
    "tests/integration/docs_record_test.py": 1,
    "tests/integration/dummy_chat_test.py": 1,
    "tests/integration/durable_daemon_test.py": 2,
    "tests/integration/e2e_early_schedule_test.py": 1,
    "tests/integration/focused_coverage_worker_test.py": 1,
    "tests/integration/generate_docs_check_test.py": 1,
    "tests/integration/installation_test.py": 1,
    "tests/integration/integration_halves_test.py": 1,
    "tests/integration/integration_timing_test.py": 1,
    "tests/integration/lifecycle_commands_test.py": 4,
    "tests/integration/lifecycle_prepare_test.py": 1,
    "tests/integration/lifecycle_store_test.py": 1,
    "tests/integration/lifecycle_uninstall_test.py": 3,
    "tests/integration/managed_backend_broker_test.py": 7,
    "tests/integration/mcp_http_check_test.py": 3,
    "tests/integration/mcp_reader_scope_test.py": 2,
    "tests/integration/mobile_timeout_privacy_test.py": 1,
    "tests/integration/model_runtime_startup_test.py": 1,
    "tests/integration/notify_stream_test.py": 1,
    "tests/integration/plugin_scaffold_test.py": 6,
    "tests/integration/plugin_web_test.py": 6,
    "tests/integration/pr_test.py": 7,
    "tests/integration/private_api_test.py": 3,
    "tests/integration/pypi_release_test.py": 1,
    "tests/integration/python_lane_partition_test.py": 2,
    "tests/integration/pyyaml_bootstrap_test.py": 4,
    "tests/integration/readme_process_test.py": 9,
    "tests/integration/runner_process_test.py": 9,
    "tests/integration/self_chat_helper_test.py": 2,
    "tests/integration/session_repository_process_test.py": 10,
    "tests/integration/session_store_test.py": 1,
    "tests/integration/shell_installer_test.py": 1,
    "tests/integration/startup_render_imports_test.py": 1,
    "tests/integration/state_test.py": 6,
    "tests/integration/system_announced_trial_test.py": 2,
    "tests/integration/team_runner_test.py": 1,
    "tests/integration/team_test.py": 1,
    "tests/integration/theme_test.py": 2,
    "tests/integration/ui_service_test.py": 9,
    "tests/integration/ui_stop_test.py": 3,
    "tests/integration/ui_terminal_test.py": 1,
    "tests/integration/ui_test.py": 5,
    "tests/integration/web_auto_update_stream_test.py": 6,
    "tests/integration/web_chatgpt_signin_test.py": 11,
    "tests/integration/web_lifecycle_test.py": 4,
    "tests/integration/web_packaging_test.py": 3,
    "tests/integration/web_state_directory_test.py": 10,
    "tests/integration/web_widget_apple_contract_test.py": 5,
    "tests/integration/web_widget_fence_test.py": 2,
    "tests/integration/web_widget_test.py": 4,
    "tests/integration/web_widget_webkit_contract_test.py": 6,
}
"""Integration and end-to-end files with bare limits today, and how many: each count may only shrink (fix it, then
lower it here; a file at 0 leaves the list).

The integration files are converted in two following pull requests, each small enough for the local test step. The
end-to-end suites run on macOS only on main (a pull request runs them on Linux, where the scale is 1), so wrapping
them would first be tested by main's macOS lane; they are converted file by file on a pull request that runs them.
"""
WAITS = {"wait", "join", "result", "communicate"}
UNSCALED = re.compile(r"# unscaled: \S")
"""The exemption needs a reason after the colon."""

pytestmark = pytest.mark.unit


def _number(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool)


def bare_limits(source: str) -> list[int]:
    """The line numbers of calls that pass a literal number of seconds as a limit.

    A call whose lines carry `# unscaled: REASON` is a product value under test (a limit the code reports, a marker, a
    FakeClock deadline), not a wall-clock limit of the test, and is exempt.
    """
    found = []
    lines, tree = source.splitlines(), ast.parse(source)
    span = {}  # each call's innermost statement: the formatter may move the comment to the statement's last line
    for statement in (n for n in ast.walk(tree) if isinstance(n, ast.stmt)):
        for call in (n for n in ast.walk(statement) if isinstance(n, ast.Call)):
            body = getattr(statement, "body", None)  # a compound statement: only its header, never its body
            end = body[0].lineno - 1 if isinstance(body, list) and body else statement.end_lineno or statement.lineno
            lines_of = (statement.lineno, max(end, statement.lineno))
            if not lines_of[0] <= call.lineno <= lines_of[1]:
                continue  # the call is in the body: an inner statement owns it
            if call not in span or lines_of[1] - lines_of[0] < span[call][1] - span[call][0]:
                span[call] = lines_of
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        first, last = span.get(node, (node.lineno, node.end_lineno or node.lineno))
        if any(UNSCALED.search(line) for line in lines[first - 1 : last]):
            continue
        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
        owner = ast.unparse(function.value) if isinstance(function, ast.Attribute) else ""
        sleeping = name == "sleep" and owner == "time"
        limited = name in WAITS and node.args and _number(node.args[0])
        keyword = any(k.arg == "timeout" and _number(k.value) for k in node.keywords)
        if (sleeping and node.args and _number(node.args[0])) or limited or keyword:
            found.append(node.lineno)
    return found


def test_the_lint_finds_a_bare_limit_and_lets_scaled_ones_pass():
    assert bare_limits("run(cmd, timeout=20)\nthread.join(5)\ntime.sleep(0.1)\nevent.wait(2)") == [1, 2, 3, 4]
    assert bare_limits("run(cmd, timeout=scaled(20))\nthread.join(scaled(5))\nclock.sleep(3600)") == []
    assert bare_limits("check(adapter, timeout=0.05)  # unscaled: the limit the finding names") == []
    assert bare_limits("check(adapter, timeout=0.05)  # unscaled:") == [1], "an exemption names its reason"
    assert bare_limits("x = f(\n    timeout=0.05\n)  # unscaled: reflowed by the formatter") == []
    assert bare_limits("with f(timeout=1):\n    g()  # unscaled: the body never exempts the header") == [1]


@pytest.mark.parametrize("path", LINTED, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_a_limit_in_a_context_test_is_scaled(path):
    found = bare_limits(path.read_text(encoding="utf-8"))
    allowed = LEGACY.get(path.relative_to(ROOT).as_posix(), 0)
    assert len(found) <= allowed, f"wrap the seconds in heinzel.testing.scaled(...): lines {found}"
    assert len(found) == allowed, f"{len(found)} bare limits left: lower LEGACY to {len(found)} (it only shrinks)"


def test_the_lint_covers_every_level_and_the_shared_fixtures():
    linted = {path.relative_to(ROOT).as_posix() for path in LINTED}
    assert "tests/integration/model_runtime_startup_test.py" in linted  # timeout=12 was bare before
    assert "tests/unit/runtime/updater/system_update_all_test.py" in linted
    assert any(name.startswith("tests/e2e/") for name in linted)
    assert "tests/conftest.py" in linted
    assert not any(name.startswith("tests/fixtures/") for name in linted)
    assert set(LEGACY) <= linted
    assert all(name.startswith(("tests/e2e/", "tests/integration/")) for name in LEGACY), "unit tests hold none"
