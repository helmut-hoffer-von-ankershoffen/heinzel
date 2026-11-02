"""Scripts declare their own dependencies (PEP 723) and run without the project, unless they import heinzel."""

import re
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((REPO / "scripts").glob("*.py"))
PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
LOCKED = {
    package["name"]: package.get("version")
    for package in tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))["package"]
}

# The reference regex of https://packaging.python.org/en/latest/specifications/inline-script-metadata/.
METADATA = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")
# A script that imports heinzel says so in a comment starting with this, and runs with `uv run python`.
PROJECT_BOUND = "# Project-bound: runs in the project environment (`uv run python`)"

# Where scripts are run from: make targets, git hooks, other scripts, workflows (the template's are symlinks or
# generated from these) and Claude Code's permission rules.
CALLERS = [
    REPO / "Makefile",
    REPO / "template" / "Makefile",
    REPO / ".pre-commit-config.yaml",
    REPO / ".claude" / "settings.json",
    REPO / "template" / ".github" / "workflows" / "ci.yml.jinja",
    *sorted((REPO / "scripts").glob("*.sh")),
    *sorted((REPO / ".github" / "workflows").glob("*.yml")),
]
CALL = re.compile(
    r"(?P<runner>\buv run(?P<flags>(?: --python [0-9]+(?:\.[0-9]+)?| -[\w-]+)*)(?P<python> python3?)? )?"
    r"scripts/(?P<name>[\w-]+\.py)\b"
)
FILE_TEST = re.compile(r"\[\s+-[ef]\s+(['\"]?)scripts/[\w-]+\.py\1\s+\]")


def inline_metadata(script: Path) -> dict[str, object] | None:
    """The script's `# /// script` block as TOML, or None without one."""
    blocks = [m for m in METADATA.finditer(script.read_text(encoding="utf-8")) if m.group("type") == "script"]
    if not blocks:
        return None
    content = "".join(
        line[2:] if line.startswith("# ") else line[1:] for line in blocks[0]["content"].splitlines(keepends=True)
    )
    return tomllib.loads(content)


def project_bound(script: Path) -> bool:
    """Whether the script runs in the project environment, because it imports heinzel."""
    return PROJECT_BOUND in script.read_text(encoding="utf-8")


def metadata_problems(script: Path) -> list[str]:
    """What is wrong with how a script declares where it runs."""
    text = script.read_text(encoding="utf-8")
    metadata = inline_metadata(script)
    if project_bound(script):
        problems = [] if metadata is None else ["is project-bound but has a `# /// script` block"]
        if not re.search(r"(?m)^(from|import) heinzel\b", text):
            problems.append("is project-bound but doesn't import heinzel; give it a `# /// script` block instead")
        return problems
    if metadata is None:
        return [f"has neither a `# /// script` block nor a `{PROJECT_BOUND}` comment saying why"]
    problems = []
    if metadata.get("requires-python") != PYPROJECT["project"]["requires-python"]:
        problems.append(f"requires-python must be the project's {PYPROJECT['project']['requires-python']!r}")
    dependencies = metadata.get("dependencies")
    if not isinstance(dependencies, list):
        return [*problems, "must list its `dependencies` (`[]` for none)"]
    for dependency in dependencies:
        name, _, version = str(dependency).partition("==")
        if LOCKED.get(name.strip().lower()) != version.strip():
            problems.append(f"{dependency!r} must be pinned with == to the version in uv.lock")
    return problems


def call_problems(text: str, standalone: set[str], bound: set[str], stdlib: set[str] | None = None) -> list[str]:
    """Calls that run a script the wrong way: standalone ones with `uv run --script`, project-bound ones with
    `uv run python`."""
    problems = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        code = FILE_TEST.sub("", line.split(" #", 1)[0])  # A shell file-existence predicate doesn't execute the file.
        for call in CALL.finditer(code):
            name, flags = call["name"], (call["flags"] or "").split()
            isolated_stdlib = (
                name in (stdlib or set())
                and call["runner"]
                and call["python"]
                and "--no-build" in flags
                and ({"--frozen", "--no-sync"} <= set(flags) or {"--no-project", "--python", "3.14"} <= set(flags))
            )
            if (
                name in standalone
                and not isolated_stdlib
                and (not call["runner"] or call["python"] or "--script" not in flags)
            ):
                problems.append(f"{call.group()}: run it as `uv run --script scripts/{name}`")
            if name in bound and (not call["runner"] or not call["python"] or "--script" in flags):
                problems.append(f"{call.group()}: run it as `uv run python scripts/{name}` (it imports heinzel)")
    return problems


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_declare_their_dependencies_or_why_they_need_the_project(script):
    """`uv run --script` runs a script in its own cached environment, from its PEP 723 block, without syncing the
    project; pinning to uv.lock's versions keeps it on what the project tests and already has in uv's cache."""
    assert metadata_problems(script) == []


def test_finds_scripts_that_declare_where_they_run_wrongly(tmp_path):
    script = tmp_path / "x.py"
    script.write_text('"""X."""\n', encoding="utf-8")
    assert metadata_problems(script) != []
    script.write_text('# /// script\n# requires-python = ">=3.0"\n# dependencies = ["six"]\n# ///\n', encoding="utf-8")
    assert len(metadata_problems(script)) == 2
    script.write_text(f"{PROJECT_BOUND}, because.\nimport json\n", encoding="utf-8")
    assert metadata_problems(script) != []
    script.write_text(f"{PROJECT_BOUND}, because.\nfrom heinzel import Kernel\n", encoding="utf-8")
    assert metadata_problems(script) == []


def test_finds_scripts_run_the_wrong_way():
    standalone, bound = {"a.py"}, {"b.py"}
    assert call_problems("uv run --quiet --script scripts/a.py --json", standalone, bound) == []
    assert call_problems("env -u X uv run python scripts/b.py --check", standalone, bound) == []
    assert call_problems("make check  # see scripts/a.py", standalone, bound) == []
    assert len(call_problems("uv run python scripts/a.py", standalone, bound)) == 1
    assert len(call_problems("python scripts/a.py", standalone, bound)) == 1
    assert len(call_problems("uv run --script scripts/b.py", standalone, bound)) == 1
    assert call_problems("if [ -f scripts/a.py ]; then uv run --script scripts/a.py; fi", standalone, bound) == []
    assert len(call_problems('if [ -f "scripts/a.py" ]; then python scripts/a.py; fi', standalone, bound)) == 1


@pytest.mark.parametrize("caller", CALLERS, ids=lambda p: str(p.relative_to(REPO)))
def test_callers_run_scripts_the_way_they_declare(caller):
    standalone = {script.name for script in SCRIPTS if not project_bound(script)}
    bound = {script.name for script in SCRIPTS if project_bound(script)}
    stdlib = {script.name for script in SCRIPTS if (inline_metadata(script) or {}).get("dependencies") == []}
    assert call_problems(caller.read_text(encoding="utf-8"), standalone, bound, stdlib) == []


@pytest.mark.parametrize(
    "launcher",
    ["uv run --no-project --frozen --no-build --python 3.14 python", "uv run --frozen --no-sync --no-build python"],
)
def test_hardened_python_launchers_only_bypass_script_resolution_for_stdlib(launcher):
    standalone = {"plain.py", "dependency.py"}
    stdlib = {"plain.py"}
    assert call_problems(f"{launcher} scripts/plain.py", standalone, set(), stdlib) == []
    assert len(call_problems(f"{launcher} scripts/dependency.py", standalone, set(), stdlib)) == 1
    assert len(call_problems("uv run python scripts/plain.py", standalone, set(), stdlib)) == 1
