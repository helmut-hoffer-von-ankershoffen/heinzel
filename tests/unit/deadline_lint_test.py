"""A wall-clock limit in a test goes through `heinzel.testing.scaled`, so a slow runner scales it (#885).

One concept needed eleven files in five directories when the macOS runners got slow (#885), and #919's first CI run
failed on one more. Every test file (unit, integration, end-to-end and every conftest.py; not the immutable fixtures)
may not hold a bare number of seconds: `timeout=20`, `thread.join(5)`, `event.wait(5)` and `time.sleep(0.1)` become
`scaled(20)` and so on. Waiting on a `FakeClock` is not a wall-clock limit and is not checked.
"""

import ast
import io
import re
import tokenize
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
    "tests/integration/apple_audio_samples_test.py": 2,
    "tests/integration/apple_browser_clear_test.py": 2,
    "tests/integration/apple_local_service_test.py": 2,
    "tests/integration/apple_prompt_dispatch_test.py": 1,
    "tests/integration/apple_widget_enrollment_transport_test.py": 1,
    "tests/integration/approval_journal_process_test.py": 4,
    "tests/integration/auto_update_real_uv_test.py": 3,
    "tests/integration/chat_test.py": 2,
    "tests/integration/cli_test.py": 4,
    "tests/integration/confidentiality_cli_test.py": 8,
    "tests/integration/confidentiality_hooks_test.py": 3,
    "tests/integration/config_list_test.py": 10,
    "tests/integration/connectors_mcp_stdio_test.py": 4,
    "tests/integration/docs_record_test.py": 1,
    "tests/integration/dummy_chat_test.py": 1,
    "tests/integration/durable_daemon_test.py": 2,
    "tests/integration/e2e_early_schedule_test.py": 1,
    "tests/integration/focused_coverage_worker_test.py": 1,
    "tests/integration/installation_test.py": 1,
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
    "tests/integration/readme_process_test.py": 9,
    "tests/integration/runner_process_test.py": 9,
    "tests/integration/self_chat_helper_test.py": 2,
    "tests/integration/session_repository_process_test.py": 10,
    "tests/integration/session_store_test.py": 1,
    "tests/integration/shell_installer_test.py": 1,
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


def _contexts(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Give statements and individual decorators separate annotation ownership."""
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    decorators = {item for node in ast.walk(tree) for item in getattr(node, "decorator_list", [])}
    contexts = {}
    for node in ast.walk(tree):
        owner = node
        while owner in parents and not isinstance(owner, ast.stmt) and owner not in decorators:
            owner = parents[owner]
        contexts[node] = owner
    return contexts


def _context_contains(owner: ast.AST, line: int) -> bool:
    body = getattr(owner, "body", None)
    end = body[0].lineno - 1 if isinstance(body, list) and body else owner.end_lineno
    return owner.lineno <= line <= max(end, owner.lineno)


def _annotation_call(token, calls, contexts, candidates):
    """Resolve local calls before considering a formatter's statement footer."""
    line, column = token.start
    owners = {owner for owner in contexts.values() if isinstance(owner, ast.stmt) and _context_contains(owner, line)}
    if len(owners) > 1:
        return None  # semicolons and inline suites do not have a unique statement
    local = [
        call
        for call in calls
        if call.lineno <= line <= call.end_lineno and (call.lineno < line or call.col_offset < column)
    ]
    # A comment inside a wrapper belongs to the calls closing on that line,
    # not to a deadline elsewhere in the wrapper.
    closing = [call for call in local if call.end_lineno == line]
    scope = closing or local or [call for call in calls if _context_contains(contexts[call], line)]
    nested = {node for call in scope for node in ast.walk(call) if isinstance(node, ast.Call)}
    matching = [call for call in nested if call in candidates]
    if len(matching) != 1:
        return None
    deadline = matching[0]
    # Shared closing lines cannot identify an owner, even for nested calls.
    # Moving the comment to a wrapper footer must not restore the exemption.
    if len({call.end_lineno for call in nested}) != len(nested):
        return None
    # Separate closing lines still need a single chain, without siblings.
    if all(call in ast.walk(deadline) or deadline in ast.walk(call) for call in nested):
        return deadline
    return None


def _bare_call(node: ast.Call) -> bool:
    function = node.func
    name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
    owner = ast.unparse(function.value) if isinstance(function, ast.Attribute) else ""
    positional = name in WAITS or (name == "sleep" and owner == "time")
    if positional and node.args and _number(node.args[0]):
        return True
    return any(k.arg == "timeout" and _number(k.value) for k in node.keywords)


def bare_limits(source: str) -> list[int]:
    """The line numbers of calls that pass a literal number of seconds as a limit.

    `# unscaled: REASON` belongs to a unique local call context, including wrappers whose calls close on separate lines.
    Shared closing lines, ambiguous statements and sibling calls exempt nothing.
    """
    tree = ast.parse(source)
    contexts = _contexts(tree)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    candidates = [node for node in calls if _bare_call(node)]
    exempt = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT or not UNSCALED.match(token.string):
            continue
        call = _annotation_call(token, calls, contexts, candidates)
        if call is not None:
            exempt.add(call)
    return [node.lineno for node in candidates if node not in exempt]


def test_the_lint_finds_a_bare_limit_and_lets_scaled_ones_pass():
    assert bare_limits("run(cmd, timeout=20)\nthread.join(5)\ntime.sleep(0.1)\nevent.wait(2)") == [1, 2, 3, 4]
    assert bare_limits("run(cmd, timeout=scaled(20))\nthread.join(scaled(5))\nclock.sleep(3600)") == []
    assert bare_limits("check(adapter, timeout=0.05)  # unscaled: the limit the finding names") == []
    assert bare_limits("check(adapter, timeout=0.05)  # unscaled:") == [1], "an exemption names its reason"
    assert bare_limits("x = f(\n    timeout=0.05\n)  # unscaled: reflowed by the formatter") == []
    assert bare_limits("with f(timeout=1):\n    g()  # unscaled: the body never exempts the header") == [1]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("x = (clock.wait(1), event.wait(2))  # unscaled: fake clock\n", [1, 1]),
        ("x = (\n    clock.wait(1),\n    event.wait(2),\n)  # unscaled: fake clock\n", [2, 3]),
        ("clock.wait(1); event.wait(2)  # unscaled: fake clock\n", [1, 1]),
        ("outer(timeout=1, value=event.wait(2))  # unscaled: product limit\n", [1, 1]),
        ("event.wait(2); clock.sleep(1)  # unscaled: fake clock\n", [1]),
        ("event.wait(2); reason = 1  # unscaled: unrelated assignment\n", [1]),
        ("x = (event.wait(2), clock.sleep(1))  # unscaled: fake clock\n", [1]),
        ("x = (clock.sleep(1), event.wait(2))  # unscaled: fake clock\n", [1]),
        ("@wrapper(check(timeout=1), clock.sleep(1))  # unscaled: ambiguous\ndef f():\n    pass", [1]),
    ],
)
def test_ambiguous_annotations_do_not_exempt_deadlines(source, expected):
    assert bare_limits(source) == expected


@pytest.mark.parametrize("container", ["({calls})", "[{calls}]", "wrapper({calls})", "wrapper(inner({calls}))"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("multiline", [False, True])
@pytest.mark.parametrize("decorator", [False, True])
def test_non_deadline_siblings_make_every_footer_ambiguous(container, reverse, multiline, decorator):
    calls = ["event.wait(2)", "clock.sleep(1)"]
    if reverse:
        calls.reverse()
    separator = ",\n    " if multiline else ", "
    contents = "\n    " + separator.join(calls) + ",\n" if multiline else separator.join(calls)
    expression = container.format(calls=contents)
    prefix = "@" if decorator else "x = "
    suffix = "def f():\n    pass\n" if decorator else ""
    source = f"{prefix}{expression}  # unscaled: fake clock\n{suffix}"
    assert bare_limits(source) == [2 + int(reverse) if multiline else 1]


def test_a_reason_inside_a_nested_non_deadline_call_cannot_exempt_its_parent():
    assert bare_limits("check(\n    clock.sleep(1),  # unscaled: fake clock\n    timeout=2,\n)") == [1]
    assert bare_limits("wrapper(\n    event.wait(2),\n    clock.sleep(1),  # unscaled: fake clock\n)") == [2]


def test_an_annotation_on_one_call_keeps_other_deadlines_tracked():
    assert bare_limits("x = (\n    clock.wait(1),  # unscaled: fake clock\n    event.wait(2),\n)") == [3]
    assert bare_limits('event.wait(2); reason = "# unscaled: fake clock"') == [1]
    assert bare_limits("items = [\n    item('e'),\n    item('i', timeout=60),  # unscaled: marker under test\n]") == []
    assert bare_limits("x = wrapper(\n    check(timeout=0.05)\n)  # unscaled: adapter limit") == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("@wrapper(\n    check(timeout=1)\n)  # unscaled: product limit\ndef f():\n    pass", []),
        ("@check(timeout=1)\n@other()  # unscaled: unrelated decorator\ndef f():\n    pass", [1]),
        ("@check(timeout=1)\ndef f():\n    clock.sleep(1)  # unscaled: fake clock", [1]),
        ("@other()  # unscaled: unrelated decorator\ndef f():\n    event.wait(2)", [3]),
        ("x = wrapper(\n    check(\n        Slow(),\n        timeout=0.05,\n    )\n)  # unscaled: adapter limit", []),
        ("hung = option(adapter, timeout=0.3)[0].test_model(\n    'slow'\n)  # unscaled: product limit", []),
        ("with guard():\n    event.wait(2)  # unscaled: product limit", []),
        ("result = event.wait(2)  # unscaled: product limit", []),
        ("clock.sleep(1)  # unscaled: fake clock\nevent.wait(2)", [2]),
    ],
)
def test_annotations_follow_their_statement_or_individual_decorator(source, expected):
    assert bare_limits(source) == expected


@pytest.mark.parametrize("footer", [False, True])
@pytest.mark.parametrize(
    "expression",
    [
        "check(clock.sleep(1), timeout=2)",
        "check(Slow(), timeout=2)",
        "wrapper(check(timeout=2))",
        "(event.wait(2), clock.sleep(1))",
        "(clock.sleep(1), event.wait(2))",
    ],
)
def test_shared_closing_lines_cannot_identify_an_annotation_owner(expression, footer):
    source = f"wrapper(\n    {expression}\n)" if footer else expression
    assert bare_limits(source + "  # unscaled: fake clock\n") == [2 if footer else 1]


def test_typed_decorator_requires_separate_call_closing_lines():
    ambiguous = "@typed(check(clock.sleep(1), timeout=2))  # unscaled: fake clock\ndef f():\n    pass"
    assert bare_limits(ambiguous) == [1]
    isolated = (
        "@typed(\n    check(\n        clock.sleep(1),\n        timeout=2,\n"
        "    )  # unscaled: product limit\n)\ndef f():\n    pass"
    )
    assert bare_limits(isolated) == []
    documented = (
        "@check(timeout=1)  # unscaled: decorator option under test\n"
        'def f(value: int) -> None:\n    """A documented typed function."""\n    pass\n'
    )
    assert bare_limits(documented) == []


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
