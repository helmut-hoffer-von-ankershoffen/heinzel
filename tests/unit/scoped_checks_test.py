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
