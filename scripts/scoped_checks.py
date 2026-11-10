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
This requires a nonempty related unit selection and leaves the hosted desktop tests to CI. For explicit Python,
web-only, or mixed Python/web selections, set HZ_FOCUSED_PUSH_MANIFEST to a reviewed JSON plan for the exact base
and head. This local selection does not alter the required hosted checks.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

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
MIXED_SOURCE_DIRS = (
    "src/heinzel/",
    "tests/unit/",
    "tests/integration/",
    "tests/fixtures/",
    "web/src/",
    "desktop/src/",
    "desktop/test/",
)


def touches_web(changed: list[str]) -> bool:
    """Whether a change can break the web app's tests or the managed backend check: files they read."""
    return any(path.startswith(WEB_INPUTS) or path in WEB_ROOT_FILES for path in changed)


TYPES = '"&mypy=uv run mypy" "&pyright=uv run pyright"'
HOSTED_DESKTOP_TESTS = {
    "tests/unit/heads/desktop_installation_test.py",
    "tests/integration/desktop_framework_wrap_test.py",
}
FOCUSED_TEST_PATHS = {
    "unit": re.compile(r"tests/unit/(?:[\w-]+/)*[\w-]+_test\.py(?:::[\w]+)?", re.ASCII),
    "integration": re.compile(r"tests/integration/(?:[\w-]+/)*[\w-]+_test\.py(?:::[\w]+)?", re.ASCII),
    "web": re.compile(r"web/src/(?:[\w-]+/)*[\w.-]+\.test\.tsx?", re.ASCII),
    "desktop": re.compile(r"desktop/test/(?:[\w-]+/)*[\w-]+\.test\.js", re.ASCII),
}
PYTHON_SOURCE = re.compile(r"src/heinzel/(?:[\w-]+/)*[\w-]+\.py", re.ASCII)
SELECTOR_TEST = "tests/unit/scoped_checks_test.py"
SELECTOR_FILES = {"scripts/scoped_checks.py", SELECTOR_TEST}
DAEMON_BROWSER = "tests/e2e/daemon_settings_restart_test.py"
DAEMON_DRIVER = "web/e2e/daemon-settings-restart.mjs"
DAEMON_INTEGRATION = "tests/integration/daemon_restart_stream_test.py"
DAEMON_WEB_TESTS = {"web/src/components/OperationDialog.test.tsx", "web/src/layout/DaemonSettings.test.tsx"}
DAEMON_CHANGE = {
    "web/src/api/stream.ts",
    "web/src/components/OperationDialog.tsx",
    "web/src/i18n.ts",
    "web/src/layout/DaemonSettings.tsx",
    "web/src/lib/operationRun.ts",
    *DAEMON_WEB_TESTS,
    DAEMON_BROWSER,
    DAEMON_DRIVER,
    DAEMON_INTEGRATION,
}
PUSH_REF_FIELDS = 4  # local ref, local SHA, remote ref, remote SHA


def focused_push_head() -> str:
    """Bind the manifest to the clean checkout and the one ref being pushed.

    Raises:
        SystemExit: If the checkout or push does not identify one exact committed tree.
    """
    head = resolve_base("HEAD")
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],  # noqa: S607 - repository Git from PATH
        text=True,
        timeout=30,
    )
    if dirty:
        msg = "A focused manifest push requires a clean checkout."
        raise SystemExit(msg)
    refs_file = os.environ.get("HZ_DEV_PUSH_REFS_FILE")
    if not refs_file:
        msg = "A focused manifest push requires the pre-push hook's ref list."
        raise SystemExit(msg)
    try:
        refs = Path(refs_file).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError) as error:
        msg = "Unable to read the pushed ref list for a focused manifest push."
        raise SystemExit(msg) from error
    if len(refs) != 1 or len(fields := refs[0].split()) != PUSH_REF_FIELDS or fields[1] != head:
        msg = "A focused manifest push requires exactly one pushed ref at the checked-out HEAD."
        raise SystemExit(msg)
    return head


def tracked_test_files(head: str, files: set[str]) -> set[str]:
    """Find the requested regular, tracked files, excluding symlinks and directories."""
    listed = subprocess.check_output(  # noqa: S603 - validated revision and literal test paths after --
        ["git", "ls-tree", "-rz", "--full-tree", head, "--", *sorted(files)],  # noqa: S607 - repository Git from PATH
        text=True,
        timeout=30,
    )
    return {entry.split("\t", 1)[1] for entry in listed.split("\0") if entry.startswith(("100644 ", "100755 "))}


def read_focused_manifest(manifest: Path, *, base: str, head: str) -> dict[str, object]:
    """Read a reviewed manifest for the exact commit pair.

    Raises:
        SystemExit: If the manifest is malformed or stale.
    """
    try:
        plan = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        msg = "Unable to read HZ_FOCUSED_PUSH_MANIFEST as JSON."
        raise SystemExit(msg) from error
    if not isinstance(plan, dict) or plan.keys() != {"base", "head", *FOCUSED_TEST_PATHS}:
        msg = "A focused manifest requires base, head, unit, integration, web and desktop fields."
        raise SystemExit(msg)
    if plan["base"] != base or plan["head"] != head:
        msg = "A focused manifest must match the exact base and head commits."
        raise SystemExit(msg)
    return cast("dict[str, object]", plan)


def require_daemon_tests(targets: dict[str, list[str]], selector_units: list[str], scope: str) -> set[str]:
    """Require the complete tests for the mapped daemon browser scenario.

    Raises:
        SystemExit: If an owning test is missing from the manifest.
    """
    if scope != "daemon-browser":
        return set()
    required = {
        "unit": {"tests/unit/modules/daemon_service_test.py", *selector_units},
        "integration": {DAEMON_INTEGRATION},
        "web": DAEMON_WEB_TESTS,
    }
    if any(not wanted.issubset(targets[lane]) for lane, wanted in required.items()):
        msg = "Daemon browser scope requires its full owning tests."
        raise SystemExit(msg)
    return {DAEMON_BROWSER, DAEMON_DRIVER}


def focused_test_targets(
    manifest: Path, *, base: str, head: str, changed: list[str], scope: str
) -> dict[str, list[str]]:
    """Load reviewed base/head SHAs and four test lists: unit, integration, web, desktop.

    Paths are repository-relative and tracked at head. Python entries can name a test within a file.
    Python pushes require unit tests and mixed pushes require web tests and Node tests for Electron shell changes.
    Web-only pushes require web tests and run the selector's unit file if the branch changes it.
    Review the selection alongside the diff.

    Raises:
        SystemExit: If the manifest is stale, incomplete or contains an unsupported test selection.
    """
    plan = read_focused_manifest(manifest, base=base, head=head)
    targets: dict[str, list[str]] = {}
    for lane, pattern in FOCUSED_TEST_PATHS.items():
        entries = plan[lane]
        if not isinstance(entries, list) or any(
            not isinstance(path, str) or not pattern.fullmatch(path) for path in cast("list[object]", entries)
        ):
            msg = f"Invalid focused {scope} test path in the {lane} list."
            raise SystemExit(msg)
        targets[lane] = sorted(set(cast("list[str]", entries)))
    selector_units = [SELECTOR_TEST] if SELECTOR_FILES.intersection(changed) else []
    daemon_files = require_daemon_tests(targets, selector_units, scope)
    if scope == "web" and (targets["integration"] or targets["desktop"] or targets["unit"] != selector_units):
        msg = "A focused web manifest allows only web tests and the full selector unit file if changed."
        raise SystemExit(msg)
    if scope == "Python" and (targets["web"] or targets["desktop"]):
        msg = "A focused Python manifest requires empty web and desktop lists."
        raise SystemExit(msg)
    for lane, required in (
        ("unit", scope != "web"),
        ("integration", any(path.startswith("tests/integration/") for path in changed)),
        ("web", scope != "Python"),
        ("desktop", any(path.startswith(("desktop/src/", "desktop/test/")) for path in changed)),
    ):
        if required and not targets[lane]:
            msg = f"A focused manifest requires a nonempty {lane} list for this change."
            raise SystemExit(msg)
    files = {entry.split("::", 1)[0] for entries in targets.values() for entry in entries}
    if files & HOSTED_DESKTOP_TESTS:
        msg = "A focused manifest cannot select hosted desktop tests."
        raise SystemExit(msg)
    if any(any(pattern.fullmatch(path) for pattern in FOCUSED_TEST_PATHS.values()) for path in set(changed) - files):
        msg = "A focused manifest must cover every changed test file."
        raise SystemExit(msg)
    files.update(daemon_files)
    if files != tracked_test_files(head, files):
        msg = "A focused manifest can select only tracked test files at HEAD."
        raise SystemExit(msg)
    return targets


def python_manifest_paths(changed: list[str]) -> bool:
    """Allow Python source, its tests, and the selector itself for staged adoption."""
    patterns = (PYTHON_SOURCE, FOCUSED_TEST_PATHS["unit"], FOCUSED_TEST_PATHS["integration"])
    return all(
        path == "scripts/scoped_checks.py" or any(pattern.fullmatch(path) for pattern in patterns) for path in changed
    )


def daemon_browser_checks() -> int:
    """Build the current web assets and run the mapped isolated browser test."""
    result = run_check([*WEB.split(), "ensure"], check=False, timeout=180).returncode
    if result:
        return result
    browser = ["uv", "run", "pytest", DAEMON_BROWSER, "-m", "e2e", "-n", "2", "-x", "-q"]
    return run_check(browser, check=False, timeout=180).returncode


def manifest_push_checks(changed: list[str], base: str, manifest: Path) -> int:  # noqa: C901, PLR0912
    """Keep the fast gate's static checks and run the reviewed tests for a supported push.

    Raises:
        SystemExit: If the change is outside the supported manifest push scope.
    """
    has_web = any(path.startswith("web/src/") for path in changed)
    has_python = any(PYTHON_SOURCE.fullmatch(path) for path in changed)
    daemon_browser = DAEMON_CHANGE.issubset(changed) and set(changed).issubset(DAEMON_CHANGE | SELECTOR_FILES)
    mixed = has_web and (has_python or daemon_browser)
    web_only = has_web and not (has_python or daemon_browser)
    if daemon_browser:
        allowed = True
    elif mixed:
        allowed = all(
            is_document(path)
            or path in {"scripts/scoped_checks.py", "web/openapi.json"}
            or path.startswith(MIXED_SOURCE_DIRS)
            for path in changed
        )
    elif web_only:
        allowed = all(
            path.startswith("web/src/") or path in SELECTOR_FILES or path == "web/openapi.json" for path in changed
        )
    else:
        allowed = python_manifest_paths(changed)
    if not allowed or WEB_ROOT_FILES.intersection(changed) or not (has_python or web_only or daemon_browser):
        msg = (
            "Focused manifests cover Python, web, or Python/web source changes without build or runtime configuration."
        )
        raise SystemExit(msg)
    scope = "daemon-browser" if daemon_browser else "mixed" if mixed else "web" if web_only else "Python"
    targets = focused_test_targets(manifest, base=base, head=focused_push_head(), changed=changed, scope=scope)
    # The pinned Vitest filters on paths relative to its web/ root.
    web = " ".join(path.removeprefix("web/") for path in targets["web"])
    web_step = f"web-tests={WEB} exec vitest run --maxWorkers=2 {web}"
    run_early_guards(changed)
    if web_only and not targets["unit"]:
        steps = [f"web-prepare={WEB} prepare", *WEB_STEPS[:-1], f"&{web_step}"]
        return run_check(
            ["uv", "run", "--quiet", "--script", "scripts/steps.py", *steps], check=False, timeout=600
        ).returncode
    command = [
        "make",
        "check-fast",
        f"FAST_TYPES={TYPES}",
        f"UNIT_TARGETS={' '.join(targets['unit'])}",
        "PYTEST_XDIST_AUTO_NUM_WORKERS=2",
    ]
    if mixed or web_only:
        command += [
            f'WEB_TESTS="{web_step}"',
            (
                'MANAGED_CHECK="managed-runtime=$(MANAGED) exec node tools/runtime-code.mjs --check" '
                '"&managed-lint=$(MANAGED) exec biome ci ." $(MANAGED_TYPES)'
            ),
        ]
    else:
        command += ["WEB_TESTS=", "MANAGED_CHECK="]
    result = run_check(command, check=False, timeout=600).returncode
    if result:
        return result
    if targets["integration"]:
        integration = ["uv", "run", "pytest", *targets["integration"], "-m", "integration and not perf"]
        result = run_check(
            [*integration, "-n", "2", "-x", "-q", "--session-timeout=120"], check=False, timeout=180
        ).returncode
        if result:
            return result
    if daemon_browser:
        result = daemon_browser_checks()
        if result:
            return result
    if targets["desktop"]:
        desktop = [*WEB.split(), "exec", "node", "--test", *(f"../{path}" for path in targets["desktop"])]
        return run_check(desktop, check=False, timeout=180).returncode
    return 0


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
    targets: list[str] = []
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


def push_checks(changed: list[str], base: str) -> int | None:
    """Run scoped push checks, or return None for the ordinary fast gate.

    Raises:
        SystemExit: If a focused push cannot use the related Python unit checks.
    """
    steps = scoped_steps(changed)
    python_scope = (
        bool(changed) and not touches_web(changed) and not all(path.startswith("plugins/") for path in changed)
    )
    focused = os.environ.get("HZ_FOCUSED_PUSH") == "1"
    if focused and (manifest := os.environ.get("HZ_FOCUSED_PUSH_MANIFEST")) is not None:
        return manifest_push_checks(changed, base, Path(manifest))
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
        if (status := push_checks(changed, base)) is not None:
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
