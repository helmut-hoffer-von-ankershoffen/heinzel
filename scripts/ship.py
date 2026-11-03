# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""The golden path for small changes: one command per stage, one JSON line of output, the next step spelled out.

A low-capability model (Haiku) can take a copy, docs or small UI change from intent to a validated pull request by
running these stages in order. Whether a change is small is decided here, by path and diff rules, never by the model.
`docs/SHIP_SMALL.md` explains the rules and the extension points; `.claude/skills/ship-small` is the short version.

    start SLUG        new worktree and branch from origin/main
    classify          lane of the current change: low (go on) or escalate (stop); --files PATH... asks before editing
    check             scoped checks for the changed files only
    pr "TITLE"        commit, push through hz-gate, open the pull request labelled `risk: low`, auto-merge off
    wait              wait for the pull request's checks; update the branch when behind
    verify            after the orchestrator merged: the Docs run succeeded and the page shows the new text
    merge PR          orchestrator only (SHIP_ORCHESTRATOR=1): re-classify the pull request, then hz-merge-train
    status            where this change is, and the next command
    escalate "WHY"    stop and hand over

Output: one JSON object on the first stdout line (`ok`, `stage`, `msg`, `next`, ...). Exit 0: go on with `next`.
Exit 1: this stage failed (fix as `next` says; the second failure stops the whole change). Exit 3: STOP, report the
JSON to whoever started you, run nothing else. Exit 4: still running, run the same command again.
"""

import argparse
import contextlib
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

SELF = Path(__file__).resolve()
RUN = f"uv run --script {SELF}"
LABEL = "risk: low"
MAIN = "origin/main"
NOT_SMALL = "Not a small change: "
MAX_FILES, MAX_LINES = 8, 80
MAX_FAILURES = 2
BUDGET = 100  # seconds one call may take: a model's shell tool gives up at 120
GATE_CHECKS = ("merge-gate", "coverage-floor")  # .github/workflows/merge-gate.yml
FLAKY = ("End-to-end (macos)",)  # failed jobs worth one `gh run rerun --failed`, without investigation
MODEL = os.environ.get("SHIP_MODEL", "Claude Haiku 4.5")
ORCHESTRATOR = os.environ.get("SHIP_ORCHESTRATOR") == "1"

# --- the policy: what is a small change -------------------------------------------------------------------------------

# Anything matching is never small, whatever the diff says. First match names the reason.
ESCALATE_PATHS: list[tuple[str, str]] = [
    (r"^src/heinzel/kernel/", "kernel code"),
    (r"^src/", "Python source"),
    (r"^tests/", "tests"),
    (r"^\.github/", "workflows and CI"),
    (r"^\.claude/", "agent skills, hooks and settings"),
    (r"^(scripts|template|starter|desktop|fuzz|packages|agent-skills)/", "build, release, packaging and tooling"),
    (
        r"^(Makefile|pyproject\.toml|uv\.lock|copier\.yml|hatch_build\.py|mkdocs\.yml|\.pre-commit-config\.yaml)$",
        "build configuration",
    ),
    (
        r"(^|/)(package\.json|pnpm-lock\.yaml|pnpm-workspace\.yaml|biome\.json|tsconfig[^/]*\.json)$",
        "web dependencies and configuration",
    ),
    (
        r"^(CHANGELOG|ATTRIBUTIONS|SECURITY|CODE_OF_CONDUCT|INTENT|AGENTS|CLAUDE|CONTRIBUTING|PYPI)\.md$|^LICENSE",
        "a root document the maintainers own or the release process generates",
    ),
    (r"^docs/adr/", "an ADR (a decision, never rewritten)"),
    (
        r"^docs/(MODULES|CLI_REFERENCE|CONFIGURATION|CATALOG)\.md$|^docs/(plugins|_data|schemas)/|^docs/install\.sh$",
        "a generated or machine-read file",
    ),
    (
        r"^docs/(DESKTOP_[A-Z_]+|SONAR_SECURITY|PRIVATE_WEB_SHARING|PUBLISHING)\.md$",
        "a document about security, authority or publishing",
    ),
    (r"^web/src/api/|^web/e2e/", "generated API client or end-to-end tests"),
]
# Path parts that mean sensitive code. Applied to code paths; Markdown is judged by its diff instead.
SENSITIVE_WORDS = (
    "auth|oauth|approval|permission|security|secret|credential|password|token|keychain|crypto|sandbox|session|durable"
    "|daemon|lifecycle|install|update|updating|upgrade|upgrading|release|publish|bootstrap|electron|package|packaging"
    "|sonar|private|authority|gate"
)
SENSITIVE_PATH = re.compile(rf"(^|[/_.-])({SENSITIVE_WORDS})", re.IGNORECASE)
# Where a small change may live at all. Everything else escalates as "not on the small-change list".
ALLOWED_PATHS = [
    r"^docs/(?!plugins/)[^/]+\.md$",
    r"^docs/guides/[^/]+\.md$",
    r"^docs/research/[^/]+\.md$",
    r"^README\.md$",
    r"^docs/overrides/[^/]+\.html$",
    r"^web/src/(components|pages|layout|splash)/.+\.(tsx?|css)$",
    r"^web/src/index\.css$",
    r"^web/src/i18n\.ts$",  # the app's words; only plain string entries may change (I18N_ENTRY)
]
DERIVED = {"README.md": ["template/README.md.jinja"]}  # generated from the key by `generate_docs.py`
CODE = re.compile(r"\.(tsx?|css|html)$")
DANGEROUS = re.compile(
    r"\b(subprocess|eval|new Function|dangerouslySetInnerHTML|innerHTML|document\.cookie|localStorage|sessionStorage"
    r"|fetch|XMLHttpRequest|WebSocket|EventSource|password|passwd|secret|api[_-]?key|authorization|bearer|credential)\b"
    r"|<script|javascript:|<iframe|\bon[a-z]+=\"|https?://",
    re.IGNORECASE,
)
# One i18n line, stripped: `"key": "text",`, `"key":` or a continued `"text",`. Every part is anchored and the string's
# alternatives are disjoint, so matching is linear.
I18N_ENTRY = re.compile(r'(?:"[\w.-]+":)? ?(?:"(?:[^"\\]|\\.)*",?)?')
IMPORT = re.compile(r"(?:^\s*(?:import\b|export\b[^\n]{0,200}\bfrom\b))|(?:\brequire\()")
FENCE = re.compile(r"^\s*(```|~~~)")
SPAN = re.compile(r"(`[^`\n]+`)|\]\((https?://[^)\s]+)\)|(?<![\w/])(https?://[^\s)>\]]+)")
LINK = re.compile(r"\]\(([^)\s#]+)(#[^)\s]*)?\)")


# --- output, state and the failure budget -----------------------------------------------------------------------------


OUT: dict[str, Any] = {}


def emit(**fields: object) -> None:
    """Print the stage's result: one compact JSON line, `ok` and `stage` first."""
    OUT.clear()
    OUT.update({key: value for key, value in fields.items() if value not in (None, [], "")})
    print(json.dumps(OUT, ensure_ascii=False, separators=(",", ":")), flush=True)


def run(
    argv: list[str], *, cwd: Path | None = None, timeout: int = 900, env: dict[str, str] | None = None
) -> tuple[int, str]:
    """Run a command to the end; its exit code and combined output."""
    full = {**os.environ, "PATH": f"{root() / '.tools' / 'bin'}{os.pathsep}{os.environ['PATH']}", **(env or {})}
    try:
        done = subprocess.run(  # noqa: S603
            argv, cwd=cwd or root(), capture_output=True, text=True, timeout=timeout, env=full, check=False
        )
    except FileNotFoundError:
        return 127, f"{argv[0]}: command not found"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: timed out after {timeout}s"
    return done.returncode, (done.stdout + done.stderr).strip()


def git(*args: str, cwd: Path | None = None) -> str:
    """Output of a git command that is expected to work; empty when it fails."""
    code, out = run(["git", *args], cwd=cwd, timeout=60)
    return out if code == 0 else ""


_ROOT: list[Path] = []


def root() -> Path:
    """The working tree this command acts on (the current directory's)."""
    if not _ROOT:
        git_binary = shutil.which("git") or "git"
        top = subprocess.run(  # noqa: S603
            [git_binary, "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False, timeout=30
        )
        if top.returncode != 0:
            emit(
                ok=False,
                stage="setup",
                msg="Not inside a git working tree.",
                next="cd into your worktree, then run this command again",
            )
            sys.exit(2)
        _ROOT.append(Path(top.stdout.strip()))
    return _ROOT[0]


def gate(argv: list[str]) -> list[str]:
    """Heavy commands queue host-wide behind hz-gate."""
    return ["hz-gate", *argv] if shutil.which("hz-gate") else argv


def state_dir() -> Path:
    """This tree's state folder: a per-user cache folder named by a digest of its git directory."""
    digest = hashlib.sha256(git("rev-parse", "--absolute-git-dir", cwd=root()).encode()).hexdigest()[:20]
    folder = Path.home() / ".cache" / "hz-ship" / digest
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def state_file() -> Path:
    """The per-worktree state file: failures, stop flag, last check, pull request."""
    return state_dir() / "ship.json"


def load() -> dict[str, Any]:
    """The change's state; empty when nothing was recorded yet."""
    with contextlib.suppress(OSError, ValueError):
        return json.loads(state_file().read_text())
    return {"failures": [], "stopped": False}


def save(state: dict[str, Any]) -> None:
    """Write the change's state."""
    state_file().write_text(json.dumps(state, indent=1))


def branch() -> str:
    """The current branch name."""
    return git("rev-parse", "--abbrev-ref", "HEAD", cwd=root())


def report(state: dict[str, Any]) -> dict[str, Any]:
    """What the orchestrator needs to pick a stopped change up: failures with their last lines of output."""
    return {
        "dir": str(root()),
        "branch": branch(),
        "pr": state.get("pr"),
        "failures": state["failures"],
        "why": state.get("why"),
    }


def stop(stage: str, msg: str, state: dict[str, Any], *, not_small: bool = False) -> NoReturn:
    """STOP: remember it, so every later stage refuses, and print what the orchestrator needs.

    A stop for a change that is not small remembers the diff it judged: it clears itself when the diff changes into
    one that is small (see `refuse_if_stopped`). Every other stop waits for the orchestrator.
    """
    state["stopped"] = True
    state["why"] = msg
    state["kind"] = "not-small" if not_small else "failure"
    state["stopped_at"] = signature(collect()) if not_small else None
    save(state)
    show_stop(stage, msg, state)


def show_stop(stage: str, msg: str, state: dict[str, Any]) -> NoReturn:
    """Print the stop report and leave with exit code 3."""
    emit(
        ok=False,
        stage=stage,
        stop=True,
        msg=f"STOP. {msg} Run nothing else. Send this line to whoever started you.",
        report=report(state),
    )
    sys.exit(3)


def fail(stage: str, msg: str, nxt: str, detail: object = None) -> NoReturn:
    """A failed stage. The second failure of a change stops it: a small model should not flail."""
    state = load()
    state["failures"].append({"stage": stage, "msg": msg[:200], "detail": detail if isinstance(detail, list) else None})
    if len(state["failures"]) >= MAX_FAILURES:
        stop(
            stage,
            f"{len(state['failures'])} failures: "
            + "; ".join(f["stage"] + ": " + f["msg"] for f in state["failures"])
            + ".",
            state,
        )
    save(state)
    emit(
        ok=False, stage=stage, msg=msg, next=nxt, detail=detail, failures=f"{len(state['failures'])} of {MAX_FAILURES}"
    )
    sys.exit(1)


def ok(stage: str, msg: str, nxt: str, **extra: object) -> None:
    """A stage that passed: the result line and what to run next."""
    emit(ok=True, stage=stage, msg=msg, next=nxt, **extra)


def refuse_if_stopped(stage: str) -> None:
    """A stopped change runs no further stage, unless it was stopped for not being small and no longer is."""
    state = load()
    if not state.get("stopped"):
        return
    if state.get("kind") == "not-small":
        changes = collect()
        if changes and signature(changes) != state.get("stopped_at") and not classify_changes(changes):
            state.update(stopped=False, why=None, kind=None, stopped_at=None)
            save(state)
            return
    show_stop(stage, state.get("why") or "This change was stopped.", state)


# --- what changed -----------------------------------------------------------------------------------------------------


@dataclass
class Change:
    """One file's change: its status, the added and removed lines with their line numbers, and both versions."""

    path: str
    status: str  # A added, M modified, D deleted
    added: list[tuple[int, str]] = field(default_factory=list)
    removed: list[tuple[int, str]] = field(default_factory=list)
    binary: bool = False
    old_text: str = ""
    new_text: str = ""

    @property
    def lines(self) -> int:
        """Added plus removed lines."""
        return len(self.added) + len(self.removed)


def parse_file(path: str, patch: list[str]) -> Change:
    """One file's part of `git diff -U0`: status, and every added and removed line with its number."""
    change = Change(path, "M")
    old = new = 0
    for line in patch:
        if line.startswith("@@"):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", line)
            old, new = (int(match[1]), int(match[2])) if match else (old, new)
        elif line.startswith("+") and not line.startswith("+++"):
            change.added.append((new, line[1:]))
            new += 1
        elif line.startswith("-") and not line.startswith("---"):
            change.removed.append((old, line[1:]))
            old += 1
        elif line.startswith("new file mode"):
            change.status = "A"
        elif line.startswith("deleted file mode"):
            change.status = "D"
        elif line.startswith("Binary files"):
            change.binary = True
    return change


def parse_diff(text: str) -> dict[str, Change]:
    """Changes from `git diff -U0 --no-renames`, by path."""
    changes: dict[str, Change] = {}
    for part in re.split(r"(?m)^diff --git ", text)[1:]:
        head, *patch = part.splitlines()
        path = head.split(" b/", 1)[1]
        changes[path] = parse_file(path, patch)
    return changes


def collect(head: str | None = None) -> dict[str, Change]:
    """Every change against the merge base with origin/main: the working tree, or the commit `head`."""
    top = root()
    base = git("merge-base", MAIN, head or "HEAD", cwd=top) or MAIN
    target = [head] if head else []
    changes = parse_diff(
        git("diff", "-U0", "--no-color", "--no-renames", "--no-ext-diff", base, *target, "--", cwd=top)
    )
    if not head:
        for name in git("ls-files", "--others", "--exclude-standard", cwd=top).splitlines():
            data = (top / name).read_bytes()
            change = changes[name] = Change(name, "A", binary=b"\0" in data)
            if not change.binary:
                change.added = list(enumerate(data.decode("utf-8", "replace").splitlines(), 1))
    for change in changes.values():
        if change.binary:
            continue
        if change.status != "A":
            change.old_text = git("show", f"{base}:{change.path}", cwd=top)
        if change.status != "D":
            change.new_text = (
                git("show", f"{head}:{change.path}", cwd=top)
                if head
                else (top / change.path).read_text("utf-8", "replace")
            )
    return changes


def fenced(text: str) -> set[int]:
    """Line numbers inside a fenced code block, fences included."""
    inside, found = False, set()
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            inside = not inside
            found.add(number)
        elif inside:
            found.add(number)
    return found


def spans(lines: list[tuple[int, str]]) -> set[str]:
    """Inline code and external links of some lines: what carries commands and destinations."""
    return {next(g for g in m.groups() if g) for _, line in lines for m in SPAN.finditer(line)}


def path_reason(path: str) -> str | None:
    """Why a path is never a small change, or None when it may be."""
    for pattern, reason in ESCALATE_PATHS:
        if re.search(pattern, path):
            return reason
    if not any(re.search(pattern, path) for pattern in ALLOWED_PATHS):
        return "not on the small-change list: docs/*.md, README.md, docs/overrides, web/src components and pages"
    if CODE.search(path) and SENSITIVE_PATH.search(path):
        return "sensitive area by name (auth, approvals, sessions, installer, updates, release, security)"
    return None


def diff_reasons(change: Change) -> list[str]:
    """Why the content of a change is not small."""
    if change.binary:
        return ["binary file"]
    if change.status in "AD":
        return [f"{'new' if change.status == 'A' else 'deleted'} file"]
    reasons = []
    if change.path.endswith(".md"):
        touched = {n for n, _ in change.added} & fenced(change.new_text) | {n for n, _ in change.removed} & fenced(
            change.old_text
        )
        if touched:
            reasons.append(f"changes a code block (line {min(touched)})")
        moved = spans(change.added) ^ spans(change.removed)
        if moved:
            reasons.append("changes inline code, a command or an external link")
    else:
        for number, line in change.added:
            if change.path == "web/src/i18n.ts" and not I18N_ENTRY.fullmatch(line.strip()):
                reasons.append(f"line {number}: not a plain string entry")
            elif DANGEROUS.search(line):
                reasons.append(f"line {number}: security-sensitive or network code")
            elif IMPORT.search(line):
                reasons.append(f"line {number}: new import")
    return reasons[:3]


def classify_changes(changes: dict[str, Change]) -> list[str]:
    """Reasons the change is not small; empty means lane low."""
    reasons: list[str] = []
    derived = {d for source in DERIVED if source in changes for d in DERIVED[source]}
    names = [n for n in changes if n not in derived]
    if not names and not derived:
        return ["nothing changed yet"]
    for name in names:
        reason = path_reason(name)
        if reason:
            reasons.append(f"{name}: {reason}")
        else:
            reasons.extend(f"{name}: {r}" for r in diff_reasons(changes[name]))
    if len(names) > MAX_FILES:
        reasons.append(f"{len(names)} files (limit {MAX_FILES})")
    total = sum(changes[n].lines for n in names)
    if total > MAX_LINES:
        reasons.append(f"{total} changed lines (limit {MAX_LINES})")
    return reasons


def signature(changes: dict[str, Change]) -> str:
    """Identity of exactly this content, so a check never vouches for text it did not see."""
    digest = hashlib.sha256(b"ship")
    for name in sorted(changes):
        digest.update(name.encode() + b"\0" + changes[name].new_text.encode())
    return digest.hexdigest()[:16]


# --- stages -----------------------------------------------------------------------------------------------------------


def cmd_start(args: argparse.Namespace) -> None:
    """Create the worktree and branch for a change."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,40}", args.slug):
        emit(
            ok=False,
            stage="start",
            msg="SLUG must be 3 to 41 characters: lowercase letters, digits and dashes.",
            next=f"{RUN} start fix-install-typo",
        )
        sys.exit(2)
    top = root()
    target = top.parent / f"heinzel-{args.slug}"
    name = f"{args.kind}/{args.slug}"
    if target.exists():
        emit(
            ok=False,
            stage="start",
            msg=f"{target} exists.",
            next=f"Use it: cd {target} && {RUN} status. Or choose another SLUG.",
        )
        sys.exit(2)
    run(["git", "fetch", "--quiet", "origin", "main"], timeout=120)
    code, out = run(["git", "worktree", "add", "-q", str(target), "-b", name, args.base], timeout=120)
    if code != 0:
        emit(
            ok=False,
            stage="start",
            msg="git worktree add failed.",
            detail=out.splitlines()[-5:],
            next="Stop and report this line.",
        )
        sys.exit(3)
    code, out = run(["make", "--no-print-directory", "install"], cwd=target, timeout=600)
    detail = None if code == 0 else out.splitlines()[-5:]
    own = target / "scripts" / "ship.py"
    run_here = f"uv run --script {own}" if own.exists() else RUN
    ok(
        "start",
        f"Worktree ready on branch {name}.",
        f"cd {target} && {run_here} classify --files PATH [PATH ...]  (every path you will edit; then edit them)",
        dir=str(target),
        branch=name,
        detail=detail,
    )


def require_changes(stage: str) -> dict[str, Change]:
    """The current changes; a stage asked before any edit is a usage error, not a reason to stop."""
    changes = collect()
    if not changes:
        emit(ok=False, stage=stage, msg="No edits yet.", next=f"Edit the files you listed, then run: {RUN} classify")
        sys.exit(2)
    return changes


def cmd_classify(args: argparse.Namespace) -> None:
    """Say whether the change (or the paths about to be edited) is small."""
    if args.files:
        reasons = [f"{name}: {why}" for name in args.files if (why := path_reason(name))]
        if reasons:
            emit(
                ok=False,
                stage="classify",
                lane="escalate",
                reasons=reasons,
                msg="STOP. This file is not a small change. Do not edit it. Send this line to whoever started you.",
                next="stop",
            )
            sys.exit(3)
        ok(
            "classify",
            "These paths are allowed. Edit them now.",
            f"Edit them, then run: {RUN} classify (it reads the real diff)",
            lane="allowed",
        )
        return
    refuse_if_stopped("classify")
    changes = require_changes("classify")
    reasons = classify_changes(changes)
    state = load()
    if reasons:
        stop("classify", NOT_SMALL + "; ".join(reasons[:4]) + ".", state, not_small=True)
    state["lane"] = "low"
    save(state)
    total = sum(c.lines for c in changes.values())
    ok("classify", f"lane low: {len(changes)} files, {total} lines.", f"{RUN} check", lane="low", files=sorted(changes))


def normalize(top: Path, names: list[str]) -> None:
    """What the commit hooks would do anyway: no trailing whitespace, exactly one final newline."""
    for name in names:
        path = top / name
        if not path.is_file():
            continue
        text = path.read_text("utf-8")
        clean = "\n".join(line.rstrip() for line in text.split("\n")).rstrip("\n") + "\n"
        if clean != text:
            path.write_text(clean, "utf-8")


def relative_links(top: Path, name: str) -> list[str]:
    """Links in a Markdown file whose target file does not exist."""
    broken = []
    text = (top / name).read_text("utf-8")
    code = fenced(text)
    outside = "\n".join(line for number, line in enumerate(text.splitlines(), 1) if number not in code)
    for match in LINK.finditer(outside):
        target = match[1]
        if re.match(r"[a-z][a-z0-9+.-]*:|/|<", target):
            continue
        if not (top / name).parent.joinpath(target).resolve().exists():
            broken.append(f"{name}: link target {target} does not exist")
    return broken


def steps_for(changes: dict[str, Change]) -> list[tuple[str, list[str]]]:
    """Light scoped checks, (name, command), for exactly the changed files."""
    names = sorted(n for n in changes if changes[n].status != "D")
    steps = [
        ("confidential", ["uv", "run", "--quiet", "--script", "scripts/check_confidentiality.py", *names]),
        ("typos", ["uv", "run", "--quiet", "typos", *names]),
    ]
    if any(n.endswith(".md") for n in names):
        unset = ["env", "-u", "HZ_SYSTEM_AGENT__SKILL_DIRS", "uv", "run", "--quiet", "python"]
        steps.append(("generated", [*unset, "scripts/generate_docs.py", "--check"]))
    if any(n.endswith(".md") or n.startswith("docs/overrides/") for n in names):
        tests = ["tests/unit/documentation_test.py", "tests/unit/standards_test.py", "tests/unit/repository_test.py"]
        tests += ["tests/unit/pypi_description_test.py", "tests/unit/readme_diagnostics_test.py"]
        steps.append(("doc-tests", ["uv", "run", "--quiet", "pytest", *tests, "-n", "2", "-q", "--tb=short"]))
    return steps


def web_steps(changes: dict[str, Change]) -> list[str]:
    """The web checks as `steps.py` arguments: format the changed files, then types and related tests together."""
    web = [n[4:] for n in sorted(changes) if n.startswith("web/") and changes[n].status != "D"]
    if not web:
        return []
    run_web = "uv run --quiet --script scripts/web_build.py exec"
    files = shlex.join(web)
    return [
        f"biome={run_web} biome check --write {files}",
        f"types={run_web} node node_modules/@typescript/native/bin/tsc --noEmit -p .",
        f"&tests={run_web} vitest related --run --passWithNoTests {files}",
    ]


def fail_step(name: str, out: str) -> NoReturn:
    """A check that failed: escalate when the cause is outside this change, else a failure with the output's tail."""
    lines = [ln for ln in out.splitlines() if ln.strip()][-12:]
    if name == "generated":
        stop("check", "A generated file is out of date after your edit.", load())
    if name == "doc-tests":
        what = [ln.strip() for ln in out.splitlines() if ln.startswith(("E ", "FAILED"))][:3]
        stop(
            "check",
            "A test asserts the text you changed, so this is not a docs-only change: " + " | ".join(what),
            load(),
        )
    nxt = {
        "confidential": "Reword the text at the reported location, never quote the term. Run check again",
        "typos": "Fix the spelling at each reported file:line. Then run check again",
    }.get(name, "Fix only what the lines in detail name. Then run check again")
    fail("check", f"{name} failed.", nxt, lines)


def stage_check() -> None:
    """Run the scoped checks for the changed files."""
    refuse_if_stopped("check")
    top = root()
    changes = require_changes("check")
    reasons = classify_changes(changes)
    if reasons:
        stop("check", NOT_SMALL + "; ".join(reasons[:3]) + ".", load(), not_small=True)
    normalize(top, sorted(changes))
    changes = collect()
    broken = [
        p for n in sorted(changes) if n.endswith(".md") and changes[n].status != "D" for p in relative_links(top, n)
    ]
    if broken:
        where = (
            "Fix the link target in the file named in detail (the path is relative to that file), then run check again"
        )
        fail("check", "Broken relative link.", where, broken[:5])
    done = []
    for name, command in steps_for(changes):
        code, out = run(command, cwd=top, timeout=900)
        if code != 0 and "Timeout (>" in out:  # a test's own time limit on a loaded host says nothing about the text
            code, out = run(command, cwd=top, timeout=900)
        if code != 0:
            fail_step(name, out)
        done.append(name)
    heavy = web_steps(changes)
    if heavy:  # one place in the hz-gate queue for all of them
        code, out = run(gate(["uv", "run", "--quiet", "--script", "scripts/steps.py", *heavy]), cwd=top, timeout=3000)
        if code != 0:
            failed = next((ln.split()[1] for ln in out.splitlines() if ln.startswith("✗")), "web")
            fail_step(failed, out)
        done += ["biome", "types", "tests"]
    changes = collect()  # biome --write may have formatted files
    state = load()
    state["checked"] = signature(changes)
    save(state)
    example = suggest(changes)
    ok(
        "check",
        f"Passed: {', '.join(done)}.",
        f'{RUN} pr "<type>(<scope>): <summary>"  (example: {example})',
        checked=state["checked"],
    )


def suggest(changes: dict[str, Change]) -> str:
    """A conventional title that fits the files, as the example in a message."""
    names = list(changes)
    if all(n.startswith(("docs/", "README")) for n in names):
        return "docs(docs): fix the install guide's wrong link"
    return "fix(web): align the footer label"


def title_problem(title: str) -> str | None:
    """Why a pull request title fails commitizen, or None; the message lists what is allowed."""
    config = tomllib.loads((root() / "pyproject.toml").read_text("utf-8"))["tool"]["commitizen"]["customize"]
    pattern = config["schema_pattern"]
    if re.match(pattern, title):
        return None
    scopes = re.search(r"\\\(\((?P<scopes>[a-z|]+)\)\\\)", pattern)
    allowed = scopes["scopes"].replace("|", ", ") if scopes else "see pyproject.toml"
    return (
        "Title must be `type(scope): summary`, at most 72 characters. Types: feat, fix, perf, refactor, docs, test, "
        f"build, ci, chore, style, revert. The scope is one of: {allowed}. For a docs page use `docs(docs)`, for the "
        "web app `fix(web)`."
    )


def slug_repo() -> str:
    """owner/name of the origin remote."""
    url = git("remote", "get-url", "origin", cwd=root())
    return re.sub(r"^.*[:/]([^/:]+/[^/:]+?)(?:\.git)?/*$", r"\1", url)


def gh(*args: str) -> tuple[int, str]:
    """Run gh; exit code and output."""
    return run(["gh", *args], timeout=120)


def commit_all(title: str, body: str) -> list[str]:
    """Commit the working tree; hooks may fix whitespace and fail once, so try twice. Empty on success."""
    top = root()
    if not git("status", "--porcelain", cwd=top):
        return []
    out = ""
    for _ in range(2):
        run(["git", "add", "-A"], cwd=top, timeout=60)
        code, out = run(["git", "commit", "-q", "-m", title, "-m", body], cwd=top, timeout=600)
        if code == 0:
            return []
    return out.splitlines()[-10:]


def sync_with_main() -> str:
    """Bring the branch up to date with origin/main (rebase before the first push, merge after). Empty, or why not."""
    top = root()
    run(["git", "fetch", "--quiet", "origin", "main"], cwd=top, timeout=120)
    if run(["git", "merge-base", "--is-ancestor", MAIN, "HEAD"], cwd=top, timeout=60)[0] == 0:
        return ""
    pushed = bool(git("ls-remote", "--heads", "origin", branch(), cwd=top))
    command = ["git", "merge", "--no-edit", MAIN] if pushed else ["git", "rebase", MAIN]
    code, out = run(command, cwd=top, timeout=300)
    if code != 0:
        run(["git", "merge" if pushed else "rebase", "--abort"], cwd=top, timeout=60)
        return out.splitlines()[-1] if out else "git could not combine them."
    return ""


def write_body(text: str) -> Path:
    """The pull request description: why, the lane and its rules, the files."""
    names = "\n".join(f"- `{name}`" for name in sorted(collect()))
    path = state_dir() / "pr-body.md"
    path.write_text(
        f"## What and why\n\n{text}\n\n## Risk\n\nLane `low`, classified by `scripts/ship.py` from the file paths and "
        "the diff: only docs, site templates or small web UI; no code block, command, external link, import or "
        f"security-sensitive code changed.\n\nFiles:\n{names}\n\n"
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n",
        "utf-8",
    )
    return path


def label_and_unarm(number: int, url: str, state: dict[str, object]) -> None:
    """Label the pull request, take it out of draft and make sure auto-merge is off (a draft never enables it)."""
    repo = slug_repo()
    color = ("--color", "0e8a16", "--description", "Small change classified by scripts/ship.py")
    gh("label", "create", LABEL, "-R", repo, *color)
    gh("pr", "edit", str(number), "-R", repo, "--add-label", LABEL)
    gh("pr", "ready", str(number), "-R", repo)
    gh("pr", "merge", str(number), "-R", repo, "--disable-auto")
    _, armed = gh(
        "pr", "view", str(number), "-R", repo, "--json", "autoMergeRequest", "--jq", ".autoMergeRequest != null"
    )
    if armed.strip() == "true":
        stop("pr", f"Auto-merge is on for {url} and could not be turned off.", state)


def stage_pr(args: argparse.Namespace) -> None:
    """Commit, push through hz-gate, open the pull request and make sure auto-merge is off."""
    refuse_if_stopped("pr")
    problem = title_problem(args.title)
    if problem:
        emit(ok=False, stage="pr", msg=problem, next=f'{RUN} pr "docs(docs): fix a typo in the install guide"')
        sys.exit(2)
    state = load()
    if branch() in ("main", "HEAD"):
        stop("pr", "You are on main or a detached HEAD, not on your branch.", state)
    changes = require_changes("pr")
    reasons = classify_changes(changes)
    if reasons:
        stop("pr", NOT_SMALL + "; ".join(reasons[:3]) + ".", state, not_small=True)
    if state.get("checked") != signature(changes):
        emit(ok=False, stage="pr", msg="Files changed since the last check.", next=f"{RUN} check")
        sys.exit(2)
    refused = commit_all(
        args.title, f"{args.body or 'See the title.'}\n\nCo-Authored-By: {MODEL} <noreply@anthropic.com>"
    )
    if refused:
        fail(
            "pr",
            "git commit was refused by a hook.",
            "Fix only what detail names, then run check, then pr again",
            refused,
        )
    synced = sync_with_main()
    if synced:
        stop("pr", f"Your branch conflicts with main: {synced}", state)
    path = write_body(args.body or args.title)
    command = ["make", "--no-print-directory", "pr", f"TITLE={args.title}", f"BODY_FILE={path}"]
    env = {"NO_PREFLIGHT": "1", "DRAFT": "1"}
    code, out = run(gate(command), cwd=root(), timeout=4 * 3600, env=env)  # the hz-gate queue may be long
    url = next((m[0] for m in re.finditer(r"https://github\.com/\S+/pull/\d+", out)), None)
    if code != 0 or not url:
        hint = "Read detail. A hook or test you did not cause, or a refused push: escalate. Else fix what it names"
        fail("pr", "make pr failed.", hint, out.splitlines()[-12:])
    number = int(url.rsplit("/", 1)[1])
    label_and_unarm(number, url, state)
    state.update(pr=number, url=url)
    save(state)
    ok("pr", f"Pull request open, auto-merge off, labelled `{LABEL}`.", f"{RUN} wait", url=url, pr=number)


def checks_of(data: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]], bool]:
    """(pending names, failed checks, CI OK green) from a statusCheckRollup."""
    pending, bad, green = [], [], False
    for item in data.get("statusCheckRollup") or []:
        name = item.get("name") or item.get("context") or "?"
        status = item.get("status") or ("COMPLETED" if item.get("state") not in ("PENDING", "EXPECTED") else "PENDING")
        result = item.get("conclusion") or item.get("state")
        if status != "COMPLETED":
            pending.append(name)
        elif result in ("FAILURE", "CANCELLED", "TIMED_OUT", "ERROR", "ACTION_REQUIRED"):
            bad.append({"name": name, "url": item.get("detailsUrl") or item.get("targetUrl") or ""})
        elif name == "CI OK" and result == "SUCCESS":
            green = True
    return pending, bad, green


def pr_number(arg: int | None, state: dict[str, Any]) -> int:
    """The pull request to act on: the argument, or the one this change opened."""
    number = arg or state.get("pr")
    if not number:
        emit(
            ok=False,
            stage="wait",
            msg="No pull request known for this change.",
            next=f'{RUN} pr "<type>(<scope>): <summary>"',
        )
        sys.exit(2)
    return int(number)


def red(state: dict[str, Any], data: dict[str, Any], bad: list[dict[str, Any]], repo: str) -> NoReturn:
    """Failed checks: one rerun for known flaky jobs, otherwise a failure with the log excerpt."""
    names = [b["name"] for b in bad]
    run_id = re.search(r"/runs/(\d+)", bad[0]["url"])
    if run_id and not state.get("rerun") and all(n.startswith(FLAKY) for n in names):
        gh("run", "rerun", run_id[1], "-R", repo, "--failed")
        state["rerun"] = True
        save(state)
        emit(
            ok=True, stage="wait", pending=True, msg="A known flaky job failed; it was rerun once.", next=f"{RUN} wait"
        )
        sys.exit(4)
    _, logs = run(
        ["uv", "run", "--quiet", "--script", "scripts/ci_logs.py", "--branch", data["headRefName"]], timeout=300
    )
    what = "Read detail. Fix only the lines it names, then run check, then pr (same title), then wait."
    fail(
        "wait",
        "Checks failed: " + ", ".join(names[:4]) + ".",
        what + " If it is a file you did not edit, escalate",
        logs.splitlines()[:14],
    )


def stage_wait(args: argparse.Namespace) -> None:
    """Wait for the checks of the pull request."""
    refuse_if_stopped("wait")
    state = load()
    number, repo = pr_number(args.pr, state), slug_repo()
    deadline = time.monotonic() + args.budget
    fields = "state,mergeStateStatus,mergeable,statusCheckRollup,headRefName,headRefOid,url"
    while True:
        code, out = gh("pr", "view", str(number), "-R", repo, "--json", fields)
        if code != 0:
            fail("wait", "gh could not read the pull request.", f"{RUN} wait", out.splitlines()[-3:])
        data = without_advisory_gates(json.loads(out), repo)
        if data["state"] != "OPEN" or data["mergeable"] == "CONFLICTING":
            if data["state"] == "MERGED":
                ok("wait", "Merged.", f"{RUN} verify", pr=number)
                return
            stop("wait", f"Pull request #{number} is {data['state'].lower()} or conflicts with main.", state)
        pending, bad, green = checks_of(data)
        if data["mergeStateStatus"] == "BEHIND":
            gh("pr", "update-branch", str(number), "-R", repo)
            pending = ["update-branch"]
        if bad and not pending:
            red(state, data, bad, repo)
        if green and not pending:
            head = data["headRefOid"][:8]
            blocked = data["mergeStateStatus"] not in ("CLEAN", "UNSTABLE", "HAS_HOOKS")
            note = (
                f" Merge state is {data['mergeStateStatus']}: a review or an unresolved thread blocks it."
                if blocked
                else ""
            )
            ready = f"{RUN} merge {number}"
            ok("wait", f"All checks green at {head}.{note}", ready, url=data["url"], head=head)
            return
        if time.monotonic() > deadline:
            waiting = ", ".join(pending[:3]) or "CI OK"
            emit(
                ok=True,
                stage="wait",
                pending=True,
                msg=f"Still running: {waiting}.",
                next=f"{RUN} wait --budget 540  (set the shell timeout to 600000)",
            )
            sys.exit(4)
        time.sleep(20)


def site_url() -> str:
    """The published site's address, from mkdocs.yml (it has moved before)."""
    found = re.search(r"(?m)^site_url:\s*(\S+)", (root() / "mkdocs.yml").read_text("utf-8"))
    return found[1].rstrip("/") + "/" if found else ""


def page_url(path: str) -> str | None:
    """Where the site shows docs/<path>.md."""
    if path.startswith("docs/") and path.endswith(".md"):
        page = path[5:-3].removesuffix("README").removesuffix("/")  # README.md is a directory's index page
        return site_url() + (page + "/" if page else "")
    return None


def plain(line: str) -> list[str]:
    """The words of a Markdown line as the published page shows them: no tags, URLs, link targets or markup."""
    text = re.sub(r"<[^>]+>|\]\([^)]*\)|https?://\S+|!\[", " ", line)
    return re.sub(r"[`*_>#\[\]()|]|^\s*[-+\d.]+\s", "", text).split()


def snippet(change: Change) -> str:
    """A few words around what changed in the longest readable added line, or "" when no added line has prose."""
    readable = [(len(words), words) for _, line in change.added if len(words := plain(line)) >= 3]  # noqa: PLR2004
    if not readable:
        return ""
    words = max(readable)[1]
    old = [w for _, line in change.removed for w in plain(line)]
    matcher = difflib.SequenceMatcher(a=old, b=words, autojunk=False)
    spots = [op for op in matcher.get_opcodes() if op[0] != "equal" and op[4] > op[3]]
    start, end = (spots[0][3], spots[0][4]) if spots else (0, len(words))
    return " ".join(words[max(0, start - 2) : min(len(words), max(end, start + 1) + 2)][:10])


def fetch(url: str) -> str:
    """The body of an https page, or an empty string."""
    if not url.startswith("https://"):
        return ""
    request = urllib.request.Request(f"{url}?ship={int(time.time())}", headers={"User-Agent": "ship/1"})  # noqa: S310
    with (
        contextlib.suppress(urllib.error.URLError, TimeoutError, OSError),
        urllib.request.urlopen(request, timeout=20) as response,  # noqa: S310
    ):
        return response.read().decode("utf-8", "replace")
    return ""


def wait_docs_run(repo: str, sha: str, budget: int) -> None:
    """Return when the Docs runs of the merge commit are over; fail when one failed; exit 4 while they run."""
    deadline = time.monotonic() + budget
    while True:
        listed = ("run", "list", "-R", repo, "--workflow", "docs.yml", "--commit", sha)
        code, out = gh(*listed, "--json", "status,conclusion,databaseId", "--limit", "5")
        runs = json.loads(out) if code == 0 and out else []
        done = [r for r in runs if r["status"] == "completed"]
        if any(r["conclusion"] in ("failure", "timed_out") for r in done):
            fail(
                "verify",
                "The Docs run failed.",
                f"Stop and report: gh run view {done[0]['databaseId']} -R {repo} --log-failed",
            )
        if runs and len(done) == len(
            runs
        ):  # success, or superseded by a later push (cancelled): the page itself is the proof
            return
        if time.monotonic() > deadline:
            next_ = f"{RUN} verify --budget 540  (set the shell timeout to 600000)"
            emit(ok=True, stage="verify", pending=True, msg="The Docs run is not done yet.", next=next_)
            sys.exit(4)
        time.sleep(20)


def pages_missing(pages: list[tuple[str, str]]) -> list[str]:
    """The pages that do not (yet) show their new text; each is polled for a minute."""
    missing = []
    for url, text in pages:
        for _ in range(6):
            body = fetch(url)
            page = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
            if text and text.lower() in page.lower():
                break
            time.sleep(10)
        else:
            missing.append(f"{url} does not show: {text}" if body else f"{url} could not be loaded")
    return missing


def stage_verify(args: argparse.Namespace) -> None:
    """After the merge: the Docs run succeeded and the live page shows the new text."""
    refuse_if_stopped("verify")
    state = load()
    number, repo = pr_number(args.pr, state), slug_repo()
    code, out = gh("pr", "view", str(number), "-R", repo, "--json", "state,mergeCommit")
    data = json.loads(out) if code == 0 else {}
    if data.get("state") != "MERGED":
        msg = f"#{number} is not merged. Merging is the orchestrator's job."
        emit(
            ok=False,
            stage="verify",
            msg=msg,
            next="Stop. Tell the orchestrator the PR URL; run verify after it merged.",
        )
        sys.exit(2)
    sha = data["mergeCommit"]["oid"]
    git("fetch", "--quiet", "origin", "main", cwd=root())
    changes = parse_diff(git("diff", "-U0", "--no-color", "--no-renames", f"{sha}^", sha, cwd=root()))
    pages = [(url, text) for n, c in changes.items() if (url := page_url(n)) and (text := snippet(c))]
    if not pages:
        ok("verify", "Merged; no published page shows these files.", "Done. Report the PR URL as shipped.", sha=sha[:8])
        return
    wait_docs_run(repo, sha, args.budget)
    missing = pages_missing(pages)
    if missing:
        wait = "run verify once more only if the Docs run finished less than 2 minutes ago"
        fail(
            "verify",
            "The live page does not show the change.",
            f"Stop and report this line to the orchestrator; Pages may need a minute, {wait}",
            missing,
        )
    ok(
        "verify",
        f"Docs run green and {len(pages)} live page(s) show the new text.",
        "Done. Report the PR URL as shipped.",
        pages=[u for u, _ in pages],
        sha=sha[:8],
    )


def read_pull(repo: str, number: int) -> dict[str, Any]:
    """The pull request's state, labels, head and checks as GitHub reports them now."""
    fields = "labels,state,isDraft,headRefOid,mergeStateStatus,mergeable,statusCheckRollup"
    code, out = gh("pr", "view", str(number), "-R", repo, "--json", fields)
    return without_advisory_gates(json.loads(out), repo) if code == 0 else {}


def without_advisory_gates(data: dict[str, Any], repo: str) -> dict[str, Any]:
    """Drop the merge gate's checks while main doesn't require them: they report, GitHub would let the merge through.

    Once a check is required it counts here too. When the rules can't be read every check counts: never a looser gate.
    """
    code, out = gh("api", f"repos/{repo}/rules/branches/main")
    try:
        rules = json.loads(out) if code == 0 else None
    except ValueError:
        rules = None
    if not isinstance(rules, list):
        return data
    required = {
        check["context"]
        for rule in rules
        if rule.get("type") == "required_status_checks"
        for check in rule["parameters"]["required_status_checks"]
    }
    advisory = set(GATE_CHECKS) - required
    rollup = data.get("statusCheckRollup") or []
    kept = [item for item in rollup if (item.get("name") or item.get("context")) not in advisory]
    if len(kept) == len(rollup):
        return data
    # GitHub says UNSTABLE for a failing or pending check it doesn't require; every other check is still judged.
    status = "CLEAN" if data.get("mergeStateStatus") == "UNSTABLE" else data.get("mergeStateStatus")
    return {**data, "statusCheckRollup": kept, "mergeStateStatus": status}


def merge_blocker(data: dict[str, Any]) -> str:
    """Why a low-lane pull request cannot merge yet, or "" when every check is green at its head and it is clean."""
    pending, bad, green = checks_of(data)
    if pending:
        return "checks are still running: " + ", ".join(pending[:3])
    if bad:
        return "checks failed: " + ", ".join(b["name"] for b in bad[:3])
    if not green:
        return "CI OK has not passed"
    if data["mergeStateStatus"] != "CLEAN":
        return f"the pull request is {data['mergeStateStatus']}, not CLEAN (behind main, or a review blocks it)"
    return ""


def train_merge(repo: str, number: int) -> None:
    """Orchestrator: hand a pull request that is not ready yet to the merge train, which waits and updates it."""
    train = shutil.which("hz-merge-train") or str(Path.home() / ".local/bin/hz-merge-train")
    _, out = run([train, repo, str(number)], timeout=3000)
    merged = "merged at" in out
    last = out.splitlines()[-1] if out else "hz-merge-train printed nothing."
    nxt = f"{RUN} verify --pr {number}" if merged else "Read the message; the train never forces a merge."
    emit(ok=merged, stage="merge", msg=last, next=nxt)
    sys.exit(0 if merged else 1)


def stage_merge(args: argparse.Namespace) -> None:
    """Merge a low-lane pull request, without the orchestrator, once it is green at its exact head.

    The labelled pull request is re-classified from GitHub's own data at that head, so the label alone never merges
    anything. Anything not low, or not ready, is not merged here: the orchestrator uses `hz-merge-train`.
    """
    repo, number = slug_repo(), args.pr
    data = read_pull(repo, number)
    labels = [label["name"] for label in data.get("labels", [])]
    if data.get("state") != "OPEN" or data.get("isDraft") or LABEL not in labels:
        msg = f"#{number} must be open, ready and labelled `{LABEL}`."
        emit(ok=False, stage="merge", msg=msg, next="Not a low-lane pull request: the orchestrator merges it.")
        sys.exit(2)
    head = data["headRefOid"]
    run(["git", "fetch", "--quiet", "origin", f"pull/{number}/head"], timeout=120)
    if git("rev-parse", "FETCH_HEAD", cwd=root()) != head:
        emit(
            ok=False, stage="merge", msg="The pull request head moved while it was read.", next=f"{RUN} merge {number}"
        )
        sys.exit(2)
    reasons = classify_changes(collect(head))
    if reasons:
        msg = "The label says low but the diff is not. The orchestrator reviews and merges it by hand."
        emit(ok=False, stage="merge", stop=True, lane="escalate", reasons=reasons, msg=msg, next="stop")
        sys.exit(3)
    blocker = merge_blocker(data)
    if blocker and ORCHESTRATOR:
        train_merge(repo, number)
    if blocker:
        emit(ok=True, stage="merge", pending=True, msg=f"Not ready: {blocker}.", next=f"{RUN} wait --pr {number}")
        sys.exit(4)
    code, out = gh("pr", "merge", str(number), "-R", repo, "--squash", "--match-head-commit", head)
    if code != 0:
        fail(
            "merge",
            "GitHub refused the merge.",
            f"Read detail. Stop and report it; do not retry more than once: {RUN} merge {number}",
            out.splitlines()[-4:],
        )
    ok("merge", f"Merged #{number} at {head[:8]}.", f"{RUN} verify --pr {number}", pr=number, head=head[:8])


def cmd_status(_: argparse.Namespace) -> None:
    """Where the change stands and what to run next."""
    state = load()
    changes = collect()
    lane = "n/a"
    if changes and not classify_changes(changes):
        lane = "low"
    if state.get("stopped"):
        lane = "stopped"
    if state.get("stopped"):
        nxt = "stop"
    elif state.get("pr"):
        nxt = f"{RUN} wait"
    elif state.get("checked") == signature(changes) and changes:
        nxt = f'{RUN} pr "<type>(<scope>): <summary>"'
    elif changes:
        nxt = f"{RUN} classify"
    else:
        nxt = f"{RUN} classify --files PATH [PATH ...]"
    emit(
        ok=not state.get("stopped"),
        stage="status",
        lane=lane,
        branch=branch(),
        dir=str(root()),
        pr=state.get("pr"),
        failures=len(state["failures"]),
        files=sorted(changes),
        next=nxt,
    )
    sys.exit(3 if state.get("stopped") else 0)


def cmd_escalate(args: argparse.Namespace) -> None:
    """Stop on request."""
    stop("escalate", args.why, load())


def cmd_reset(_: argparse.Namespace) -> None:
    """Orchestrator only: clear the failures of a stopped change."""
    if not ORCHESTRATOR:
        emit(
            ok=False,
            stage="reset",
            msg="Only the orchestrator resets a stopped change (SHIP_ORCHESTRATOR=1).",
            next="stop",
        )
        sys.exit(3)
    state = load()
    state.update(failures=[], stopped=False, why=None, kind=None, stopped_at=None)
    save(state)
    ok("reset", "Failures cleared.", f"{RUN} status")


# --- long stages run detached, so a shell timeout cannot kill them ---


def relay(name: str, argv: list[str], budget: int, *, gated: bool = True) -> None:
    """Run a long stage as a detached job and wait up to `budget` seconds; call again to keep waiting."""
    folder = state_dir() / "jobs"
    folder.mkdir(exist_ok=True)
    result, pid, log = folder / f"{name}.json", folder / f"{name}.pid", folder / f"{name}.log"
    key = hashlib.sha256(" ".join([*argv, signature(collect())]).encode()).hexdigest()
    meta = folder / f"{name}.key"
    if meta.exists() and meta.read_text() != key:  # the inputs changed since that job started
        for stale in (result, pid, meta):
            stale.unlink(missing_ok=True)
    if not result.exists() and not pid.exists():
        meta.write_text(key)
        (folder / f"{name}.spec").write_text(json.dumps(argv))  # the child reads its arguments here, not from argv
        with log.open("w") as sink:
            child = subprocess.Popen(  # noqa: S603
                [sys.executable, str(SELF), "_job", name],
                cwd=root(),
                stdout=sink,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        pid.write_text(str(child.pid))
    deadline = time.monotonic() + budget
    while not result.exists() and time.monotonic() < deadline:
        time.sleep(1)
    if not result.exists():
        holder = Path.home().joinpath(".cache", "hz-gate.lock")
        queue = holder.read_text("utf-8").strip()[:100] if gated and holder.exists() else ""
        emit(
            ok=True,
            stage=name,
            pending=True,
            msg=f"{name} is still running." + (f" Queued behind hz-gate: {queue}" if queue else ""),
            next=f"Run the same command again: {RUN} {shlex.join(sys.argv[1:])}",
        )
        sys.exit(4)
    data = json.loads(result.read_text())
    for done in (result, pid, meta):
        done.unlink(missing_ok=True)
    print(json.dumps(data["out"], ensure_ascii=False, separators=(",", ":")), flush=True)
    sys.exit(data["code"])


def cmd_job(args: argparse.Namespace) -> None:
    """Internal: run one stage in the background and store its output and exit code.

    Raises:
        SystemExit: with the stage's exit code, once it is stored.
    """
    folder = state_dir() / "jobs"
    argv = [str(item) for item in json.loads((folder / f"{args.name}.spec").read_text())]
    result = folder / f"{args.name}.json"
    try:
        dispatch(argv)
    except SystemExit as stopped:
        result.write_text(json.dumps({"code": int(stopped.code or 0), "out": OUT}))
        raise
    result.write_text(json.dumps({"code": 0, "out": OUT}))


# --- entry ------------------------------------------------------------------------------------------------------------


EXITS = "Exit codes: 0 go on with `next`; 1 failed, fix what detail names; 2 wrong usage; 3 STOP; 4 run it again."
STAGES = {
    "start": ("New worktree and branch from origin/main.", "start fix-install-typo"),
    "classify": (
        "Is the change small? With --files: may these files be edited? Without: reads your real diff.",
        "classify",
    ),
    "check": ("Scoped checks for exactly the files you changed. Fixes whitespace and formatting itself.", "check"),
    "pr": (
        "Commit, push through hz-gate and open the pull request (risk: low, auto-merge off).",
        'pr "docs(docs): fix a typo" --body "why"',
    ),
    "wait": ("Wait for the pull request's checks; merges main in when behind.", "wait"),
    "verify": ("After the orchestrator merged: the Docs run is green and the live page shows your text.", "verify"),
    "merge": (
        "Orchestrator only (SHIP_ORCHESTRATOR=1): re-classify the pull request, then hz-merge-train.",
        "merge 123",
    ),
    "status": ("Where this change is, and the next command.", "status"),
    "reset": ("Orchestrator only (SHIP_ORCHESTRATOR=1): clear a stopped change.", "reset"),
    "escalate": ("Stop and hand over, with one sentence why.", 'escalate "the change needs a test edit"'),
}


def parser() -> argparse.ArgumentParser:
    """The command line; every stage has its own --help."""
    cli = argparse.ArgumentParser(
        prog="ship",
        description="Golden path for small changes: one command per stage, one JSON line of output.",
        epilog=f"Run a stage with --help for its usage. {EXITS}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = cli.add_subparsers(dest="command", required=True, metavar="STAGE")

    def stage_parser(name: str) -> argparse.ArgumentParser:
        """A stage's sub-parser whose --help says what it does, an example, and the exit codes."""
        summary, example = STAGES[name]
        return sub.add_parser(
            name,
            help=summary,
            description=summary,
            epilog=f"Example: {RUN} {example}\n{EXITS}",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

    stage_parser("start").add_argument("slug", help="3 to 41 characters: lowercase letters, digits, dashes")
    start = sub.choices["start"]
    start.add_argument("--kind", default="docs", choices=["docs", "fix", "feat", "chore"], help="branch prefix")
    start.add_argument("--base", default=MAIN, help="what the branch starts from")
    stage_parser("classify").add_argument("--files", nargs="+", help="paths you are about to edit")
    stage_parser("check").add_argument("--budget", type=int, default=BUDGET, help="seconds to wait per call")
    pr = stage_parser("pr")
    pr.add_argument("title", help='conventional commit title, for example "docs(docs): fix a typo"')
    pr.add_argument("--body", default="", help="why, in a sentence or two")
    pr.add_argument("--budget", type=int, default=BUDGET, help="seconds to wait per call")
    for name in ("wait", "verify"):
        waiter = stage_parser(name)
        waiter.add_argument("--pr", type=int, help="pull request number (default: the one this change opened)")
        waiter.add_argument("--budget", type=int, default=BUDGET, help="seconds to wait per call")
    stage_parser("merge").add_argument("pr", type=int, help="number of a pull request labelled risk: low")
    stage_parser("status")
    stage_parser("reset")
    stage_parser("escalate").add_argument("why", help="one sentence")
    job = sub.add_parser("_job")
    job.add_argument("name", choices=["check", "pr"])
    return cli


def cmd_check(args: argparse.Namespace) -> None:
    """`check` as the model runs it: a detached job; inside the job, the stage itself."""
    stage_check() if os.environ.get("SHIP_IN_JOB") else relay(
        "check", ["check"], args.budget, gated=bool(web_steps(collect()))
    )


def cmd_pr(args: argparse.Namespace) -> None:
    """`pr` as the model runs it: a detached job; inside the job, the stage itself."""
    if os.environ.get("SHIP_IN_JOB"):
        stage_pr(args)
    else:
        relay("pr", ["pr", args.title, "--body", args.body], args.budget)


def cmd_job_entry(args: argparse.Namespace) -> None:
    """Internal: mark the process as the job, then run the stage."""
    os.environ["SHIP_IN_JOB"] = "1"
    cmd_job(args)


HANDLERS = {
    "start": cmd_start,
    "classify": cmd_classify,
    "check": cmd_check,
    "pr": cmd_pr,
    "wait": stage_wait,
    "verify": stage_verify,
    "merge": stage_merge,
    "status": cmd_status,
    "reset": cmd_reset,
    "escalate": cmd_escalate,
    "_job": cmd_job_entry,
}


def dispatch(argv: list[str]) -> None:
    """Run one stage from its arguments."""
    args = parser().parse_args(argv)
    HANDLERS[args.command](args)


def main() -> int:
    """Entry point; the exit code is the stage's."""
    dispatch(sys.argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
