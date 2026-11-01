# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Keep Core-root PR and agent checks within the owning projects for plugin-only changes.

`push` is the pre-push hook: the fast checks scoped to what the branch changes against main. Only Markdown, site
templates, assets and `mkdocs.yml`, and the site's own tooling (`scripts/site_*.py`, `check_docs.py`,
`sync_template.py` and their unit tests) run the documentation checks and the tests that read documents and the site;
only `web/` runs the web checks; changes that touch neither `web/` nor `desktop/` nor the root build files run
`make check-fast` without the web app's tests and the managed backend's check; anything else runs `make check-fast` in
full. CI always runs the full gate, so nothing goes unchecked, only later.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLKIT = (
    "heinzel-plugin-tools @ https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
    "tools-v0.2.1/heinzel_plugin_tools-0.2.1-py3-none-any.whl"
    "#sha256=eebaf555c0c3b39cb13d28ae5ea146ec459e06d1b96cf8ba4499ffea4247aab7"
)


def changed_paths(mode: str, base: str) -> list[str]:
    """Include deleted, moved and untracked paths without parsing Git's quoted display format."""
    revision = [f"{base}...HEAD"] if mode in ("preflight", "push") else ["HEAD"]
    result = subprocess.check_output(  # noqa: S603 - fixed git argument lists, no shell
        ["git", "diff", "--name-only", "--no-renames", "-z", *revision],  # noqa: S607 - git from PATH
        text=True,
        timeout=30,
    )
    if mode == "fast":
        result += subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],  # noqa: S607 - git from PATH
            text=True,
            timeout=30,
        )
    return [path for path in result.split("\0") if path]


MARKDOWN_TESTS = [
    "tests/unit/documentation_test.py",
    "tests/unit/standards_test.py",
    "tests/unit/repository_test.py",
    "tests/unit/pypi_description_test.py",
    "tests/unit/readme_diagnostics_test.py",
]
DOCS_STEPS = [  # the documentation steps of `make check-fast` and the tests that read Markdown
    "template=uv run --script scripts/sync_template.py --check",
    "&docs=env -u HZ_SYSTEM_AGENT__SKILL_DIRS uv run python scripts/generate_docs.py --check",
    "&demo=uv run --script scripts/demo_tape.py --check",
    "&typos=uv run typos",
    f"tests=uv run pytest {' '.join(MARKDOWN_TESTS)} -n 2 -x -q --session-timeout=120",
]
WEB = "uv run --quiet --script scripts/web_build.py"
WEB_STEPS = [  # WEB_LINT, WEB_TYPES and WEB_TESTS of the Makefile
    f"web-lint={WEB} exec biome ci .",
    "&web-client=env -u HZ_SYSTEM_AGENT__SKILL_DIRS uv run --quiet python scripts/web.py client --check",
    f"&web-types={WEB} exec node node_modules/@typescript/native/bin/tsc --noEmit -p .",
    f"&web-tests={WEB} exec vitest run",
]


def is_document(path: str) -> bool:
    """Markdown, the site's page templates, assets and configuration: nothing the product or its tests run."""
    return (
        path.endswith(".md")
        or (path.startswith("docs/overrides/") and path.endswith(".html"))
        or path.startswith("docs/assets/website/")
        or path == "mkdocs.yml"
    )


TOOLING = re.compile(r"scripts/(?:site_[a-z_]+|check_docs|sync_template)\.py")
TOOLING_TESTS = re.compile(r"tests/unit/(?:site_[a-z_]+|check_docs|docs_style|documentation|sync_template)_test\.py")


def is_docs_tooling(path: str) -> bool:
    """The scripts that build and check the documentation site, and their own unit tests."""
    return bool(TOOLING.fullmatch(path) or TOOLING_TESTS.fullmatch(path))


def docs_steps(tooling: list[str]) -> list[str]:
    """The documentation steps; with tooling files also their lint, and the tests of the site and the template.

    The Makefile is deliberately not part of any of this: it can change anything.
    """
    if not tooling:
        return DOCS_STEPS
    files = " ".join(tooling)
    own = sorted(
        {
            *[path for path in tooling if path.startswith("tests/")],
            *(str(path.relative_to(ROOT)) for path in ROOT.glob("tests/unit/site_*_test.py")),
        }
    )
    tests = " ".join([*MARKDOWN_TESTS, "tests/unit/ci_test.py", *own])
    return [
        f"format=uv run ruff format --check {files}",
        f"&ruff=uv run ruff check --no-fix {files}",
        *DOCS_STEPS[:-1],
        f"tests=uv run pytest {tests} -n 2 -x -q --session-timeout=120",
    ]


def scoped_steps(changed: list[str]) -> list[str] | None:
    """Steps for a branch of documents, their tooling and web files only, or None when the full gate must run."""
    web = [path for path in changed if path.startswith("web/") and not is_document(path)]
    tooling = [path for path in changed if is_docs_tooling(path)]
    documents = [path for path in changed if is_document(path)]
    if not changed or len(web) + len(documents) + len(tooling) != len(changed):
        return None
    steps: list[str] = []
    if documents or tooling:
        steps += docs_steps(tooling)
    if web:
        steps += [f"web-prepare={WEB} prepare", *WEB_STEPS]
    return steps


WEB_INPUTS = ("web/", "desktop/")
WEB_ROOT_FILES = {"pyproject.toml", "uv.lock", "Makefile", "mkdocs.yml"}


def touches_web(changed: list[str]) -> bool:
    """Whether a change can break the web app's tests or the managed backend check: files they read."""
    return any(path.startswith(WEB_INPUTS) or path in WEB_ROOT_FILES for path in changed)


def main() -> None:
    """Preserve core checks and common policies while selecting independent owning plugin work."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["fast", "preflight", "push"])
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()
    changed = changed_paths(args.mode, args.base)
    if args.mode == "push":
        changed = sorted({*changed, *changed_paths("fast", args.base)})
        steps = scoped_steps(changed)
        if steps:
            command = ["uv", "run", "--quiet", "--script", "scripts/steps.py", *steps]
            done = subprocess.run(command, check=False, timeout=600)  # noqa: S603
            sys.exit(done.returncode)
        if changed and not touches_web(changed) and not all(path.startswith("plugins/") for path in changed):
            # Python, scripts, tests and documents: everything of the fast gate but the web app's tests and the managed
            # backend's check, which read nothing of it (the generated client is still checked by web-lint).
            command = ["make", "check-fast", "WEB_TESTS=", "MANAGED_CHECK="]
            sys.exit(subprocess.run(command, check=False, timeout=1800).returncode)  # noqa: S603
        args.mode = "fast"  # everything else is the full fast gate
    if not changed or not all(path.startswith("plugins/") for path in changed):
        subprocess.run(  # noqa: S603 - explicit owning commands, no shell
            ["make", "preflight" if args.mode == "preflight" else "check-fast", f"BASE={args.base}"],  # noqa: S607 - make from PATH
            check=True,
            timeout=1800 if args.mode == "preflight" else 300,
        )
        return
    toolkit = ["uv", "run", "--quiet", "--no-project", "--with", TOOLKIT, "hz-plugin"]
    if args.mode == "fast":
        subprocess.run([*toolkit, "check-changed", "--changed", *changed], check=True, timeout=300)  # noqa: S603 - pinned toolkit, literal paths
        return
    # These policies apply to plugin source too; no core build, native job or test suite runs.
    subprocess.run(["make", "commits", "secrets", f"BASE={args.base}"], check=True, timeout=120)  # noqa: S603, S607 - shared policy targets
    subprocess.run(
        ["uv", "run", "--script", "scripts/check_confidentiality.py"],  # noqa: S607 - pinned script runner
        check=True,
        timeout=120,
    )
    plan = json.loads(
        subprocess.check_output([*toolkit, "plan", "--changed", *changed], text=True, timeout=60)  # noqa: S603 - pinned metadata-only planner
    )
    for project in plan["include"]:
        subprocess.run([*toolkit, "preflight", project["name"]], check=True, timeout=1800)  # noqa: S603 - canonical names from the pinned planner


if __name__ == "__main__":
    # Git runs hooks at the repository root; explicit cwd also supports direct invocation elsewhere.
    os.chdir(Path(__file__).resolve().parents[1])
    main()
