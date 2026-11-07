#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Affected-tests selector: changed files -> the minimal pytest paths, Vitest files, E2E lanes and CI jobs.

  affected.py [--repo DIR] FILE...            paths relative to the repo root (as `git diff --name-only` prints)
  affected.py [--repo DIR] --base origin/main  the files the checkout differs from BASE in (three-dot diff + tree)
  affected.py --stdin                          one path per line
  affected.py --json                           machine output (the CI step's input); default is a text summary

Conservative on purpose (selection never weakens a test, unsure means run everything): workflows, Makefile, pyproject,
the lockfile, scripts, the kernel, installers, the updater, the daemon, credentials, release code, shared test fixtures
and any path the map does not know select the FULL set. Everything else narrows:
  docs and Markdown only      -> site tests (make test-site), no app jobs
  web/src, web/e2e            -> the Vitest files that import the change (the repo's import graph), the browser E2E
                                 lanes, lint (biome, tsc, vitest); no Python matrix, no coverage, no CLI lane
  modules/*, heads/* (python) -> unit and integration test files that import the change (scripts/related_tests.py's
                                 graph) plus the tests named after it, the Linux test cell, coverage and the E2E lanes
                                 the head needs; macOS smoke only for native paths
  tests/*                     -> the changed test files themselves (and their lane)
  desktop/                    -> Electron; plugins/ -> the source plugin row
The repo is the checkout this script lives in unless --repo names another; the map reads that checkout's own Makefile
lanes and tests, so it follows the repo rather than a copy of its rules. Advisory in CI: the ci.yml `changes` job
publishes the selection in shadow mode and no job is skipped by it. Exit 0; 2 when the repo is unusable.
"""

from __future__ import annotations

import argparse
import functools
import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_REPO = Path(__file__).resolve().parent.parent

# Every CI job the ordinary pull request of a core change can start (ci.yml). "full" is all of them.
JOBS_FULL = [
    "lint",
    "setup",
    "audit",
    "test-linux",
    "coverage",
    "perf",
    "e2e-cli",
    "e2e-supervised",
    "e2e-web-1",
    "e2e-web-2",
    "macos-smoke",
    "macos-e2e",
    "electron",
    "template",
    "core-wheel",
    "consumers",
    "build",
]
JOBS_ALWAYS = ["commits", "dependency-review", "secrets"]  # cheap, every pull request

# Paths whose change selects the full set (owner rule: workflows, scripts, tools, kernel, security, installers, updater,
# release, lockfiles; plus the shared test harness, build metadata and the template the plugins are generated from).
FULL = re.compile(
    r"^(?:\.github/|Makefile$|pyproject\.toml$|uv\.lock$|hatch_build\.py$|copier\.yml$|template/|starter/"
    r"|scripts/(?!site_[a-z_]+\.py$|check_docs\.py$)"
    r"|src/heinzel/__init__\.py$|src/heinzel/__main__\.py$|src/heinzel/_lifecycle_control\.py$|src/_heinzel_launcher\.py$"
    r"|src/heinzel/kernel/|src/heinzel/testing\.py$"
    r"|src/heinzel/heads/(?:install|bootstrap|daemon_recovery|_desktop_[a-z]+|_tool_install|_prerequisites|_presence|_exposure)\.py$"
    r"|src/heinzel/heads/daemon/|src/heinzel/heads/web/_(?:auto_update|supervision|lifecycle|service)\.py$"
    r"|src/heinzel/modules/system/(?:daemon|lifecycle|credential|plugin|prerequisite|runner|layer|doctor)/"
    r"|src/heinzel/modules/system/notify/macos/"
    r"|tests/conftest\.py$|tests/_[a-z_]+\.py$|tests/fixtures/|tests/unit/conftest\.py$|tests/integration/conftest\.py$"
    r"|tests/e2e/conftest\.py$|tests/e2e/_[a-z_]+\.py$"
    r"|web/package\.json$|web/pnpm-lock\.yaml$|web/vite\.config\.ts$|web/tsconfig\.json$|web/biome\.json$"
    r"|web/src/test/|web/src/main\.tsx$|web/src/App\.tsx$|web/index\.html$"
    r"|fuzz/|agent-skills/|design/tokens|\.pre-commit|\.gitleaks|SECURITY\.md$)"
)
DOCS = re.compile(
    r"^(?:docs/(?!.*\.sh$).*|[^/]*\.md|mkdocs\.yml|scripts/(?:site_[a-z_]+|check_docs)\.py|design/README\.md)$"
)
SITE_TEST_FILES = re.compile(
    r"^tests/unit/(?:site_[a-z_]+|docs_style|documentation|standards|repository|pypi_description|readme_diagnostics)_test\.py$"
)
DESKTOP = re.compile(r"^desktop/")
PLUGINS = re.compile(r"^plugins/")
WEB_SRC = re.compile(r"^web/src/.*\.(?:ts|tsx|css)$")
WEB_E2E = re.compile(r"^web/(?:e2e/.*\.(?:mjs|html|tsx)|public/.*)$")
WEB_OPENAPI = re.compile(r"^web/openapi\.json$")
PY_SRC = re.compile(r"^src/heinzel/(?:modules|heads)/.*\.py$")
PY_TEST = re.compile(r"^tests/(unit|integration|e2e)/.*_test\.py$")
PY_SUPPORT = re.compile(r"^tests/(unit|integration|e2e)/.*\.py$")  # a helper next to the tests: run its directory
NATIVE_PY = re.compile(r"src/heinzel/(?:heads/(?:cli|web)/|modules/(?:ui|system/(?:notify|sound|theme)/))")
CLI_LANE_PY = re.compile(r"src/heinzel/heads/(?:cli/|mcp/|api/)")

# Sites tests, as `make test-site` lists them; read from the Makefile when present.
SITE_TESTS_DEFAULT = [
    "tests/unit/documentation_test.py",
    "tests/unit/standards_test.py",
    "tests/unit/repository_test.py",
    "tests/unit/pypi_description_test.py",
    "tests/unit/readme_diagnostics_test.py",
    "tests/unit/docs_style_test.py",
    "tests/unit/site_catalog_test.py",
    "tests/unit/site_domain_test.py",
    "tests/integration/guides_test.py",
]
# Tests that read the repository's Markdown: a docs change runs them, a code change does not (they are in test-site).
DOCS_READERS = SITE_TESTS_DEFAULT

# Shared web files: when one of these changes, more than half of the Vitest files import it; run them all (it is
# one job of a minute or two) rather than list 80 files.
# Module specifiers in a web source file: `... from "x"` (import and re-export), `import("x")` and `import "x"`. Three
# simple patterns rather than one alternation: each is linear, and a stray match only widens the selection.
WEB_IMPORTS = (
    re.compile(r"\bfrom\s*[\"']([^\"'\n]+)[\"']"),
    re.compile(r"\bimport\s*\(\s*[\"']([^\"'\n]+)[\"']"),
    re.compile(r"\bimport\s*[\"']([^\"'\n]+)[\"']"),
)
# An E2E script name, matched only where a name starts, so a long run of name characters is not rescanned.
MJS_NAME = re.compile(r"(?<![a-z0-9-])[a-z0-9-]+\.mjs")
E2E_DIR = "tests/e2e"
REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@^~{}-]*")
SHA = re.compile(r"[0-9a-f]{40}")


class UnusableRepoError(Exception):
    """The directory is not a core checkout the map can be read from."""


def make_variable(text: str, name: str) -> str | None:
    """The value of `NAME := value` in a Makefile, or None. Line-based, no regular expression."""
    for line in text.splitlines():
        key, sep, value = line.partition(":=")
        if sep and key.strip() == name and not line[:1].isspace():
            return value.strip()
    return None


WEB_SHARED = re.compile(r"^web/src/(?:i18n\.ts|index\.css|api/|lib/|components/ui/|splash/|lifecycle/)")


class Repo:
    """The core checkout the map is read from: its Makefile lanes, tests and import graphs."""

    def __init__(self, root: Path) -> None:
        """Read the checkout at ROOT.

        Raises:
            UnusableRepoError: when ROOT is not a core checkout.
        """
        self.root = root
        if not (root / "Makefile").is_file() or not (root / "src/heinzel").is_dir():
            msg = f"affected: {root} is not a core checkout (no Makefile or src/heinzel); pass --repo"
            raise UnusableRepoError(msg)

    # -- Python ---------------------------------------------------------------------------------------------------
    @functools.cached_property
    def related(self) -> Any:  # noqa: ANN401 - the loaded scripts/related_tests module
        """The core's own import graph (scripts/related_tests.py), loaded from the checkout."""
        sys.path.insert(0, str(self.root / "scripts"))
        module: Any = importlib.import_module("related_tests")
        module.ROOT = self.root  # the graph resolves files against ROOT
        return module

    def python_tests_for(self, changed: list[str]) -> list[str]:
        """Unit and integration test files that import a changed source file, or are named after it."""
        graph = self.related.graph()
        existing = {path for path in changed if path in graph.trees}
        reached = graph.dependents(existing)
        tests = {p for p in reached if re.fullmatch(r"tests/(?:unit|integration)/.*_test\.py", p)}
        for path in changed:
            stem = Path(path).stem
            tests |= {
                t
                for t in graph.files
                if re.fullmatch(r"tests/(?:unit|integration)/.*_test\.py", t) and t.endswith(f"/{stem}_test.py")
            }
        return sorted(t for t in tests if (self.root / t).is_file())

    # -- E2E lanes ------------------------------------------------------------------------------------------------
    @functools.cached_property
    def lanes(self) -> dict[str, list[str]]:
        """E2E lane -> its pytest files, from the Makefile's E2E_* variables (web-1 = web_ui_test, web-2 = the rest)."""
        text = (self.root / "Makefile").read_text(encoding="utf-8")
        supervised = make_variable(text, "E2E_SUPERVISED")
        supervised_files = supervised.split() if supervised else []
        all_e2e = sorted(str(p.relative_to(self.root)) for p in (self.root / E2E_DIR).glob("*_test.py"))
        candidates = make_variable(text, "E2E_WEB_CANDIDATES")
        if candidates and "$(" not in candidates:
            web_candidates = candidates.split()
        else:
            # Browser tests are the ones whose module drives a web/e2e script.
            web_candidates = [
                f for f in all_e2e if MJS_NAME.search((self.root / f).read_text(encoding="utf-8", errors="replace"))
            ]
        web = [f for f in web_candidates if f not in supervised_files]
        web_1 = [f for f in web if f == f"{E2E_DIR}/web_ui_test.py"]
        web_2 = [f for f in web if f not in web_1]
        cli = [f for f in all_e2e if f not in web and f not in supervised_files]
        return {"cli": cli, "supervised": supervised_files, "web-1": web_1, "web-2": web_2}

    @functools.cached_property
    def script_owners(self) -> dict[str, list[str]]:
        """web/e2e/<name>.mjs -> the tests/e2e files that run it."""
        owners: dict[str, list[str]] = {}
        for f in (self.root / E2E_DIR).glob("*.py"):
            text = f.read_text(encoding="utf-8", errors="replace")
            for name in set(MJS_NAME.findall(text)):
                owners.setdefault(f"web/e2e/{name}", []).append(str(f.relative_to(self.root)))
        return owners

    def lane_of(self, test_file: str) -> str | None:
        """The E2E lane that runs TEST_FILE, if any."""
        return next((lane for lane, files in self.lanes.items() if test_file in files), None)

    # -- Vitest ---------------------------------------------------------------------------------------------------
    @functools.cached_property
    def web_graph(self) -> tuple[list[str], dict[str, set[str]]]:
        """web/src files and who imports whom (`@/` alias and relative imports; `.d.ts` resolved like modules)."""
        files = [
            str(p.relative_to(self.root))
            for p in (self.root / "web/src").rglob("*")
            if p.suffix in {".ts", ".tsx"} and "node_modules" not in p.parts
        ]
        reverse: dict[str, set[str]] = {}
        for f in files:
            text = (self.root / f).read_text(encoding="utf-8", errors="replace")
            for pattern in WEB_IMPORTS:
                for spec in pattern.findall(text):
                    target = self.resolve_web_import(f, spec)
                    if target:
                        reverse.setdefault(target, set()).add(f)
        return files, reverse

    def resolve_web_import(self, src: str, spec: str) -> str | None:
        """The web/src file an import specifier in SRC names, or None for packages and unresolved paths."""
        if spec.startswith("@/"):
            base = self.root / "web/src" / spec[2:]
        elif spec.startswith("."):
            base = (self.root / src).parent / spec
        else:
            return None
        base = Path(os.path.normpath(base))
        candidates = (
            base,
            base.with_suffix(".ts"),
            base.with_suffix(".tsx"),
            Path(str(base) + ".d.ts"),
            base / "index.ts",
            base / "index.tsx",
        )
        found = next((c for c in candidates if c.is_file()), None)
        return str(found.relative_to(self.root)) if found else None

    def vitest_for(self, changed: list[str]) -> list[str] | None:
        """Vitest files importing a changed web file; None means all of them (a shared file changed)."""
        if any(WEB_SHARED.match(p) for p in changed):
            return None
        files, reverse = self.web_graph
        reached, queue = set(changed), list(changed)
        while queue:
            for importer in reverse.get(queue.pop(), ()):
                if importer not in reached:
                    reached.add(importer)
                    queue.append(importer)
        tests = {f for f in reached if ".test." in f}
        for p in changed:  # a sibling test named after the file, even without an import (e.g. CSS modules)
            stem = Path(p).name.split(".")[0]
            tests |= {f for f in files if ".test." in f and Path(f).name.split(".")[0] == stem}
        return sorted(t for t in tests if (self.root / t).is_file())


CLIP = 12  # paths shown per list in the text summary
ALL_LANES = ["cli", "supervised", "web-1", "web-2"]


class Selection:
    """What the changed paths select so far; the helpers below add to it and `classify` freezes it."""

    def __init__(self) -> None:
        """Start with the always-on jobs and nothing else."""
        self.jobs: set[str] = set(JOBS_ALWAYS)
        self.pytest: set[str] = set()
        self.lanes: set[str] = set()
        self.vitest: set[str] | None = set()  # None means every Vitest file
        self.reasons: list[str] = []


def _full_result(changed: list[str], reasons: list[str]) -> dict[str, Any]:
    return {
        "files": changed,
        "full": True,
        "pytest": ["ALL"],
        "vitest": ["ALL"],
        "e2e_lanes": list(ALL_LANES),
        "jobs": [*JOBS_ALWAYS, *JOBS_FULL],
        "reasons": reasons,
    }


def _select_docs(sel: Selection, changed: list[str]) -> None:
    docs = [p for p in changed if DOCS.match(p) or SITE_TEST_FILES.match(p)]
    if docs:
        sel.pytest.update(DOCS_READERS)
        sel.pytest.update(p for p in docs if SITE_TEST_FILES.match(p))
        sel.jobs.add("site-tests")
        sel.reasons.append(f"{len(docs)} document/site file(s): site tests")


def _select_python_source(repo: Repo, sel: Selection, py_src: list[str]) -> None:
    related = repo.python_tests_for(py_src)
    sel.pytest.update(related)
    sel.reasons.append(
        f"{len(py_src)} python source file(s): {len(related)} unit/integration test file(s) import or match them"
    )
    # The API serves the browser: any module or head change can change what the web E2E lanes see.
    sel.lanes.update({"web-1", "web-2"})
    if any(CLI_LANE_PY.search(p) for p in py_src):
        sel.lanes.add("cli")
    if any(re.search(r"heads/(?:web|cli)/|modules/ui/", p) for p in py_src):
        sel.lanes.add("supervised")
    if any(NATIVE_PY.search(p) for p in py_src):
        sel.jobs.add("macos-smoke")
    if any(re.search(r"src/heinzel/modules/system/ui/|src/heinzel/heads/", p) for p in py_src):
        sel.jobs.add("consumers")  # the contracts rule of ci.yml


def _select_python_tests(repo: Repo, sel: Selection, py_tests: list[str], py_support: list[str]) -> None:
    for test in py_tests:
        if not test.startswith(f"{E2E_DIR}/"):
            sel.pytest.add(test)
        elif lane := repo.lane_of(test):
            sel.lanes.add(lane)
        else:
            sel.lanes.update(ALL_LANES)
            sel.reasons.append(f"{test}: not in a known E2E lane, all lanes")
    for helper in py_support:
        directory = str(Path(helper).parent)
        if directory.startswith(E2E_DIR):
            sel.lanes.update(ALL_LANES)
        else:
            sel.pytest.add(directory + "/")
        sel.reasons.append(f"{helper}: test helper, its directory runs")


def _select_python(repo: Repo, sel: Selection, changed: list[str]) -> None:
    py_src = [p for p in changed if PY_SRC.match(p)]
    py_tests = [p for p in changed if PY_TEST.match(p) and not SITE_TEST_FILES.match(p)]
    py_support = [p for p in changed if PY_SUPPORT.match(p) and not PY_TEST.match(p)]
    if not (py_src or py_tests or py_support):
        return
    sel.jobs.update({"lint", "test-linux", "coverage", "perf", "setup", "audit", "core-wheel", "build"})
    if py_src:
        _select_python_source(repo, sel, py_src)
    _select_python_tests(repo, sel, py_tests, py_support)


def _select_web_source(repo: Repo, sel: Selection, web_src: list[str]) -> None:
    selected = repo.vitest_for(web_src)
    if selected is None:
        sel.vitest = None
        sel.reasons.append("a shared web file changed (i18n, api, lib, ui, splash, lifecycle): every Vitest file")
    else:
        sel.vitest = (sel.vitest or set()) | set(selected)
        sel.reasons.append(f"{len(web_src)} web source file(s): {len(selected)} Vitest file(s) import them")
    sel.lanes.update({"web-1", "web-2"})
    if any(re.search(r"web/src/(?:updates|lifecycle|pwa)/", p) for p in web_src):
        sel.jobs.add("macos-smoke")  # the auto-update and lifecycle paths are native-sensitive
    if any(re.search(r"web/src/plugins/", p) for p in web_src):
        sel.jobs.add("consumers")


def _select_web_e2e(repo: Repo, sel: Selection, web_e2e: list[str]) -> None:
    for path in web_e2e:
        owners = repo.script_owners.get(path, [])
        if owners:
            sel.lanes.update(repo.lane_of(owner) or "web-2" for owner in owners)
        elif path.endswith(".test.mjs"):
            sel.vitest = None if sel.vitest is None else sel.vitest | {path}
        else:
            sel.lanes.update({"supervised", "web-1", "web-2"})
            sel.reasons.append(f"{path}: no tests/e2e owner found, all browser lanes")


def _select_web(repo: Repo, sel: Selection, changed: list[str]) -> None:
    web_src = [p for p in changed if WEB_SRC.match(p)]
    web_e2e = [p for p in changed if WEB_E2E.match(p)]
    openapi = [p for p in changed if WEB_OPENAPI.match(p)]
    if not (web_src or web_e2e or openapi):
        return
    sel.jobs.update({"lint", "build"})  # biome, tsc and vitest run inside the lint job
    if web_src:
        _select_web_source(repo, sel, web_src)
    _select_web_e2e(repo, sel, web_e2e)
    if openapi:
        sel.jobs.update({"test-linux", "coverage"})
        sel.pytest.update(
            repo.python_tests_for(["src/heinzel/heads/api/__init__.py"])
            or ["tests/integration/api_input_schema_test.py"]
        )
        sel.lanes.update({"web-1", "web-2"})
        sel.reasons.append("web/openapi.json: the API's tests and both browser lanes")


def _select_other(sel: Selection, changed: list[str]) -> None:
    if any(DESKTOP.match(p) for p in changed):
        sel.jobs.add("electron")
        sel.reasons.append("desktop/: Electron")
    if any(PLUGINS.match(p) for p in changed):
        sel.jobs.update({"core-wheel", "local-plugins"})
        sel.reasons.append("plugins/: the source plugin row")


def _known(path: str) -> bool:
    patterns = (DOCS, DESKTOP, PLUGINS, WEB_SRC, WEB_E2E, WEB_OPENAPI, PY_SRC, PY_SUPPORT, SITE_TEST_FILES)
    return any(pattern.match(path) for pattern in patterns)


def classify(repo: Repo, changed: list[str]) -> dict[str, Any]:
    """The selection for a list of changed paths: any full-set or unknown path selects everything."""
    changed = sorted({p.strip() for p in changed if p.strip()})
    if not changed:
        return {
            "files": changed,
            "full": False,
            "pytest": [],
            "vitest": [],
            "e2e_lanes": [],
            "jobs": list(JOBS_ALWAYS),
            "reasons": ["no changed files: nothing to run"],
        }
    reasons = [f"{p}: shared/sensitive path, selects the full set" for p in changed if FULL.match(p)]
    reasons += [f"{p}: not in the map, selects the full set" for p in changed if not FULL.match(p) and not _known(p)]
    if reasons:
        return _full_result(changed, reasons)

    sel = Selection()
    _select_docs(sel, changed)
    _select_python(repo, sel, changed)
    _select_web(repo, sel, changed)
    _select_other(sel, changed)
    sel.jobs.update(f"e2e-{lane}" for lane in sel.lanes)
    return {
        "files": changed,
        "full": False,
        "pytest": sorted(sel.pytest),
        "vitest": ["ALL"] if sel.vitest is None else sorted(sel.vitest),
        "e2e_lanes": sorted(sel.lanes),
        "jobs": sorted(sel.jobs),
        "reasons": sel.reasons,
    }


def changed_from_git(repo: Path, base: str) -> list[str]:
    """Files changed against BASE (three-dot diff) plus the working tree's own changes.

    BASE is resolved to a commit id first, so whatever was passed reaches `git diff` only as 40 hex digits, never as
    an option.

    Raises:
        SystemExit: when BASE is not a revision of the checkout, or git cannot diff against it.
    """
    if not REVISION.fullmatch(base):
        msg = f"affected: not a git revision: {base!r}"
        raise SystemExit(msg)
    verify = ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", "--end-of-options", f"{base}^{{commit}}"]
    resolved = subprocess.run(verify, capture_output=True, text=True, check=False, timeout=60)  # noqa: S603 - fixed executable
    commit = resolved.stdout.strip()
    if resolved.returncode != 0 or not SHA.fullmatch(commit):
        msg = f"affected: {base!r} is not a commit of {repo}"
        raise SystemExit(msg)
    diff = ["git", "-C", str(repo), "diff", "--name-only", f"{commit}...HEAD"]
    out = subprocess.run(diff, capture_output=True, text=True, check=False, timeout=120)  # noqa: S603 - fixed executable
    if out.returncode != 0:
        msg = f"affected: git diff failed: {out.stderr.strip()}"
        raise SystemExit(msg)
    status = ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"]
    work = subprocess.run(status, capture_output=True, text=True, check=False, timeout=120)  # noqa: S603 - fixed executable
    tree = [line[3:].split(" -> ")[-1] for line in work.stdout.splitlines() if line.strip()]
    return sorted(set(out.stdout.split()) | set(tree))


def _clip(label: str, items: list[str]) -> str:
    shown = " ".join(items[:CLIP]) + (f" ... +{len(items) - CLIP}" if len(items) > CLIP else "")
    return f"{label} ({len(items)}): {shown}"


def render(result: dict[str, Any]) -> str:
    """The text summary of a selection."""
    lines = [
        f"full: {'yes' if result['full'] else 'no'}",
        "jobs: " + (" ".join(result["jobs"]) or "-"),
        "e2e lanes: " + (" ".join(result["e2e_lanes"]) or "-"),
        _clip("pytest", result["pytest"]),
        _clip("vitest", result["vitest"]),
    ]
    lines.extend("  - " + reason for reason in result["reasons"])
    return "\n".join(lines)


def main() -> int:
    """Print the selection for the given files, stdin or a git base."""
    parser = argparse.ArgumentParser(description="Changed files -> the minimal tests and CI jobs (advisory).")
    parser.add_argument("files", nargs="*")
    parser.add_argument(
        "--repo", type=Path, default=DEFAULT_REPO, help="checkout to read (default: the one this script is in)"
    )
    parser.add_argument("--base", default=None, help="git revision to diff the checkout against (three-dot)")
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        repo = Repo(args.repo.resolve())
    except UnusableRepoError as error:
        print(error, file=sys.stderr)
        return 2
    files = list(args.files)
    if args.stdin:
        files += sys.stdin.read().splitlines()
    if args.base:
        files += changed_from_git(repo.root, args.base)
    result = classify(repo, files)
    print(json.dumps(result, indent=1) if args.json else render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
