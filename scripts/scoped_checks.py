# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Keep Core-root PR and agent checks within the owning projects for plugin-only changes.

`push` is the pre-push hook: the fast checks scoped to what the branch changes against main. Only Markdown, site
templates, assets and `mkdocs.yml`, and the site's own tooling (`scripts/site_*.py`, `check_docs.py`,
`sync_template.py` and their unit tests) run the documentation checks and the tests that read documents and the site;
only `web/` runs the web checks; changes that touch neither `web/` nor `desktop/` nor the root build files run
`make check-fast` without the web app's tests and the managed backend's check, with the type checks added and the unit
tests narrowed to the ones related to the change (`scripts/related_tests.py`; configuration, the lockfile and
`conftest.py` cannot be mapped and run all of them); anything else runs `make check-fast` in full. `make check-fast` by
hand stays the full run. Set HZ_FOCUSED_PUSH=1 for a Python push from a host where the desktop tests cannot run.
This requires a nonempty related unit selection and leaves the desktop tests to CI. CI always runs the full gate.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLKIT = (
    "heinzel-plugin-tools @ https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
    "tools-v0.2.1/heinzel_plugin_tools-0.2.1-py3-none-any.whl"
    "#sha256=eebaf555c0c3b39cb13d28ae5ea146ec459e06d1b96cf8ba4499ffea4247aab7"
)


def named_revision(base: str) -> str:
    """Rebuild a revision from parts that cannot carry an option: Git's own ref names, hex digits or `HEAD` offsets.

    Raises:
        ValueError: If the input is none of these.
    """
    if offset := re.fullmatch(r"HEAD(?:([~^])([0-9]{1,4}))?", base):
        return f"HEAD{offset[1]}{int(offset[2])}" if offset[1] else "HEAD"
    if re.fullmatch(r"[0-9a-f]{7,64}", base):
        return f"{int(base, 16):0{len(base)}x}"
    # Fixed Git listing of the branch, remote and tag names
    names = subprocess.check_output(
        # Repository Git from PATH
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes", "refs/tags"],  # noqa: S607
        text=True,
        timeout=30,
    ).splitlines()
    for name in names:
        if name == base:
            return name
    msg = "The base must name a branch, tag, commit or HEAD offset"
    raise ValueError(msg)


def resolve_base(base: str) -> str:
    """Resolve a revision once so Git and Make receive only a canonical commit identity.

    Raises:
        ValueError: If the input is not a revision or Git returned an invalid identity.
    """
    # Fixed Git operation, revision explicitly separated from options
    revision = subprocess.check_output(  # noqa: S603
        # Repository Git from PATH
        ["git", "rev-parse", "--verify", "--end-of-options", f"{named_revision(base)}^{{commit}}"],  # noqa: S607
        text=True,
        timeout=30,
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", revision):
        msg = "Git returned an invalid commit identity"
        raise ValueError(msg)
    return revision


def changed_paths(mode: str, base: str) -> list[str]:
    """Include deleted, moved and untracked paths without parsing Git's quoted display format."""
    revision = [f"{base}...HEAD"] if mode in ("preflight", "push") else ["HEAD"]
    result = subprocess.check_output(  # noqa: S603 - fixed git argument lists, no shell
        # Git from PATH
        ["git", "diff", "--name-only", "--no-renames", "-z", "--end-of-options", *revision, "--"],  # noqa: S607
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


TYPES = '"&mypy=uv run mypy" "&pyright=uv run pyright"'
HOSTED_DESKTOP_TESTS = {
    "tests/unit/heads/desktop_installation_test.py",
    "tests/integration/desktop_framework_wrap_test.py",
}


def python_overrides(changed: list[str], *, focused: bool = False) -> list[str]:
    """Make variables for the pre-push run of `check-fast`: no web steps, the type checks, the related unit tests.

    A change that cannot be mapped to tests (configuration, the lockfile, `conftest.py`) keeps the full unit run. When
    nothing is related (a script no test mentions), only the hook's own test runs besides lint, contracts and types.

    Raises:
        SystemExit: If focused mode cannot select any related tests that can run locally.
    """
    overrides = ["WEB_TESTS=", "MANAGED_CHECK=", f"FAST_TYPES={TYPES}"]
    sys.path.insert(0, str(ROOT / "scripts"))
    from related_tests import related_tests  # noqa: PLC0415 - only the hook needs the import graph

    related = related_tests(changed)
    if focused:
        related = [path for path in related or [] if path not in HOSTED_DESKTOP_TESTS]
        if not related:
            msg = "HZ_FOCUSED_PUSH requires at least one related unit test that can run locally."
            raise SystemExit(msg)
    if related is None:
        return overrides
    if not related:
        related = ["tests/unit/scoped_checks_test.py"]  # pytest fails on an empty selection; this pins the hook itself
    return [*overrides, f"UNIT_TARGETS={' '.join(related)}"]


def early_guard_commands(changed: list[str]) -> list[list[str]]:
    """Existing owning assertions, before preparation; none replace a full-gate step.

    The partition guard collects E2E inventory but selects only integration bodies. Guides parse the CLI tree
    without invoking documented commands. These checks never start product services or access accounts.
    """
    if changed and all(path.startswith("plugins/") for path in changed):
        return []
    fixture = any(
        path.startswith(("tests/", "template/")) or path.endswith((".py", ".sh", ".jinja")) for path in changed
    )
    guides = any(
        (path.startswith("docs/guides/") and path.endswith(".md"))
        or path == "tests/integration/guides_test.py"
        or (
            path.startswith(("src/heinzel/heads/cli/", "src/heinzel/modules/", "src/heinzel/kernel/"))
            and path.endswith(".py")
        )
        for path in changed
    )
    ci = any(
        path
        in {
            "Makefile",
            "pyproject.toml",
            "uv.lock",
            "tests/_e2e_lanes.py",
            "tests/_e2e_schedule.py",
            "tests/conftest.py",
            "scripts/scoped_checks.py",
        }
        or path.startswith((".github/workflows/", ".github/actions/", "tests/e2e/"))
        or path
        in {
            "tests/unit/browser_runtime_setup_test.py",
            "tests/unit/ci_test.py",
            "tests/unit/ci_supervised_test.py",
            "tests/unit/web_build_test.py",
            "tests/integration/e2e_lane_partition_test.py",
        }
        for path in changed
    )
    targets = []
    if fixture:
        targets.append("tests/unit/standards_test.py::test_tests_and_template_use_only_variables_something_reads")
    if guides:
        targets.append("tests/integration/guides_test.py")
    if ci:
        targets += ["tests/unit/browser_runtime_setup_test.py", "tests/unit/ci_supervised_test.py"]
        targets += ["tests/unit/ci_test.py", "tests/unit/web_build_test.py"]
    commands = [["uv", "run", "pytest", *targets, "-n", "2", "-x", "-q", "--session-timeout=120"]] if targets else []
    if ci:
        commands.append(
            [
                "uv",
                "run",
                "pytest",
                "tests/integration/e2e_lane_partition_test.py",
                "tests/e2e",
                "-m",
                "integration",
                "-n",
                "2",
                "-x",
                "-q",
                "--session-timeout=120",
            ]
        )
    return commands


MODES = ("fast", "preflight", "push", "early-fast", "early-preflight")
GATE_MAX_WAIT = 3600  # waiting for a host slot does not count against the check's runtime limit


def run_check(
    command: list[str], *, timeout: int, light: bool = False, check: bool = True, capture_output: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run the selected check through the optional host queue, with its own runtime limit."""
    gate = shutil.which("hz-gate")
    if gate is None or not Path(gate).is_absolute():
        return subprocess.run(  # noqa: S603 - fixed check commands and validated operands
            command, check=check, timeout=timeout, capture_output=capture_output, text=True
        )
    env = os.environ.copy()
    try:
        requested_wait = int(os.environ.get("HZ_GATE_MAX_WAIT", ""))
    except ValueError:
        requested_wait = GATE_MAX_WAIT
    queue_wait = min(requested_wait, GATE_MAX_WAIT) if requested_wait > 0 else GATE_MAX_WAIT
    env["HZ_GATE_MAX_WAIT"] = str(queue_wait)
    if light:
        env["HZ_GATE_LIGHT"] = "1"
    else:
        env.pop("HZ_GATE_LIGHT", None)
    # The host enforces separate queue and runtime limits. The outer watchdog allows both
    # the optional pre-push lock wait and the slot wait, plus time for launcher startup.
    return subprocess.run(  # noqa: S603 - absolute gate, fixed check commands and validated operands
        [gate, "--timeout", str(timeout), *command],
        env=env,
        check=check,
        capture_output=capture_output,
        text=True,
        timeout=2 * queue_wait + timeout + 10,
    )


def run_early_guards(changed: list[str], *, light: bool = False) -> None:
    """Fail before the caller can launch an expensive full-check command."""
    for command in early_guard_commands(changed):
        run_check(command, timeout=180, light=light)


def push_checks(changed: list[str]) -> int | None:
    """Run scoped push checks, or return None for the ordinary fast gate.

    Raises:
        SystemExit: If a focused push cannot use the related Python unit checks.
    """
    steps = scoped_steps(changed)
    python_scope = (
        bool(changed) and not touches_web(changed) and not all(path.startswith("plugins/") for path in changed)
    )
    focused = os.environ.get("HZ_FOCUSED_PUSH") == "1"
    if focused and (steps or not python_scope):
        msg = "HZ_FOCUSED_PUSH requires a Python push with a related unit selection."
        raise SystemExit(msg)
    light = bool(changed) and all(is_document(path) for path in changed)
    run_early_guards(changed, light=light)
    if steps:
        command = ["uv", "run", "--quiet", "--script", "scripts/steps.py", *steps]
        return run_check(command, check=False, timeout=600, light=light).returncode
    if python_scope:
        # The generated client is still checked by web-lint.
        command = ["make", "check-fast", *python_overrides(changed, focused=focused)]
        return run_check(command, check=False, timeout=1800).returncode
    return None


def main() -> None:
    """Preserve core checks and common policies while selecting independent owning plugin work."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()
    early = args.mode.startswith("early-")
    if early:
        args.mode = args.mode.removeprefix("early-")
    try:
        base = resolve_base(args.base) if early or args.mode in ("preflight", "push") else "HEAD"
    except (ValueError, subprocess.CalledProcessError):
        parser.error("Use a valid commit revision for --base")
    changed = changed_paths("preflight" if early else args.mode, base)
    if early:
        changed = sorted({*changed, *changed_paths("fast", base)})
        run_early_guards(changed)
        return
    base_arguments = [f"BASE={base}"] if args.mode == "preflight" else []
    if args.mode == "push":
        changed = sorted({*changed, *changed_paths("fast", base)})
        if (status := push_checks(changed)) is not None:
            sys.exit(status)
        args.mode = "fast"  # everything else is the full fast gate
    if not changed or not all(path.startswith("plugins/") for path in changed):
        run_check(
            ["make", "preflight" if args.mode == "preflight" else "check-fast", *base_arguments],
            timeout=1800 if args.mode == "preflight" else 300,
        )
        return
    toolkit = [sys.executable, str(Path(__file__).with_name("plugin_tooling.py")), "--toolkit", TOOLKIT, "hz-plugin"]
    if args.mode == "fast":
        run_check([*toolkit, "check-changed", "--changed", *changed], timeout=300)
        return
    # These policies apply to plugin source too; no core build, native job or test suite runs.
    # Shared policy targets
    run_check(["make", "commits", "secrets", *base_arguments], timeout=120)
    run_check([sys.executable, "scripts/check_confidentiality.py"], timeout=120)
    plan = run_check([*toolkit, "plan", "--changed", *changed], timeout=60, capture_output=True)
    for project in json.loads(plan.stdout)["include"]:
        run_check([*toolkit, "preflight", project["name"]], timeout=1800)


if __name__ == "__main__":
    # Git runs hooks at the repository root; explicit cwd also supports direct invocation elsewhere.
    os.chdir(Path(__file__).resolve().parents[1])
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
