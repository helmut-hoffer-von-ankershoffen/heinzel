import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.timeout(60)]  # the import graph parses the tree: ~1 s idle, more loaded

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("scoped_checks", REPO / "scripts" / "scoped_checks.py")
assert spec
assert spec.loader
scoped = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scoped)
sys.path.insert(0, str(REPO / "scripts"))
import related_tests as scoped_related  # noqa: E402 - needs the scripts path above


@pytest.fixture(autouse=True)
def _without_host_gate(monkeypatch):
    """Keep dispatch tests independent of the developer's PATH."""
    monkeypatch.setattr(scoped.shutil, "which", lambda _: None)
    monkeypatch.delenv("HZ_FOCUSED_PUSH", raising=False)
    monkeypatch.delenv("HZ_FOCUSED_PUSH_MANIFEST", raising=False)


@pytest.fixture
def gate_calls(monkeypatch, tmp_path):
    gate = str(tmp_path / "hz-gate")
    monkeypatch.setattr(scoped.shutil, "which", lambda _: gate)
    calls = []
    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] == "git":
            return real_run(command, **kwargs)
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(scoped.subprocess, "run", run)
    return gate, calls


@pytest.mark.parametrize(("mode", "timeout"), [("preflight", 1800), ("fast", 300)])
def test_a_full_core_check_passes_the_actual_make_command_to_the_heavy_gate(monkeypatch, gate_calls, mode, timeout):
    gate, calls = gate_calls
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", mode, "--base", "origin/main"])
    monkeypatch.setattr(scoped, "resolve_base", lambda _: "a" * 40)
    monkeypatch.setattr(scoped, "changed_paths", lambda *_: ["README.md"])
    monkeypatch.setenv("HZ_GATE_LIGHT", "1")

    scoped.main()

    [(command, kwargs)] = calls
    target = ["preflight", "BASE=" + "a" * 40] if mode == "preflight" else ["check-fast"]
    assert command == [gate, "--timeout", str(timeout), "make", *target]
    assert "HZ_GATE_LIGHT" not in kwargs["env"]
    queue_wait = int(kwargs["env"]["HZ_GATE_MAX_WAIT"])
    assert kwargs["timeout"] > queue_wait + timeout  # the outer watchdog leaves room for admission and execution


def test_only_the_selected_documentation_push_checks_take_the_light_lane(monkeypatch, gate_calls):
    gate, calls = gate_calls
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    monkeypatch.setattr(scoped, "resolve_base", lambda _: "a" * 40)
    monkeypatch.setattr(scoped, "changed_paths", lambda *_: ["docs/guides/install.md"])

    with pytest.raises(SystemExit) as done:
        scoped.main()

    assert done.value.code == 0
    [(guard, guard_kwargs), (docs, docs_kwargs)] = calls
    assert guard == [gate, "--timeout", "180", *scoped.early_guard_commands(["docs/guides/install.md"])[0]]
    assert docs == [
        gate,
        "--timeout",
        "600",
        "uv",
        "run",
        "--quiet",
        "--script",
        "scripts/steps.py",
        *scoped.DOCS_STEPS,
    ]
    assert guard_kwargs["env"]["HZ_GATE_LIGHT"] == docs_kwargs["env"]["HZ_GATE_LIGHT"] == "1"


@pytest.mark.parametrize(
    ("requested", "expected"), [("0", "3600"), ("-1", "3600"), ("bad", "3600"), ("9999", "3600"), ("5", "5")]
)
def test_a_caller_cannot_skip_the_gate_or_raise_its_queue_deadline(monkeypatch, gate_calls, requested, expected):
    gate, calls = gate_calls
    monkeypatch.setenv("HZ_GATE_HELD", "4242")
    monkeypatch.setenv("HZ_GATE_MAX_WAIT", requested)

    scoped.run_check(["make", "check-fast"], timeout=300)

    [(command, kwargs)] = calls
    assert command == [gate, "--timeout", "300", "make", "check-fast"]
    assert kwargs["env"]["HZ_GATE_HELD"] == "4242"  # the host decides whether the holder is real
    assert kwargs["env"]["HZ_GATE_MAX_WAIT"] == expected
    assert kwargs["timeout"] == 2 * int(expected) + 310


@pytest.mark.parametrize("gate_path", [None, "bin/hz-gate"])
def test_checks_run_directly_with_the_same_runtime_limit_without_an_absolute_gate(monkeypatch, gate_calls, gate_path):
    _, calls = gate_calls
    monkeypatch.setattr(scoped.shutil, "which", lambda _: gate_path)

    scoped.run_check(["make", "check-fast"], timeout=300)

    [(command, kwargs)] = calls
    assert command == ["make", "check-fast"]
    assert kwargs["timeout"] == 300


def names(steps: list[str]) -> list[str]:
    return [step.lstrip("&").split("=", 1)[0] for step in steps]


def test_documents_alone_run_the_documentation_checks_and_no_web_or_unit_suite():
    steps = scoped.scoped_steps(["docs/guides/install.md", "README.md", "docs/overrides/home.html"])
    assert names(steps) == ["template", "docs", "demo", "typos", "tests"]


def test_site_assets_and_configuration_run_only_the_documentation_checks():
    steps = scoped.scoped_steps(["docs/assets/website/site.css", "mkdocs.yml"])
    assert names(steps) == ["template", "docs", "demo", "typos", "tests"]


def test_web_files_alone_run_the_web_checks_without_python_unit_tests_or_the_managed_backend():
    steps = scoped.scoped_steps(["web/src/components/ErrorScreen.tsx"])
    assert names(steps) == ["web-prepare", "web-lint", "web-client", "web-types", "web-tests"]


def test_documents_and_web_files_run_both():
    steps = scoped.scoped_steps(["docs/TEAMS.md", "web/src/index.css"])
    assert "docs" in names(steps)
    assert "web-lint" in names(steps)


@pytest.mark.parametrize(
    "changed",
    [
        [],
        ["src/heinzel/modules/system/updates.py"],
        ["docs/TEAMS.md", "Makefile"],
        ["web/package.json", "scripts/ship.py"],
        ["tests/unit/x_test.py"],
        ["docs/install.sh"],
    ],
)
def test_anything_else_runs_the_full_fast_gate(changed):
    assert scoped.scoped_steps(changed) is None


def test_the_scoped_commands_are_the_ones_make_check_fast_runs():
    """A scoped step that drifts from the Makefile would check something else than the full gate does."""
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    for step in scoped.DOCS_STEPS[:3]:
        assert step.split("=", 1)[1] in makefile
    for fragment in ("exec biome ci .", "client --check", "tsc --noEmit -p .", "exec vitest run"):
        assert fragment in makefile
        assert any(fragment in step for step in scoped.WEB_STEPS)


def test_the_document_tests_exist():
    assert [path for path in scoped.MARKDOWN_TESTS if not (REPO / path).is_file()] == []


@pytest.mark.parametrize(
    ("changed", "web"),
    [
        (["src/heinzel/kernel/_kernel.py", "tests/unit/x_test.py", "scripts/ship.py", "AGENTS.md"], False),
        (["web/src/components/A.tsx"], True),
        (["desktop/main.ts", "scripts/x.py"], True),
        (["pyproject.toml"], True),
        (["uv.lock", "src/heinzel/a.py"], True),
    ],
)
def test_only_changes_that_touch_the_web_app_run_its_tests(changed, web):
    assert scoped.touches_web(changed) is web


def test_docs_tooling_and_its_tests_run_the_documentation_checks_not_the_full_gate():
    changed = ["docs/TEAMS.md", "scripts/site_pages.py", "scripts/sync_template.py", "tests/unit/site_catalog_test.py"]
    steps = scoped.scoped_steps(changed)
    assert names(steps) == ["format", "ruff", "template", "docs", "demo", "typos", "tests"]
    tests = steps[-1]
    assert "tests/unit/site_catalog_test.py" in tests
    assert "tests/unit/documentation_test.py" in tests
    assert "tests/unit/ci_test.py" in tests
    assert "scripts/site_pages.py" in steps[0]
    assert "--check" in steps[0]


def test_docs_tooling_alone_is_enough_for_the_scope():
    assert scoped.scoped_steps(["scripts/check_docs.py"]) is not None
    assert scoped.scoped_steps(["tests/unit/sync_template_test.py"]) is not None


@pytest.mark.parametrize(
    "changed",
    [
        ["docs/TEAMS.md", "Makefile"],
        ["scripts/site_pages.py", "scripts/pr.sh"],
        ["scripts/site_pages.py", "scripts/steps.py"],
        ["scripts/site_pages.py", "src/heinzel/kernel/_kernel.py"],
        ["scripts/sitemap.py"],
        ["tests/unit/site_catalog_test.py", "tests/unit/ship_test.py"],
    ],
)
def test_anything_beyond_docs_and_their_tooling_runs_the_full_gate(changed):
    assert scoped.scoped_steps(changed) is None


def overrides(changed):
    return dict(item.split("=", 1) for item in scoped.python_overrides(changed))


def test_the_push_hook_runs_the_types_and_only_the_unit_tests_related_to_the_change():
    given = overrides(["src/heinzel/modules/system/_update.py"])
    targets = given["UNIT_TARGETS"].split()
    assert "tests/unit/modules/system_update_test.py" in targets
    assert 0 < len(targets) < 20  # not the ~340 test files of the full run
    assert "mypy" in given["FAST_TYPES"]
    assert "pyright" in given["FAST_TYPES"]
    assert given["WEB_TESTS"] == given["MANAGED_CHECK"] == ""


def test_a_changed_test_file_runs_itself_and_a_changed_script_runs_the_tests_that_name_it():
    assert overrides(["tests/unit/ship_test.py"])["UNIT_TARGETS"] == "tests/unit/ship_test.py"
    assert "tests/unit/ship_test.py" in overrides(["scripts/ship.py"])["UNIT_TARGETS"].split()


@pytest.mark.parametrize(
    "changed",
    [["pyproject.toml"], ["tests/conftest.py"], ["src/heinzel/modules/system/_update.py", "src/heinzel/x.json"]],
)
def test_changes_that_cannot_be_mapped_to_tests_keep_the_full_unit_run(changed):
    assert scoped_related.related_tests(changed) is None
    assert "UNIT_TARGETS" not in overrides(changed)


def test_a_change_no_test_reaches_still_runs_the_hooks_own_test():
    assert overrides(["docs/TEAMS.md", "scripts/__nothing_mentions_this__.py"])["UNIT_TARGETS"] == (
        "tests/unit/scoped_checks_test.py"
    )


def test_focused_push_keeps_early_guards_and_all_related_local_units(monkeypatch, gate_calls):
    gate, calls = gate_calls
    changed = ["src/heinzel/heads/_desktop_release.py"]
    safe = ["tests/unit/heads/desktop_release_progress_test.py", "tests/unit/scoped_checks_test.py"]
    hosted = [
        "tests/unit/heads/desktop_installation_test.py",
        "tests/integration/desktop_framework_wrap_test.py",
    ]
    monkeypatch.setattr(scoped_related, "related_tests", lambda _: [hosted[0], *safe, hosted[1]])
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    monkeypatch.setattr(scoped, "resolve_base", lambda _: "a" * 40)
    monkeypatch.setattr(scoped, "changed_paths", lambda *_: changed)
    monkeypatch.setenv("HZ_FOCUSED_PUSH", "1")

    with pytest.raises(SystemExit) as done:
        scoped.main()

    assert done.value.code == 0
    [(guard, _), (checks, _)] = calls
    assert guard == [gate, "--timeout", "180", *scoped.early_guard_commands(changed)[0]]
    assert checks == [
        gate,
        "--timeout",
        "1800",
        "make",
        "check-fast",
        "WEB_TESTS=",
        "MANAGED_CHECK=",
        f"FAST_TYPES={scoped.TYPES}",
        f"UNIT_TARGETS={' '.join(safe)}",
    ]
    assert overrides(changed)["UNIT_TARGETS"].split() == [hosted[0], *safe, hosted[1]]


@pytest.mark.parametrize("related", [None, [], ["tests/unit/heads/desktop_installation_test.py"]])
def test_focused_python_push_refuses_an_unknown_or_empty_local_selection(monkeypatch, related):
    monkeypatch.setattr(scoped_related, "related_tests", lambda _: related)

    with pytest.raises(SystemExit, match="requires at least one related unit test"):
        scoped.python_overrides(["src/heinzel/heads/_desktop_release.py"], focused=True)


@pytest.mark.parametrize("changed", [[], ["desktop/main.ts"], ["web/src/main.tsx"]])
def test_focused_push_refuses_routes_that_would_start_a_full_suite(monkeypatch, gate_calls, changed):
    _, calls = gate_calls
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    monkeypatch.setattr(scoped, "resolve_base", lambda _: "a" * 40)
    monkeypatch.setattr(scoped, "changed_paths", lambda *_: changed)
    monkeypatch.setenv("HZ_FOCUSED_PUSH", "1")

    with pytest.raises(SystemExit, match="requires a Python push"):
        scoped.main()

    assert calls == []


@pytest.fixture
def mixed_push(monkeypatch, tmp_path):
    base = "c" * 40
    head = scoped.resolve_base("HEAD")
    plan = {
        "base": base,
        "head": head,
        "unit": ["tests/unit/scoped_checks_test.py"],
        "integration": ["tests/integration/guides_test.py::test_the_guides_exist_and_cite_commands"],
        "web": ["web/src/updates/AutoUpdate.test.tsx"],
        "desktop": ["desktop/test/window.test.js"],
    }
    changed = ["src/heinzel/heads/cli/__init__.py", "web/src/updates/AutoUpdate.tsx", "desktop/src/main.js"]
    manifest = tmp_path / "focused.json"
    monkeypatch.setattr(scoped, "focused_push_head", lambda: head)
    monkeypatch.setenv("HZ_FOCUSED_PUSH", "1")
    monkeypatch.setenv("HZ_FOCUSED_PUSH_MANIFEST", str(manifest))
    return base, plan, changed, manifest


def test_focused_mixed_push_keeps_guards_static_checks_and_explicit_test_targets(gate_calls, mixed_push):
    gate, calls = gate_calls
    base, plan, changed, manifest = mixed_push
    manifest.write_text(json.dumps(plan), encoding="utf-8")

    assert scoped.push_checks(changed, base) == 0

    [(guard, _), (checks, _), (integration, _), (desktop, _)] = calls
    assert guard == [gate, "--timeout", "180", *scoped.early_guard_commands(changed)[0]]
    assert checks[:5] == [gate, "--timeout", "600", "make", "check-fast"]
    assert f"FAST_TYPES={scoped.TYPES}" in checks
    assert "UNIT_TARGETS=tests/unit/scoped_checks_test.py" in checks
    assert any("vitest run --maxWorkers=2 src/updates/AutoUpdate.test.tsx" in arg for arg in checks)
    managed = next(arg for arg in checks if arg.startswith("MANAGED_CHECK="))
    assert "runtime-code.mjs --check" in managed
    assert "biome ci ." in managed
    assert "$(MANAGED_TYPES)" in managed
    assert integration[3:7] == ["uv", "run", "pytest", *plan["integration"]]
    assert desktop[3:] == [*scoped.WEB.split(), "exec", "node", "--test", "../desktop/test/window.test.js"]


@pytest.fixture
def acme_mixed_push(mixed_push):
    base, plan, _, manifest = mixed_push
    plan.update(
        unit=["tests/unit/acme_setup_e2e_test.py", "tests/unit/scripts_test.py"],
        integration=[],
        web=["web/src/api/stream.test.ts", "web/src/layout/PluginsSection.test.tsx"],
        desktop=[],
    )
    changed = [
        "scripts/acme_setup_e2e.py",
        "src/heinzel/modules/system/plugin/_service.py",
        "web/openapi.json",
        "web/src/api/schema.d.ts",
    ]
    manifest.write_text(json.dumps(plan), encoding="utf-8")
    return base, plan, changed, manifest


def test_focused_acme_fixture_scope_runs_owning_checks(gate_calls, acme_mixed_push):
    gate, calls = gate_calls
    base, plan, changed, _ = acme_mixed_push

    assert scoped.push_checks(changed, base) == 0

    [(guard, _), (checks, _)] = calls
    assert guard == [gate, "--timeout", "180", *scoped.early_guard_commands(changed)[0]]
    assert checks[:5] == [gate, "--timeout", "600", "make", "check-fast"]
    assert f"FAST_TYPES={scoped.TYPES}" in checks
    assert f"UNIT_TARGETS={' '.join(plan['unit'])}" in checks
    web = next(arg for arg in checks if arg.startswith("WEB_TESTS="))
    assert web.endswith('vitest run --maxWorkers=2 src/api/stream.test.ts src/layout/PluginsSection.test.tsx"')


@pytest.mark.parametrize(
    ("lane", "omitted"),
    [
        ("unit", "tests/unit/acme_setup_e2e_test.py"),
        ("unit", "tests/unit/scripts_test.py"),
        ("web", "web/src/api/stream.test.ts"),
        ("web", "web/src/layout/PluginsSection.test.tsx"),
    ],
)
def test_focused_acme_fixture_refuses_missing_owning_checks(gate_calls, acme_mixed_push, lane, omitted):
    _, calls = gate_calls
    base, plan, changed, manifest = acme_mixed_push
    plan[lane].remove(omitted)
    manifest.write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(SystemExit, match="must include its owning test files") as error:
        scoped.push_checks(changed, base)

    assert omitted in str(error.value)
    assert calls == []


def test_focused_acme_fixture_refuses_an_untracked_script(monkeypatch, gate_calls, acme_mixed_push):
    _, calls = gate_calls
    base, _, changed, _ = acme_mixed_push
    monkeypatch.setattr(scoped, "tracked_test_files", lambda _, files: files - {"scripts/acme_setup_e2e.py"})

    with pytest.raises(SystemExit, match="tracked test files and fixture scripts"):
        scoped.push_checks(changed, base)

    assert calls == []


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("base", "a" * 40, "exact base and head"),
        ("head", "b" * 40, "exact base and head"),
        ("unit", [], "nonempty unit"),
        ("unit", ["tests/unit/heads/desktop_installation_test.py"], "hosted desktop"),
        ("integration", ["tests/integration/desktop_framework_wrap_test.py::test_unsafe"], "hosted desktop"),
        ("web", ["web/src"], "Invalid focused mixed test path"),
        ("web", ["web/src/updates/../updates/AutoUpdate.test.tsx"], "Invalid focused mixed test path"),
        ("web", ["web/src/updates/not_tracked.test.tsx"], "tracked test files"),
        ("desktop", [], "nonempty desktop"),
    ],
)
def test_focused_mixed_push_refuses_unsafe_manifests(gate_calls, mixed_push, field, value, message):
    _, calls = gate_calls
    base, plan, changed, manifest = mixed_push
    plan[field] = value
    manifest.write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(SystemExit, match=message):
        scoped.push_checks(changed, base)

    assert calls == []


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("uv.lock", "without build or runtime configuration"),
        ("web/vite.config.ts", "without build or runtime configuration"),
        ("scripts/foreign_fixture.py", "without build or runtime configuration"),
        ("tests/unit/related_tests_test.py", "cover every changed test file"),
    ],
)
def test_focused_mixed_push_refuses_uncovered_changes(gate_calls, mixed_push, extra, message):
    _, calls = gate_calls
    base, plan, changed, manifest = mixed_push
    manifest.write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(SystemExit, match=message):
        scoped.push_checks([*changed, extra], base)

    assert calls == []


def test_mixed_manifest_alone_leaves_default_push_selection_unchanged(monkeypatch, gate_calls, mixed_push):
    _, calls = gate_calls
    base, _, changed, _ = mixed_push
    monkeypatch.delenv("HZ_FOCUSED_PUSH")

    assert scoped.push_checks(changed, base) is None

    assert [command[3:] for command, _ in calls] == scoped.early_guard_commands(changed)


def test_focused_mixed_push_requires_the_manifest_head_to_be_the_only_pushed_ref(monkeypatch, tmp_path):
    head = "a" * 40
    monkeypatch.setattr(scoped, "resolve_base", lambda _: head)
    monkeypatch.setattr(scoped.subprocess, "check_output", lambda *_, **__: "")
    refs_file = tmp_path / "refs"
    monkeypatch.setenv("HZ_DEV_PUSH_REFS_FILE", str(refs_file))
    ref = f"refs/heads/work {head} refs/heads/work {'0' * 40}\n"
    refs_file.write_text(ref, encoding="utf-8")
    assert scoped.focused_push_head() == head

    refs_file.write_text(ref * 2, encoding="utf-8")
    with pytest.raises(SystemExit, match="exactly one pushed ref"):
        scoped.focused_push_head()

    refs_file.write_text(ref.replace(head, "b" * 40), encoding="utf-8")
    with pytest.raises(SystemExit, match="checked-out HEAD"):
        scoped.focused_push_head()


def test_imports_follow_the_defining_module_not_the_package_that_re_exports_it():
    """`from heinzel import Config` must not make every test depend on everything `heinzel` imports."""
    graph = scoped_related.graph()
    assert "src/heinzel/modules/system/_update.py" not in graph.imports["tests/conftest.py"]
    assert "tests/unit/modules/system_update_test.py" in graph.dependents({"src/heinzel/modules/system/_update.py"})


def test_make_check_fast_by_hand_is_the_full_unit_run():
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "UNIT_TARGETS ?=\n" in makefile
    assert "FAST_TYPES ?=\n" in makefile
    assert "uv run pytest $(UNIT_TARGETS) -m 'unit and not perf' -n auto" in makefile


def test_the_pre_push_hook_keeps_the_confidentiality_and_commit_checks():
    config = (REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "id: confidentiality-branch" in config
    assert "id: check-fast" in config
    assert "scoped_checks.py push" in config


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("docs/guides/install.md", {"guides"}),
        ("docs/guides/install.md.bak", set()),
        ("docs/ARCHITECTURE.md", set()),
        ("web/src/main.tsx", set()),
        ("plugins/example/module.py", set()),
        ("tests/fixtures/example.py", {"standards"}),
        ("template/tests/conftest.py", {"standards"}),
        ("src/heinzel/modules/system/__init__.py", {"standards", "guides"}),
        (
            ".github/workflows/ci.yml",
            {"browser_runtime_setup", "ci_supervised", "ci", "web_build", "e2e_lane_partition"},
        ),
        (
            "tests/e2e/offline_uninstall_test.py",
            {"standards", "browser_runtime_setup", "ci_supervised", "ci", "web_build", "e2e_lane_partition"},
        ),
        ("Makefile", {"browser_runtime_setup", "ci_supervised", "ci", "web_build", "e2e_lane_partition"}),
        (
            "tests/e2e/new_test.py",
            {"standards", "browser_runtime_setup", "ci_supervised", "ci", "web_build", "e2e_lane_partition"},
        ),
        (
            "tests/unit/web_build_test.py",
            {"standards", "browser_runtime_setup", "ci_supervised", "ci", "web_build", "e2e_lane_partition"},
        ),
    ],
)
def test_early_guards_select_only_owning_inputs(path, expected):
    commands = scoped.early_guard_commands([path])
    actual = {
        Path(argument.split("::")[0]).stem.removesuffix("_test")
        for command in commands
        for argument in command
        if argument.startswith("tests/") and (argument.endswith(".py") or "::" in argument)
    }
    assert actual == expected
    for command in commands:
        if "tests/e2e" in command:
            assert command[command.index("-m") + 1] == "integration"
            assert "tests/integration/e2e_lane_partition_test.py" in command


@pytest.mark.parametrize("path", ["tests/fixtures/example.py", "docs/guides/install.md", ".github/workflows/ci.yml"])
def test_push_stops_at_failing_existing_guard_before_any_full_or_scoped_commands(monkeypatch, path):
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", "push"])
    monkeypatch.setattr(scoped, "resolve_base", lambda _: "a" * 40)
    monkeypatch.setattr(scoped, "changed_paths", lambda *_: [path])
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        assert kwargs["check"] is True
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(scoped.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        scoped.main()
    assert calls == scoped.early_guard_commands([path])[:1]


@pytest.mark.parametrize("target", ["check-fast", "preflight"])
def test_direct_make_stops_before_web_preparation_or_full_checks_when_early_guard_fails(tmp_path, target):
    """Execute the real Make recipe with a failing guard runner; no product command can run."""
    executable = tmp_path / "uv"
    record = tmp_path / "commands"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$EARLY_GUARD_RECORD"\nexit 23\n')
    executable.chmod(0o700)
    result = subprocess.run(
        ["make", "--no-print-directory", target],
        cwd=REPO,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "EARLY_GUARD_RECORD": str(record)},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode != 0
    commands = record.read_text().splitlines()
    assert len(commands) == 1
    assert "scripts/scoped_checks.py early-" in commands[0]
    assert ("early-preflight --base" in commands[0]) is (target == "preflight")


def test_core_early_guards_are_not_copied_into_the_plugin_template():
    spec = importlib.util.spec_from_file_location("sync_template_early", REPO / "scripts/sync_template.py")
    assert spec
    assert spec.loader
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
    assert "scoped_checks.py early-" not in sync.makefile()


@pytest.mark.parametrize("mode", ["early-fast", "early-preflight"])
def test_direct_make_guards_include_branch_and_uncommitted_inputs(monkeypatch, mode):
    monkeypatch.setattr(sys, "argv", ["scoped_checks.py", mode, "--base", "HEAD~1"])
    monkeypatch.setattr(scoped, "resolve_base", lambda base: "a" * 40 if base == "HEAD~1" else None)
    calls = []

    def changed(mode, base):
        assert base == "a" * 40
        return ["docs/guides/install.md"] if mode == "preflight" else ["tests/fixtures/example.py"]

    monkeypatch.setattr(scoped, "changed_paths", changed)
    monkeypatch.setattr(scoped, "run_early_guards", calls.append)
    scoped.main()
    assert calls == [["docs/guides/install.md", "tests/fixtures/example.py"]]


def test_early_ci_guards_use_whole_owning_files_so_test_renames_do_not_break_preflight():
    command, partition = scoped.early_guard_commands([".github/workflows/ci.yml"])
    assert {argument for argument in command if argument.startswith("tests/")} == {
        "tests/unit/browser_runtime_setup_test.py",
        "tests/unit/ci_supervised_test.py",
        "tests/unit/ci_test.py",
        "tests/unit/web_build_test.py",
    }
    assert "tests/e2e" in partition
    assert partition[partition.index("-m") + 1] == "integration"
