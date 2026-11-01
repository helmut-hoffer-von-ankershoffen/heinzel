import importlib.util
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
