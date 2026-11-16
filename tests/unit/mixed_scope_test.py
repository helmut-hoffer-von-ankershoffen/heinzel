"""Mixed local plans execute owning checks without implying hosted/native acceptance."""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import mixed_scope as mixed  # noqa: E402
import related_tests  # noqa: E402

spec = importlib.util.spec_from_file_location("mixed_scoped_checks", REPO / "scripts/scoped_checks.py")
assert spec
assert spec.loader
scoped = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scoped)


@pytest.fixture
def scope(monkeypatch, tmp_path):
    """Bind disposable source and packet bytes; no real fixture or compiler runs."""
    for key in (
        "HZ_LOCAL_TEST_SCOPE",
        "HZ_LOCAL_TEST_SCOPE_SHA256",
        "HZ_LOCAL_MIXED_SCOPE",
        "HZ_LOCAL_MIXED_SCOPE_SHA256",
    ):
        monkeypatch.delenv(key, raising=False)
    changed = [
        "src/heinzel/heads/web/_widget.py",
        "tests/unit/heads/widget_fence_test.py",
        *mixed.WEB_OWNERS,
        "apple/Shared/WidgetCredentialStore.swift",
    ]
    python, web, integration, pending = mixed.requirements(changed)
    for name in {*changed, *web, *integration}:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    packet = {
        "schema": 2,
        "base": "a" * 40,
        "head": "b" * 40,
        "tree": "c" * 40,
        "owning": ["tests/unit/heads/widget_fence_test.py"],
        "hosted_only": [],
        "web": sorted(web),
        "integration": sorted(integration),
        "pending": sorted(pending),
    }
    monkeypatch.setattr(scoped, "ROOT", tmp_path)
    monkeypatch.setattr(scoped, "resolve_base", lambda value: "b" * 40 if value == "HEAD" else "a" * 40)
    monkeypatch.setattr(scoped, "resolve_tree", lambda: "c" * 40)
    monkeypatch.setattr(
        scoped.subprocess, "check_output", lambda argv, **__: "refs/heads/topic" if "symbolic-ref" in argv else ""
    )
    monkeypatch.setattr(scoped, "changed_paths", lambda mode, _: [] if mode == "fast" else changed)

    def related(paths):
        assert set(paths) == set(python)
        return ["tests/unit/heads/widget_fence_test.py"]

    monkeypatch.setattr(related_tests, "related_tests", related)
    path = tmp_path.parent / f"{tmp_path.name}-scope.json"

    def bind():
        data = json.dumps(packet).encode()
        path.write_bytes(data)
        monkeypatch.setenv("HZ_LOCAL_MIXED_SCOPE", str(path))
        monkeypatch.setenv("HZ_LOCAL_MIXED_SCOPE_SHA256", hashlib.sha256(data).hexdigest())

    bind()
    pushed = tmp_path.parent / f"{tmp_path.name}-pushed.txt"
    pushed.write_text(f"refs/heads/topic {'b' * 40} refs/heads/topic {'e' * 40}\n")
    monkeypatch.setenv("HZ_DEV_PUSH_REFS_FILE", str(pushed))
    monkeypatch.setenv("PRE_COMMIT", "1")
    monkeypatch.setenv("PREK_HOOK_STAGE", "pre-push")
    return packet, changed, bind


def test_preview_and_dispatch_match_and_leave_native_proof_pending(scope, monkeypatch, capsys):
    packet, changed, _ = scope
    # The environment cannot replace the validated Vitest step.
    monkeypatch.setenv("WEB_TEST_FILES", "--passWithNoTests")
    monkeypatch.setenv("WEB_TESTS", "")
    calls = []
    monkeypatch.setattr(scoped, "run_check", lambda argv, **_: calls.append(argv))
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push", "--show-mixed-push-plan"])
    scoped.main()
    plan = json.loads(capsys.readouterr().out)
    assert calls == []
    assert plan["early_guards"] == scoped.early_guard_commands(sorted(changed))
    assert plan["checks"][0] == [
        sys.executable,
        "scripts/mixed_scope.py",
        "check-fast",
        "--unit",
        "tests/unit/heads/widget_fence_test.py",
        "--web",
        "web/src/audio/AudioVisualizer.test.tsx",
    ]
    assert plan["checks"][1][2:] == ["integration", *packet["integration"]]
    assert plan["checks"][2][2:] == ["ios"]
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    scoped.main()
    assert calls == [*plan["early_guards"], *plan["checks"]]
    assert "PENDING:" in capsys.readouterr().out
    assert set(plan["pending"]) >= mixed.NATIVE_FIXTURES | mixed.HARNESS_INTEGRATION | {"full-hosted-validation"}


@pytest.mark.parametrize("key", ["web", "integration", "pending", "owning"])
def test_missing_owner_or_obligation_is_rejected_before_any_command(scope, monkeypatch, key):
    packet, _, bind = scope
    packet[key] = []
    bind()
    calls = []
    monkeypatch.setattr(scoped, "run_check", lambda *args, **_: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    with pytest.raises(SystemExit) as error:
        scoped.main()
    assert error.value.code == 2
    assert calls == []


@pytest.mark.parametrize("key", ["owning", "hosted_only"])
@pytest.mark.parametrize("node", ["::test_pass", "::test_parameter[alpha]"])
def test_mixed_packet_cannot_claim_node_level_proof(scope, monkeypatch, key, node):
    packet, _, bind = scope
    packet[key] = ["tests/unit/heads/widget_fence_test.py" + node]
    bind()
    calls = []
    monkeypatch.setattr(scoped, "run_check", lambda command, **_: calls.append(command))
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    with pytest.raises(SystemExit) as error:
        scoped.main()
    assert error.value.code == 2
    assert calls == []


def test_direct_recipe_cannot_bypass_whole_file_selection(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(mixed.subprocess, "run", lambda command, **_: calls.append(command))
    with pytest.raises(ValueError, match="bounded permitted"):
        mixed.check_fast(
            [
                "--unit",
                "tests/unit/mixed_scope_test.py::test_pass",
                "tests/unit/mixed_scope_test.py::test_skip",
                "--web",
                "web/src/audio/AudioVisualizer.test.tsx",
            ],
            tmp_path,
        )
    assert calls == []


@pytest.mark.parametrize(
    "payload",
    [
        "with space",
        "with'quote",
        'with"quote',
        "with\\slash",
        "with\nnewline",
        "`touch MARKER`",
        "$(shell touch MARKER)",
        '"`touch MARKER`"',
        "$(touch MARKER)",
    ],
)
def test_unsafe_report_paths_fail_before_make_or_shell_substitution(tmp_path, monkeypatch, payload):
    """A regression can run only the disposable recorder toolchain, never real test bodies."""
    marker = tmp_path / "substitution-ran"
    directory = tmp_path / payload.replace("MARKER", str(marker))
    toolchain = tmp_path / "bin"
    toolchain.mkdir()
    uv = toolchain / "uv"
    uv.write_text("#!/bin/sh\nexit 0\n")
    uv.chmod(0o700)
    monkeypatch.setenv("PATH", str(toolchain) + os.pathsep + os.environ["PATH"])
    calls = []
    real_run = subprocess.run

    def run(command, **kwargs):
        calls.append(command)
        return real_run(command, capture_output=True, text=True, **kwargs)

    monkeypatch.setattr(mixed.subprocess, "run", run)
    with pytest.raises(ValueError, match="safe for both Make and shell"):
        mixed.check_fast(
            [
                "--unit",
                "tests/unit/mixed_scope_test.py",
                "--web",
                "web/src/audio/AudioVisualizer.test.tsx",
            ],
            directory,
        )
    assert calls == []
    assert not marker.exists()


def test_actual_tmpdir_with_make_syntax_is_rejected_before_recipe_dispatch(tmp_path, monkeypatch):
    temporary_root = tmp_path / "unsafe$(shell false)"
    temporary_root.mkdir()
    monkeypatch.setenv("TMPDIR", str(temporary_root))
    monkeypatch.setattr(mixed.tempfile, "tempdir", None)
    calls = []
    monkeypatch.setattr(mixed.subprocess, "run", lambda command, **_: calls.append(command))
    with pytest.raises(ValueError, match="safe for both Make and shell"):
        mixed.run_recipe(
            [
                "check-fast",
                "--unit",
                "tests/unit/mixed_scope_test.py",
                "--web",
                "web/src/audio/AudioVisualizer.test.tsx",
            ]
        )
    assert calls == []
    assert list(temporary_root.iterdir()) == []  # the rejected recipe also removes its disposable scratch directory


@pytest.mark.parametrize("value", ["relative/unit.xml", "/safe/../unit.xml", "/safe/ユニット.xml"])
def test_report_operands_require_inert_absolute_paths(value):
    with pytest.raises(ValueError, match="safe for both Make and shell"):
        mixed.report_operand(Path(value))


def test_report_operands_allow_normal_macos_temporary_paths():
    value = "/var/folders/ab/Example_123/T/heinzel-mixed-check-abc/unit.xml"
    assert mixed.report_operand(Path(value)) == value


@pytest.mark.parametrize(
    "path",
    ["Makefile", "pyproject.toml", "web/package.json", "apple/New.swift", "tests/conftest.py", "desktop/main.ts"],
)
def test_unmapped_paths_are_not_dropped_from_the_python_graph(scope, path):
    _, changed, _ = scope
    with pytest.raises(ValueError, match="no owning recipe"):
        mixed.requirements([*changed, path])


@pytest.mark.parametrize("key", ["base", "head", "tree", "schema"])
def test_stale_or_wrong_packet_version_cannot_select_a_mixed_plan(scope, key):
    packet, changed, bind = scope
    packet[key] = 1 if key == "schema" else "d" * 40
    bind()
    with pytest.raises(ValueError, match="exact base/head/tree"):
        scoped.mixed_push_plan(changed, "a" * 40)


@pytest.mark.parametrize(
    "problem", ["untracked", "dirty", "digest", "conflicting", "unmapped", "deleted", "symlink", "missing-half"]
)
def test_unsafe_checkout_or_configuration_cannot_run_mixed_checks(scope, monkeypatch, tmp_path, problem):
    _, changed, _ = scope
    if problem == "untracked":
        monkeypatch.setattr(scoped, "changed_paths", lambda *_: ["new.py"])
    elif problem == "dirty":
        monkeypatch.setattr(scoped.subprocess, "check_output", lambda *_, **__: "modified.py")
    elif problem == "digest":
        monkeypatch.setenv("HZ_LOCAL_MIXED_SCOPE_SHA256", "0" * 64)
    elif problem == "conflicting":
        monkeypatch.setenv("HZ_LOCAL_TEST_SCOPE", "other.json")
    elif problem == "unmapped":
        monkeypatch.setattr(related_tests, "related_tests", lambda _: None)
    elif problem == "missing-half":
        monkeypatch.delenv("HZ_LOCAL_MIXED_SCOPE_SHA256")
    else:
        source = tmp_path / changed[0]
        source.unlink()
        if problem == "symlink":
            source.symlink_to(tmp_path / changed[1])
    with pytest.raises(ValueError, match="scope"):
        scoped.mixed_push_plan(changed, "a" * 40)


@pytest.mark.parametrize("key", ["web", "integration", "pending"])
@pytest.mark.parametrize("bad", [["--help"], ["bad;command"], ["../outside"], [1], ["same", "same"], "not-a-list"])
def test_packet_cannot_inject_recipes_or_disable_tests(scope, key, bad):
    packet, changed, bind = scope
    packet[key] = bad
    bind()
    with pytest.raises(ValueError, match="mixed scope"):
        scoped.mixed_push_plan(changed, "a" * 40)


def test_guard_failure_stops_before_make_or_native_compile(scope, monkeypatch):
    def fail(command, **_):
        raise subprocess.CalledProcessError(23, command)

    monkeypatch.setattr(scoped, "run_check", fail)
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    with pytest.raises(subprocess.CalledProcessError) as error:
        scoped.main()
    assert error.value.cmd[0:3] == ["uv", "run", "pytest"]


@pytest.mark.parametrize(
    "cases",
    [
        "",
        '<testcase classname="other"/>',
        '<testcase classname="tests.integration.web_widget_test"><skipped/></testcase>',
        '<testcase classname="tests.integration.web_widget_test"><failure/></testcase>',
    ],
)
def test_skipped_empty_or_wrong_file_integration_proof_is_rejected(tmp_path, cases):
    report = tmp_path / "report.xml"
    report.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
    with pytest.raises(ValueError, match="empty, skipped or unsuccessful"):
        mixed.pytest_result(report, ["tests/integration/web_widget_test.py"])


def test_fixed_recipes_compile_without_booting_and_validate_actual_pytest_report(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        for arg in command:
            if arg.startswith("--junitxml="):
                Path(arg.split("=", 1)[1]).write_text(
                    '<testsuites><testsuite><testcase classname="tests.integration.web_widget_test"/>'
                    "</testsuite></testsuites>"
                )

    monkeypatch.setattr(mixed.subprocess, "run", run)
    mixed.run_recipe(["integration", "tests/integration/web_widget_test.py"])
    mixed.run_recipe(["ios"])
    integration, native = calls
    assert integration[0][0:4] == ["uv", "run", "pytest", "tests/integration/web_widget_test.py"]
    assert native[0] == mixed.ios_command(Path(native[0][native[0].index("-derivedDataPath") + 1]))
    assert "CODE_SIGNING_ALLOWED=NO" in native[0]
    assert "generic/platform=iOS Simulator" in native[0]
    assert "-jobs" in native[0]
    assert native[0][-1] == "build"
    assert native[1] == {"check": True, "timeout": 900}


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["ios", "--help"],
        ["integration"],
        ["integration", "tests/integration/desktop_framework_wrap_test.py"],
        ["integration", "--collect-only"],
    ],
)
def test_recipe_entrypoint_rejects_arbitrary_commands(args):
    with pytest.raises(ValueError, match="unsupported mixed local recipe"):
        mixed.run_recipe(args)


def test_actual_make_recipe_preserves_checks_and_receives_only_selected_test_arguments(scope, monkeypatch, tmp_path):
    """Run Make with a recorder toolchain, never a test body or build preparation."""
    import scoped_checks as recipe_scoped

    monkeypatch.setattr(recipe_scoped, "ROOT", tmp_path)
    record = tmp_path / "commands.jsonl"
    executable = tmp_path / "uv"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['MIXED_COMMANDS_FILE'], 'a') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("MIXED_COMMANDS_FILE", str(record))
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("WEB_TESTS", "wrong")
    real_run = subprocess.run

    def run(command, **kwargs):
        result = real_run(command, cwd=REPO, **kwargs)
        (tmp_path / "unit.xml").write_text(
            '<testsuites><testcase classname="tests.unit.heads.widget_fence_test"/></testsuites>'
        )
        (tmp_path / "web.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "testResults": [
                        {
                            "name": str(tmp_path / "web/src/audio/AudioVisualizer.test.tsx"),
                            "assertionResults": [{"status": "passed"}],
                        }
                    ],
                }
            )
        )
        return result

    monkeypatch.setattr(mixed.subprocess, "run", run)
    mixed.check_fast(
        ["--unit", "tests/unit/heads/widget_fence_test.py", "--web", "web/src/audio/AudioVisualizer.test.tsx"], tmp_path
    )
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    assert "early-fast" in calls[0]
    steps = next(call for call in calls if "scripts/steps.py" in call)
    assert (
        "unit=uv run pytest tests/unit/heads/widget_fence_test.py "
        f"--junitxml={tmp_path / 'unit.xml'} "
        "-m 'unit and not perf' -n auto -x -q --session-timeout=120" in steps
    )
    assert (
        "web-tests=uv run --quiet --script scripts/web_build.py exec vitest run "
        "--maxWorkers=2 ./src/audio/AudioVisualizer.test.tsx --reporter=default --reporter=json "
        f"--outputFile.json={tmp_path / 'web.json'}" in steps
    )
    for name in (
        "format",
        "ruff",
        "contracts",
        "template",
        "docs",
        "design",
        "release-policy",
        "mypy",
        "pyright",
        "web-lint",
        "web-client",
        "web-types",
    ):
        assert any(arg.lstrip("&").startswith(name + "=") for arg in steps)


def test_source_changing_during_checks_cannot_be_reported_as_passed(scope, monkeypatch, capsys):
    def change_source(*_, **__):
        monkeypatch.setattr(scoped, "resolve_tree", lambda: "d" * 40)

    monkeypatch.setattr(scoped, "run_check", change_source)
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    with pytest.raises(SystemExit) as error:
        scoped.main()
    assert error.value.code == 2
    assert "Local mixed checks passed" not in capsys.readouterr().out


def mutate_binding(scope, monkeypatch, drift):
    packet, _, bind = scope
    if drift == "base":
        packet["base"] = "d" * 40
        bind()
    elif drift == "head":
        monkeypatch.setattr(scoped, "resolve_base", lambda _: "d" * 40)
    elif drift == "tree":
        monkeypatch.setattr(scoped, "resolve_tree", lambda: "d" * 40)
    elif drift == "bytes":
        Path(os.environ["HZ_LOCAL_MIXED_SCOPE"]).write_text(json.dumps(packet) + "\n")
    elif drift == "dirty":
        monkeypatch.setattr(scoped.subprocess, "check_output", lambda *_, **__: "changed.py")
    else:
        monkeypatch.setattr(scoped, "changed_paths", lambda *_: ["untracked.py"])


@pytest.mark.parametrize("phase", ["before-first", "after-guard", "after-final"])
@pytest.mark.parametrize("drift", ["base", "head", "tree", "bytes", "dirty", "untracked"])
def test_binding_drift_blocks_the_next_dispatch_and_success(scope, monkeypatch, capsys, phase, drift):
    _, changed, _ = scope
    original = scoped.mixed_push_plan
    plan = original(changed, "a" * 40)
    total = len(plan["early_guards"]) + len(plan["checks"])
    calls = []

    def initial(*args):
        result = original(*args)
        if phase == "before-first":
            mutate_binding(scope, monkeypatch, drift)
        return result

    def run(command, **_):
        calls.append(command)
        if len(calls) == (1 if phase == "after-guard" else total):
            mutate_binding(scope, monkeypatch, drift)

    monkeypatch.setattr(scoped, "mixed_push_plan", initial)
    monkeypatch.setattr(scoped, "run_check", run)
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    with pytest.raises(SystemExit) as error:
        scoped.main()
    assert error.value.code == 2
    assert len(calls) == {"before-first": 0, "after-guard": 1, "after-final": total}[phase]
    assert "Local mixed checks passed" not in capsys.readouterr().out


def test_replacing_reviewed_packet_with_equivalent_bytes_is_still_drift(scope, monkeypatch):
    original = scoped.mixed_push_plan
    calls = []

    def initial(*args):
        plan = original(*args)
        path = Path(os.environ["HZ_LOCAL_MIXED_SCOPE"])
        data = path.read_bytes() + b"\n"
        path.write_bytes(data)
        monkeypatch.setenv("HZ_LOCAL_MIXED_SCOPE_SHA256", hashlib.sha256(data).hexdigest())
        return plan

    monkeypatch.setattr(scoped, "mixed_push_plan", initial)
    monkeypatch.setattr(scoped, "run_check", lambda command, **_: calls.append(command))
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    with pytest.raises(SystemExit) as error:
        scoped.main()
    assert error.value.code == 2
    assert calls == []


def test_python_preview_cannot_accidentally_execute_a_mixed_packet(scope, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push", "--show-python-push-plan"])
    with pytest.raises(SystemExit) as error:
        scoped.main()
    assert error.value.code == 2


def test_selector_changes_require_both_owning_files(scope, tmp_path):
    packet, changed, _ = scope
    source = tmp_path / "scripts/mixed_scope.py"
    source.parent.mkdir()
    source.touch()
    with pytest.raises(ValueError, match="both selector test files"):
        mixed.validate(packet, [*changed, "scripts/mixed_scope.py"], tmp_path)
    assert "tests/unit/ci_test.py" in scoped.early_guard_commands(["scripts/mixed_scope.py"])[0]


def test_full_supported_candidate_inventory_preserves_python_and_all_native_obligations():
    changed = sorted(
        mixed.APPLE_SOURCES
        | mixed.NATIVE_FIXTURES
        | mixed.WIDGET_CORE
        | mixed.CORE_INTEGRATION
        | mixed.APPLE_INTEGRATION
        | mixed.HARNESS_INTEGRATION
        | mixed.WEB_OWNERS.keys()
        | {"apple/README.md", "tests/unit/heads/widget_fence_test.py"}
    )
    python, web, integration, pending = mixed.requirements(changed)
    assert set(python) == mixed.WIDGET_CORE | {"apple/README.md", "tests/unit/heads/widget_fence_test.py"}
    assert web == {"web/src/audio/AudioVisualizer.test.tsx"}
    assert integration == mixed.CORE_INTEGRATION | mixed.APPLE_INTEGRATION
    assert pending == mixed.NATIVE_FIXTURES | mixed.HARNESS_INTEGRATION | {
        "full-hosted-validation",
        "managed-backend-check",
    }


@pytest.mark.parametrize(
    "body",
    [
        "def test_body():\n    assert True\n",
        "import pytest\n@pytest.mark.skip\ndef test_body():\n    pass\n",
        "# no tests\n",
        "import pytest\ndef test_body():\n    assert True\n@pytest.mark.skip\ndef test_skipped():\n    pass\n",
    ],
)
def test_real_pytest_reports_require_executed_bodies_in_each_file(tmp_path, capsys, body):
    target = tmp_path / "tests/unit/owned_test.py"
    target.parent.mkdir(parents=True)
    target.write_text(body)
    (target.parent / "other_test.py").write_text("def test_other():\n    assert True\n")
    report = tmp_path / "report.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "/dev/null",
            "--rootdir",
            str(tmp_path),
            "--confcutdir",
            str(tmp_path),
            "tests/unit/owned_test.py",
            "tests/unit/other_test.py",
            "-q",
            f"--junitxml={report}",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""},
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0  # A passing sibling makes the empty/skipped owning file invisible to exit status.
    expected = ["tests/unit/owned_test.py", "tests/unit/other_test.py"]
    if "assert True" in body:
        mixed.pytest_result(report, expected, allow_partial_skips=True)
        if "test_skipped" in body:
            assert "1 skipped cases remain unverified" in capsys.readouterr().out
            with pytest.raises(ValueError, match="whole files only"):
                mixed.pytest_result(
                    report,
                    [
                        "tests/unit/owned_test.py::test_body",
                        "tests/unit/owned_test.py::test_skipped",
                    ],
                    allow_partial_skips=True,
                )
    else:
        with pytest.raises(ValueError, match="empty, skipped or unsuccessful"):
            mixed.pytest_result(report, expected, allow_partial_skips=True)


@pytest.mark.parametrize(
    "problem", [None, "all-skipped", "one-file-skipped", "empty", "failure", "wrong-file", "duplicate", "malformed"]
)
def test_vitest_requires_passing_bodies_for_each_exact_selected_file(tmp_path, problem):
    paths = ["web/src/first.test.ts", "web/src/second.test.ts"]
    data = {
        "success": True,
        "testResults": [{"name": str(tmp_path / path), "assertionResults": [{"status": "passed"}]} for path in paths],
    }
    if problem == "all-skipped":
        for result in data["testResults"]:
            result["assertionResults"] = [{"status": "pending"}]
    elif problem == "one-file-skipped":
        data["testResults"][1]["assertionResults"] = [{"status": "pending"}]
    elif problem == "empty":
        data["testResults"] = []
    elif problem == "failure":
        data["testResults"][0]["assertionResults"].append({"status": "failed"})
    elif problem == "wrong-file":
        data["testResults"][0]["name"] += ".other"
    elif problem == "duplicate":
        data["testResults"].append(data["testResults"][0])
    elif problem == "malformed":
        data = []
    report = tmp_path / "vitest.json"
    report.write_text(json.dumps(data))
    if problem is None:
        mixed.vitest_result(report, paths, tmp_path)
    else:
        with pytest.raises(ValueError, match="mixed Vitest"):
            mixed.vitest_result(report, paths, tmp_path)


@pytest.mark.parametrize("drift", ["base-ref", "changed-paths", "push-capture"])
@pytest.mark.parametrize("after_final", [False, True])
def test_fences_re_read_refs_diff_and_actual_push_input(scope, monkeypatch, capsys, drift, after_final):
    _, changed, _ = scope
    total = len(scoped.early_guard_commands(changed)) + 3
    calls = []

    def run(command, **_):
        calls.append(command)
        if len(calls) != (total if after_final else 1):
            return
        if drift == "base-ref":
            monkeypatch.setattr(scoped, "resolve_base", lambda value: "b" * 40 if value == "HEAD" else "f" * 40)
        elif drift == "changed-paths":
            monkeypatch.setattr(scoped, "changed_paths", lambda mode, _: [] if mode == "fast" else [*changed, "new.py"])
        else:
            Path(os.environ["HZ_DEV_PUSH_REFS_FILE"]).write_text(
                f"refs/heads/topic {'b' * 40} refs/heads/other {'e' * 40}\n"
            )

    monkeypatch.setattr(scoped, "run_check", run)
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    with pytest.raises(SystemExit) as error:
        scoped.main()
    assert error.value.code == 2
    assert len(calls) == (total if after_final else 1)
    assert "Local mixed checks passed" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "problem", ["missing", "outside-hook", "multi-ref", "non-head", "other-branch", "deletion", "tag"]
)
def test_mixed_execution_refuses_unbound_or_different_pushes(scope, monkeypatch, problem):
    captured = Path(os.environ["HZ_DEV_PUSH_REFS_FILE"])
    line = captured.read_text()
    if problem == "missing":
        monkeypatch.delenv("HZ_DEV_PUSH_REFS_FILE")
    elif problem == "outside-hook":
        monkeypatch.delenv("PREK_HOOK_STAGE")
    elif problem == "multi-ref":
        captured.write_text(line + line)
    elif problem == "non-head":
        captured.write_text(line.replace("b" * 40, "c" * 40))
    elif problem == "other-branch":
        captured.write_text(line.replace("refs/heads/topic", "refs/heads/other", 1))
    elif problem == "deletion":
        captured.write_text(f"(delete) {'0' * 40} refs/heads/topic {'e' * 40}\n")
    else:
        captured.write_text(line.replace("refs/heads/topic", "refs/tags/v1"))
    calls = []
    monkeypatch.setattr(scoped, "run_check", lambda command, **_: calls.append(command))
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    with pytest.raises(SystemExit) as error:
        scoped.main()
    assert error.value.code == 2
    assert calls == []


def test_standalone_preview_cannot_authorize_an_actual_push(scope, monkeypatch, capsys):
    monkeypatch.delenv("HZ_DEV_PUSH_REFS_FILE")
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push", "--show-mixed-push-plan"])
    scoped.main()
    assert json.loads(capsys.readouterr().out)["push"] is None
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    with pytest.raises(SystemExit) as error:
        scoped.main()
    assert error.value.code == 2


def test_preserved_push_input_is_bound_to_real_git_head_and_branch(tmp_path, monkeypatch):
    """Use a disposable Git repository; no remote, actual hook or product state is touched."""

    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=Example", "-c", "user.email=fixture@example.test", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()

    git("init", "--initial-branch=topic")
    (tmp_path / "source.txt").write_text("fixture\n")
    git("add", "source.txt")
    git("commit", "-m", "test(ci): create synthetic ref fixture")
    head = git("rev-parse", "HEAD")
    git("branch", "other")
    captured = tmp_path / "captured.txt"
    line = f"refs/heads/topic {head} refs/heads/topic {'0' * len(head)}\n"
    captured.write_text(line)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scoped, "ROOT", tmp_path)
    monkeypatch.setenv("HZ_DEV_PUSH_REFS_FILE", str(captured))
    monkeypatch.setenv("PRE_COMMIT", "1")
    monkeypatch.setenv("PREK_HOOK_STAGE", "pre-push")
    assert scoped.push_binding(required=True)["local_oid"] == head
    captured.write_text(line.replace("refs/heads/topic", "refs/heads/other", 1))
    with pytest.raises(ValueError, match="checked HEAD branch"):
        scoped.push_binding(required=True)
    captured.write_text(line)
    git("commit", "--allow-empty", "-m", "test(ci): move synthetic head")
    with pytest.raises(ValueError, match="checked HEAD branch"):
        scoped.push_binding(required=True)
