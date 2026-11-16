# /// script
# requires-python = ">=3.15"
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
hand stays the full run. CI runs reviewed focused workloads and reserves the full suite for release candidates.

For a scoped web push, set WEB_TEST_FILES to a space-separated list of existing repo-relative Vitest files,
for example `WEB_TEST_FILES=web/src/sound/StreamPlayer.test.tsx git push`. Leave it unset to run all web tests.

For an independently reviewed Python push scope, HZ_LOCAL_TEST_SCOPE names a bounded private JSON packet and
HZ_LOCAL_TEST_SCOPE_SHA256 pins its reviewed bytes. The packet has schema=1, exact base/head/tree, owning selectors,
and hosted_only selectors. The latter must be precisely the prohibited desktop file selections, retained for hosted
proof. The caller obtains independent scope review before supplying the digest; the digest binds bytes, not reviewer
identity. --show-python-push-plan validates that same packet and prints the unchanged guards and resulting Make argv.
No packet means the existing import-graph selection. This input never authorizes a hosted or coverage acceptance.

For mixed Python/web/Apple changes, HZ_LOCAL_MIXED_SCOPE and HZ_LOCAL_MIXED_SCOPE_SHA256 bind a schema=2 packet.
It retains schema=1 identity and Python fields, adding web, integration and pending lists. The closed inventory in
mixed_scope.py requires the owning local recipes and every outstanding native/hosted obligation. --show-mixed-push-plan
prints the same commands the hook executes. Success means local checks only, never completion of pending obligations.
Every execution fence rereads the named base and complete diff. Execution requires the normal pre-push wrapper's
preserved single HEAD branch update; a standalone preview without that input cannot authorize a push. The fixed
check-fast recipe requires fresh JUnit and Vitest reports with passing bodies in every selected local test file.
Mixed scopes accept whole test files only. Unit files may contain platform skips; these are explicitly reported as
unverified cases and never count as passing bodies. Schema-1 Python node selection is unchanged.
The original partition guard collects full E2E inventory with -m integration; it does not execute E2E bodies.
Mixed local plans instead run the bounded selector/partition assertions without directory collection. Complete
real-inventory partition proof and full E2E/integration execution remain explicit hosted obligations. Nested Make
guards require the same reviewed scope, pushed refs and guard-plan binding; nonmixed guards are unchanged.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLKIT = (
    "heinzel-plugin-tools @ https://github.com/helmut-hoffer-von-ankershoffen/heinzel-plugins/releases/download/"
    "tools-v0.4.1/heinzel_plugin_tools-0.4.1-py3-none-any.whl"
    "#sha256=56072dabdd2a7c94f5210b10dedb85099d6ad67f19a58e37949a05d4b830ad38"
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
WEB_TEST_SUFFIXES = {"src": (".test.ts", ".test.tsx"), "e2e": (".test.mjs",)}


def web_test_step(files: str | None) -> str:
    """Build the Vitest step from explicit checkout files, with no shell or Vitest options from the caller.

    Raises:
        ValueError: If the opt-in is empty or any entry is outside the web test inventory.
    """
    if files is None:
        return WEB_STEPS[-1]
    targets = []
    for file in files.split():
        root, _, relative = file.partition("/")
        area, _, test = relative.partition("/")
        if (
            root != "web"
            or not test.endswith(WEB_TEST_SUFFIXES.get(area, ()))
            or any(not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part) for part in test.split("/"))
        ):
            msg = "WEB_TEST_FILES must list repo-relative web/src/*.test.ts(x) or web/e2e/*.test.mjs files"
            raise ValueError(msg)
        path = ROOT / file
        if not path.is_file() or path.resolve() != path:
            msg = f"WEB_TEST_FILES must name an existing web test file: {file}"
            raise ValueError(msg)
        targets.append(f"./{relative}")
    if not targets:
        msg = "WEB_TEST_FILES is empty. Unset it for the default web test run."
        raise ValueError(msg)
    return f"{WEB_STEPS[-1]} --maxWorkers=2 {' '.join(targets)}"


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


def scoped_steps(changed: list[str], *, web_test_files: str | None = None) -> list[str] | None:
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
        steps += [f"web-prepare={WEB} prepare", *WEB_STEPS[:-1], web_test_step(web_test_files)]
    return steps


WEB_INPUTS = ("web/", "desktop/")
WEB_ROOT_FILES = {"pyproject.toml", "uv.lock", "Makefile", "mkdocs.yml"}


def touches_web(changed: list[str]) -> bool:
    """Whether a change can break the web app's tests or the managed backend check: files they read."""
    return any(path.startswith(WEB_INPUTS) or path in WEB_ROOT_FILES for path in changed)


TYPES = '"&mypy=uv run mypy" "&pyright=uv run pyright"'


def python_overrides(changed: list[str], selected: list[str] | None = None) -> list[str]:
    """Make variables for the pre-push run of `check-fast`: no web steps, the type checks, the related unit tests.

    A change that cannot be mapped to tests (configuration, the lockfile, `conftest.py`) keeps the full unit run. When
    nothing is related (a script no test mentions), only the hook's own test runs besides lint, contracts and types.
    """
    overrides = ["WEB_TESTS=", "MANAGED_CHECK=", f"FAST_TYPES={TYPES}"]
    sys.path.insert(0, str(ROOT / "scripts"))
    from related_tests import related_tests  # noqa: PLC0415 - only the hook needs the import graph

    related = selected if selected is not None else related_tests(changed)
    if related is None:
        return overrides
    if not related:
        related = ["tests/unit/scoped_checks_test.py"]  # pytest fails on an empty selection; this pins the hook itself
    return [*overrides, f"UNIT_TARGETS={' '.join(related)}"]


LOCAL_SCOPE_MAX = 32 * 1024
MIXED_SCOPE_SCHEMA = 2
PUSH_REF_FIELDS = 4
LOCAL_SCOPE_SELECTORS = 64
LOCAL_SELECTOR = re.compile(r"tests/unit/[A-Za-z0-9_/-]+_test\.py(?:::[A-Za-z0-9_]+(?:\[[A-Za-z0-9_.,=+-]+\])?)*")
HOSTED_ONLY_TESTS = frozenset({"tests/unit/heads/desktop_installation_test.py"})


def _scope_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            msg = "duplicate local scope field"
            raise ValueError(msg)
        result[key] = value
    return result


def local_python_scope(changed: list[str], base: str) -> tuple[list[str], list[str]] | None:
    """Bind a reviewed local selector packet; hosted-only tests remain explicit obligations.

    HZ_LOCAL_TEST_SCOPE names a private JSON packet; HZ_LOCAL_TEST_SCOPE_SHA256 pins the independently reviewed bytes.
    This is local push configuration only, never hosted admission, coverage or acceptance evidence.

    Raises:
        ValueError: The packet is stale, ambiguous, unbounded or outside the owning Python unit-test selection.
    """
    name = os.environ.get("HZ_LOCAL_TEST_SCOPE")
    digest = os.environ.get("HZ_LOCAL_TEST_SCOPE_SHA256")
    if name is None and digest is None:
        return None
    if not name or not digest or not re.fullmatch(r"[0-9a-f]{64}", digest):
        msg = "local scope requires both a packet path and its reviewed SHA256"
        raise ValueError(msg)
    if not changed or touches_web(changed) or all(path.startswith("plugins/") for path in changed):
        msg = "local scope is only supported for the owning Python push lane"
        raise ValueError(msg)
    packet = _local_scope_packet(name, digest, base)
    return _local_scope_selection(packet, changed)


def _local_scope_packet(name: str, digest: str, base: str, *, schema: int = 1) -> dict:
    """Read bounded reviewed bytes and require the exact unchanged checkout.

    Raises:
        ValueError: The packet is ambiguous, stale or does not match the reviewed digest.
    """
    with Path(name).open("rb") as stream:
        data = stream.read(LOCAL_SCOPE_MAX + 1)
    if not 0 < len(data) <= LOCAL_SCOPE_MAX or hashlib.sha256(data).hexdigest() != digest:
        msg = "local scope size or reviewed digest mismatch"
        raise ValueError(msg)
    packet = json.loads(data, object_pairs_hook=_scope_object)
    fields = {"schema", "base", "head", "tree", "owning", "hosted_only"}
    if schema == MIXED_SCOPE_SCHEMA:
        fields.update({"web", "integration", "pending"})
    if not isinstance(packet, dict) or set(packet) != fields:
        msg = "invalid local scope fields"
        raise ValueError(msg)
    if (
        type(packet["schema"]) is not int
        or packet["schema"] != schema
        or packet["base"] != base
        or packet["head"] != resolve_base("HEAD")
        or packet["tree"] != resolve_tree()
    ):
        msg = "local scope differs from exact base/head/tree"
        raise ValueError(msg)
    dirty = subprocess.check_output(["git", "diff", "HEAD", "--name-only"], cwd=ROOT, text=True, timeout=30)  # noqa: S607
    if dirty.strip():
        msg = "local scope requires unchanged tracked source"
        raise ValueError(msg)
    return packet


def mixed_push_plan(changed: list[str], base: str) -> dict | None:
    """Validate an explicit mixed scope before any guard, preparation or check runs.

    Raises:
        ValueError: The packet is ambiguous, stale, dirty or lacks an owning recipe.
    """
    name, digest = os.environ.get("HZ_LOCAL_MIXED_SCOPE"), os.environ.get("HZ_LOCAL_MIXED_SCOPE_SHA256")
    if name is None and digest is None:
        return None
    if any(key in os.environ for key in ("HZ_LOCAL_TEST_SCOPE", "HZ_LOCAL_TEST_SCOPE_SHA256")):
        msg = "mixed scope cannot be combined with a schema-1 scope"
        raise ValueError(msg)
    if not name or not digest or not re.fullmatch(r"[0-9a-f]{64}", digest):
        msg = "mixed scope requires both a packet path and its reviewed SHA256"
        raise ValueError(msg)
    packet = _local_scope_packet(name, digest, base, schema=MIXED_SCOPE_SCHEMA)
    if changed_paths("fast", base):
        msg = "mixed scope requires no uncommitted or untracked source"
        raise ValueError(msg)
    sys.path.insert(0, str(ROOT / "scripts"))
    from mixed_scope import APPLE_SOURCES, NATIVE_FIXTURES, validate  # noqa: PLC0415 - opt-in local policy

    python = validate(packet, changed, ROOT)
    selected, hosted = _local_scope_selection(packet, python)
    recipe = [sys.executable, "scripts/mixed_scope.py"]
    checks = [[*recipe, "check-fast", "--unit", *selected, "--web", *packet["web"]]]
    if packet["integration"]:
        checks.append([*recipe, "integration", *sorted(packet["integration"])])
    if set(changed) & (APPLE_SOURCES | NATIVE_FIXTURES):
        checks.append([*recipe, "ios"])
    return {
        "identity": {key: packet[key] for key in ("base", "head", "tree")},
        "scope_sha256": digest,
        "early_guards": mixed_early_guard_commands(changed),
        "checks": checks,
        "pending": sorted([*packet["pending"], *hosted]),
        "acceptance": "local checks only; pending obligations require separate exact-head proof",
    }


def mixed_early_guard_commands(changed: list[str]) -> list[list[str]]:
    """Keep bounded local partition assertions; full inventory collection stays hosted.

    Raises:
        ValueError: The existing inventory recipe changed beyond the reviewed replacement.
    """
    from mixed_scope import LOCAL_PARTITION_TESTS, PARTITION_TEST  # noqa: PLC0415

    result = []
    for command in early_guard_commands(changed):
        if "tests/e2e" not in command:
            result.append(command)
            continue
        if command[:5] != ["uv", "run", "pytest", PARTITION_TEST, "tests/e2e"] or command[5:7] != ["-m", "integration"]:
            msg = "mixed partition guard no longer matches the reviewed bounded replacement"
            raise ValueError(msg)
        result.append([*command[:3], *LOCAL_PARTITION_TESTS, *command[5:]])
    if any(
        (argument == "tests" or argument.startswith("tests/")) and not argument.partition("::")[0].endswith(".py")
        for command in result
        for argument in command
    ):
        msg = "mixed early guards cannot invoke test directories"
        raise ValueError(msg)
    return result


def push_binding(*, required: bool) -> dict | None:
    """Bind the complete stdin preserved by the normal pre-push wrapper to this HEAD.

    Raises:
        ValueError: The actual push is unavailable, multi-ref, or not the checked HEAD branch update.
    """
    name = os.environ.get("HZ_DEV_PUSH_REFS_FILE")
    if name is None and not required:
        return None
    if not name or os.environ.get("PRE_COMMIT") != "1" or os.environ.get("PREK_HOOK_STAGE") != "pre-push":
        msg = "mixed execution requires actual pre-push input preserved by the normal hook"
        raise ValueError(msg)
    with Path(name).open("rb") as stream:
        data = stream.read(LOCAL_SCOPE_MAX + 1)
    lines = data.decode("ascii").splitlines()
    if len(data) > LOCAL_SCOPE_MAX or len(lines) != 1 or len(lines[0].split()) != PUSH_REF_FIELDS:
        msg = "mixed execution requires exactly one complete pushed ref"
        raise ValueError(msg)
    local_ref, local_oid, remote_ref, remote_oid = lines[0].split()
    branch = subprocess.check_output(
        ["git", "symbolic-ref", "--quiet", "HEAD"],  # noqa: S607 - repository Git
        cwd=ROOT,
        text=True,
        timeout=30,
    ).strip()
    head = resolve_base("HEAD")
    if (
        local_ref not in {"HEAD", branch}
        or local_oid != head
        or not remote_ref.startswith("refs/heads/")
        or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", remote_oid)
    ):
        msg = "mixed execution only permits pushing the checked HEAD branch"
        raise ValueError(msg)
    return {
        "local_ref": local_ref,
        "local_oid": local_oid,
        "remote_ref": remote_ref,
        "remote_oid": remote_oid,
        "stdin_sha256": hashlib.sha256(data).hexdigest(),
    }


def current_mixed_plan(base_ref: str, *, require_push: bool) -> dict | None:
    """Read current refs and the complete diff again, never reuse a previously captured path list."""
    base = resolve_base(base_ref)
    changed = sorted({*changed_paths("push", base), *changed_paths("fast", base)})
    plan = mixed_push_plan(changed, base)
    if plan is not None:
        plan.update({"base_ref": base_ref, "changed": changed, "push": push_binding(required=require_push)})
    return plan


def configured_mixed_plan(parser: argparse.ArgumentParser, base_ref: str, *, show: bool) -> bool:
    """Execute precisely the previewed local plan, keeping remaining proof visibly pending."""
    if not show and all(key not in os.environ for key in ("HZ_LOCAL_MIXED_SCOPE", "HZ_LOCAL_MIXED_SCOPE_SHA256")):
        return False
    try:
        plan = current_mixed_plan(base_ref, require_push=not show)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    if show and plan is None:
        parser.error("--show-mixed-push-plan requires an explicit reviewed mixed scope")
    if plan is None:
        return False
    print(json.dumps(plan, sort_keys=True), flush=True)
    if show:
        return True
    with mixed_guard_context(plan):
        for command in plan["early_guards"]:
            unchanged_mixed_plan(parser, plan)
            run_check(command, timeout=180)
        for command in plan["checks"]:
            unchanged_mixed_plan(parser, plan)
            run_check(command, timeout=1800)
    # A hook can take minutes: do not report proof for a checkout or packet changed during its checks.
    unchanged_mixed_plan(parser, plan)
    print("Local mixed checks passed. PENDING: " + ", ".join(plan["pending"]), flush=True)
    return True


def mixed_guard_binding(plan: dict) -> str:
    """Bind the policy identity and exact guard plan across Python tool environments."""
    keys = ("identity", "scope_sha256", "base_ref", "changed", "push", "early_guards", "pending")
    data = json.dumps({key: plan[key] for key in keys}, sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()


def mixed_guard_environment(plan: dict) -> dict[str, str]:
    """Pass the validated parent context directly, never through Make or shell interpolation."""
    return {
        "HZ_LOCAL_MIXED_BASE_REF": plan["base_ref"],
        "HZ_LOCAL_MIXED_GUARD_BINDING": mixed_guard_binding(plan),
    }


@contextmanager
def mixed_guard_context(plan: dict) -> Iterator[None]:
    """Inherit only the bound context for these synchronous checks, restoring it afterwards."""
    values = mixed_guard_environment(plan)
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def inherited_mixed_plan() -> dict:
    """Require the exact parent identity and guard plan for nested Make checks.

    Raises:
        ValueError: The bound parent context is missing or differs from current source.
    """
    base_ref = os.environ.get("HZ_LOCAL_MIXED_BASE_REF")
    expected = os.environ.get("HZ_LOCAL_MIXED_GUARD_BINDING")
    if not base_ref or not expected:
        msg = "mixed early guards require the reviewed parent context"
        raise ValueError(msg)
    plan = current_mixed_plan(base_ref, require_push=True)
    if plan is None or mixed_guard_binding(plan) != expected:
        msg = "mixed early guards differ from the reviewed parent context"
        raise ValueError(msg)
    return plan


def configured_mixed_early_guards(parser: argparse.ArgumentParser) -> bool:
    """Fence nested Make guards to the same exact scope and bounded parent guard plan."""
    if all(key not in os.environ for key in ("HZ_LOCAL_MIXED_SCOPE", "HZ_LOCAL_MIXED_SCOPE_SHA256")):
        return False
    try:
        plan = inherited_mixed_plan()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    with mixed_guard_context(plan):
        for command in plan["early_guards"]:
            unchanged_mixed_plan(parser, plan)
            run_check(command, timeout=180)
    unchanged_mixed_plan(parser, plan)
    return True


def unchanged_mixed_plan(parser: argparse.ArgumentParser, plan: dict) -> None:
    """Fence every dispatch and the final result to the initially reviewed source and bytes."""
    try:
        current = current_mixed_plan(plan["base_ref"], require_push=True)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    if current != plan:
        parser.error("mixed scope changed during local checks")


def _local_scope_selection(packet: dict, changed: list[str]) -> tuple[list[str], list[str]]:
    """Keep permitted owning checks local and list every hosted-only obligation.

    Raises:
        ValueError: A selector or hosted obligation is absent, unsafe or outside the owning scope.
    """
    owning, hosted = packet["owning"], packet["hosted_only"]
    if not isinstance(owning, list) or not 0 < len(owning) <= LOCAL_SCOPE_SELECTORS or not isinstance(hosted, list):
        msg = "local scope requires bounded owning selectors and hosted obligations"
        raise ValueError(msg)
    if any(not isinstance(item, str) or not LOCAL_SELECTOR.fullmatch(item) for item in owning + hosted):
        msg = "local scope contains an invalid selector"
        raise ValueError(msg)
    if len(set(owning)) != len(owning) or len(set(hosted)) != len(hosted):
        msg = "local scope repeats a selector"
        raise ValueError(msg)
    expected_hosted = [item for item in owning if item.split("::", 1)[0] in HOSTED_ONLY_TESTS]
    if hosted != expected_hosted:
        msg = "local scope must retain exactly every prohibited file as a hosted-only obligation"
        raise ValueError(msg)
    selected = [item for item in owning if item not in hosted]
    if not selected:
        msg = "local scope has no permitted local owning checks"
        raise ValueError(msg)
    sys.path.insert(0, str(ROOT / "scripts"))
    from related_tests import related_tests  # noqa: PLC0415 - the push lane owns this import graph

    related = related_tests(changed)
    paths = {item.split("::", 1)[0] for item in owning}
    changed_tests = {path for path in changed if path.startswith("tests/unit/") and path.endswith("_test.py")}
    if related is None or not paths <= set(related) or not changed_tests <= paths:
        msg = "local scope omits a changed test or includes a test outside the owning import graph"
        raise ValueError(msg)
    for path in paths:
        candidate = ROOT / path
        if not candidate.is_file() or candidate.is_symlink() or not candidate.resolve().is_relative_to(ROOT.resolve()):
            msg = "local scope test path is absent or escapes the checkout"
            raise ValueError(msg)
    return selected, hosted


def resolve_tree() -> str:
    """Read the exact committed tree for local scope binding."""
    return subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True, timeout=30).strip()  # noqa: S607


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
            "scripts/mixed_scope.py",
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
    command: list[str],
    *,
    timeout: int,
    light: bool = False,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the selected check through the optional host queue, with its own runtime limit."""
    gate = shutil.which("hz-gate")
    if gate is None or not Path(gate).is_absolute():
        return subprocess.run(  # noqa: S603 - fixed check commands and validated operands
            command,
            check=check,
            timeout=timeout,
            capture_output=capture_output,
            text=True,
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


def run_scoped_push(changed: list[str], parser: argparse.ArgumentParser) -> None:
    """Run the pre-push guards and the docs or web steps when they cover the branch."""
    light = bool(changed) and all(is_document(path) for path in changed)
    run_early_guards(changed, light=light)
    try:
        steps = scoped_steps(changed, web_test_files=os.environ.get("WEB_TEST_FILES"))
    except ValueError as error:
        parser.error(str(error))
    if steps:
        command = ["uv", "run", "--quiet", "--script", "scripts/steps.py", *steps]
        sys.exit(run_check(command, check=False, timeout=600, light=light).returncode)


def configured_python_selection(
    parser: argparse.ArgumentParser, changed: list[str], base: str, *, show: bool
) -> list[str] | None:
    """Use the same validated command for the read-only preview and normal push."""
    try:
        local_scope = local_python_scope(changed, base)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    if show:
        if local_scope is None:
            parser.error("--show-python-push-plan requires an explicit reviewed local scope")
        print(
            json.dumps(
                {
                    "early_guards": early_guard_commands(changed),
                    "check": ["make", "check-fast", *python_overrides(changed, local_scope[0])],
                    "hosted_only": local_scope[1],
                },
                sort_keys=True,
            )
        )
        sys.exit(0)
    return local_scope[0] if local_scope else None


def arguments() -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    """Parse mutually exclusive previews before any scope can execute checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument(
        "--show-python-push-plan",
        action="store_true",
        help="validate explicit local scope and print the normal commands without running them",
    )
    parser.add_argument("--show-mixed-push-plan", action="store_true", help="preview a reviewed mixed local plan")
    args = parser.parse_args()
    if args.show_python_push_plan and args.mode != "push":
        parser.error("--show-python-push-plan requires push")
    if args.show_mixed_push_plan and (args.mode != "push" or args.show_python_push_plan):
        parser.error("--show-mixed-push-plan requires push and cannot be combined with the Python preview")
    if args.show_python_push_plan and any(
        key in os.environ for key in ("HZ_LOCAL_MIXED_SCOPE", "HZ_LOCAL_MIXED_SCOPE_SHA256")
    ):
        parser.error("use --show-mixed-push-plan for a mixed scope")
    return parser, args


def check_inputs(parser: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[str, list[str], bool]:
    """Resolve the ordinary command's base and changes without altering default selection."""
    early = args.mode.startswith("early-")
    if early:
        args.mode = args.mode.removeprefix("early-")
    try:
        base = resolve_base(args.base) if early or args.mode in ("preflight", "push") else "HEAD"
    except (ValueError, subprocess.CalledProcessError):
        parser.error("Use a valid commit revision for --base")
    changed = changed_paths("preflight" if early else args.mode, base)
    return base, changed, early


def main() -> None:
    """Preserve core checks and common policies while selecting independent owning plugin work."""
    parser, args = arguments()
    if args.mode.startswith("early-") and configured_mixed_early_guards(parser):
        return
    base, changed, early = check_inputs(parser, args)
    if early:
        changed = sorted({*changed, *changed_paths("fast", base)})
        run_early_guards(changed)
        return
    base_arguments = [f"BASE={base}"] if args.mode == "preflight" else []
    if args.mode == "push":
        changed = sorted({*changed, *changed_paths("fast", base)})
        if configured_mixed_plan(parser, args.base, show=args.show_mixed_push_plan):
            return
        selected = configured_python_selection(parser, changed, base, show=args.show_python_push_plan)
        run_scoped_push(changed, parser)
        if changed and not touches_web(changed) and not all(path.startswith("plugins/") for path in changed):
            # Python, scripts, tests and documents: everything of the fast gate but the web app's tests and the managed
            # backend's check, which read nothing of it (the generated client is still checked by web-lint).
            command = ["make", "check-fast", *python_overrides(changed, selected)]
            sys.exit(run_check(command, check=False, timeout=1800).returncode)
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
