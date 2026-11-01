import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("scoped_checks", REPO / "scripts" / "scoped_checks.py")
assert spec
assert spec.loader
scoped = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scoped)


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
