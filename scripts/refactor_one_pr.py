# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Move whole files into the hz target module map and rewrite what points at them; prove the move by tool.

The map is `scripts/refactor_one_pr_map.toml`: one `[[move]]` per file (old path, new path, stage) and, for the few
mixed `from PKG import a, b` lines that cannot be rewritten by swapping a package, one `[[edit]]`. Nothing else is
decided here: a file keeps its name and its bytes, only an old path, an old dotted module name or an old import
specifier inside any tracked text file is replaced by its new one.

    refactor_one_pr.py apply STAGE        git mv the stage's files and rewrite references (python, then web)
    refactor_one_pr.py check STAGE BASE HEAD
                                          P2: HEAD differs from BASE only by renames in the map and replaced tokens
    refactor_one_pr.py repro STAGE BASE HEAD
                                          P1: apply on a checkout of BASE yields exactly the tree of HEAD

For a branch that still holds the old layout: run `apply python` and then `apply web` on it. The script stays in the
repository for seven days after the merge and is then deleted.
"""

from __future__ import annotations

import argparse
import ast
import posixpath
import re
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

MAP = "scripts/refactor_one_pr_map.toml"
SKIP_REWRITE = {MAP, "scripts/refactor_one_pr.py", "CHANGELOG.md", "uv.lock", "web/package-lock.json"}
# recordings of old releases keep their old paths
SKIP_DIRS = ("web/node_modules/", "apple/", "desktop/node_modules/", "tests/fixtures/web-launcher-history/")
SOURCE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts")
RESOLVE_SUFFIXES = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".css", ".json", "/index.ts", "/index.tsx", "/index.js")
FROM_LINE = re.compile(r"^\s*from (?P<pkg>[\w.]+) import (?P<names>[\w, ]+?)\s*$")
SPEC = re.compile(r"""(?P<q>["'`])(?P<spec>(?:\./|\.\./|@/)[^"'`\n]*)(?P=q)""")


@dataclass(frozen=True)
class Edit:
    """One exact text replacement in a file that a package swap cannot express."""

    file: str
    old: str
    new: str


@dataclass(frozen=True)
class Plan:
    """The moves and edits of one stage."""

    moves: dict[str, str]
    edits: list[Edit]

    def dotted(self) -> dict[str, str]:
        """Old dotted module name to new, for moved Python files under `src/` and `tests/`."""
        return {_dotted(old): _dotted(new) for old, new in self.moves.items() if _is_module(old)}


def _is_module(path: str) -> bool:
    return path.startswith(("src/heinzel/", "tests/")) and path.endswith(".py")


def _dotted(path: str) -> str:
    return path.removeprefix("src/").removesuffix(".py").replace("/", ".")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, timeout=600).stdout  # noqa: S603, S607


def load(root: Path, stage: str) -> Plan:
    """Read the map and keep the entries of one stage."""
    data = tomllib.loads((root / MAP).read_text(encoding="utf-8"))
    moves = {m["old"]: m["new"] for m in data["move"] if m["stage"] == stage}
    edits = [Edit(e["file"], e["old"], e["new"]) for e in data.get("edit", []) if e["stage"] == stage]
    return Plan(moves, edits)


def _tracked(root: Path) -> list[str]:
    return [p for p in _git(root, "ls-files").splitlines() if not p.startswith(SKIP_DIRS) and p not in SKIP_REWRITE]


def _read(root: Path, rel: str) -> str | None:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return None


def _token_pattern(tokens: list[str], tail: str) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))
    return re.compile(rf"(?<![\w./-])(?:{alternatives}){tail}")


def _path_variants(old: str, new: str) -> dict[str, str]:
    """Spellings of a path inside text: from the repository root, from `src/`, and for web from `web/`."""
    variants = {old: new}
    for prefix in ("src/", "web/"):
        if old.startswith(prefix) and new.startswith(prefix):
            variants[old.removeprefix(prefix)] = new.removeprefix(prefix)
    if old.startswith("src/heinzel/") and new.startswith("src/heinzel/"):
        variants[old.removeprefix("src/heinzel/")] = new.removeprefix("src/heinzel/")
    if old.startswith("web/src/"):
        variants[old.removeprefix("web/src/")] = new.removeprefix("web/src/")
    return variants


class Rewriter:
    """Applies one plan's token replacements to the text of one file."""

    def __init__(self, plan: Plan, files: set[str]) -> None:
        """Index the plan's names once."""
        self.plan = plan
        self.files = files
        self.dotted = plan.dotted()
        self._dotted_re = _token_pattern(list(self.dotted), r"(?!\w)") if self.dotted else None
        variants: dict[str, str] = {}
        for old, new in plan.moves.items():
            variants.update(_path_variants(old, new))
        self.paths = variants
        self._path_re = _token_pattern(list(variants), r"(?![\w])") if variants else None
        self.packages: dict[str, dict[str, str]] = {}
        for old, new in self.dotted.items():
            old_pkg, leaf = old.rsplit(".", 1)
            self.packages.setdefault(old_pkg, {})[leaf] = new.rsplit(".", 1)[0]

    def python(self, text: str) -> str:
        """Swap dotted names, `from PKG import moved` headers and path strings in one Python file."""
        lines = text.split("\n")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if not (isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in self.packages):
                    continue
                leaves = self.packages[node.module]
                names = [alias.name for alias in node.names]
                targets = {leaves[n] for n in names if n in leaves}
                if not targets:
                    continue
                if len(targets) != 1 or any(n not in leaves for n in names):
                    continue  # mixed: an explicit [[edit]] must cover it; `apply` fails if none does
                head = lines[node.lineno - 1]
                lines[node.lineno - 1] = head.replace(f"from {node.module} import", f"from {targets.pop()} import", 1)
        return self.tokens("\n".join(self._text_imports(lines)))

    def _text_imports(self, lines: list[str]) -> list[str]:
        """Single-line `from PKG import moved` inside strings (a script a test writes) has no AST node."""
        for index, line in enumerate(lines):
            match = FROM_LINE.match(line)
            if match and match.group("pkg") in self.packages:
                leaves = self.packages[match.group("pkg")]
                names = [part.split(" as ")[0].strip() for part in match.group("names").split(",")]
                targets = {leaves[n] for n in names if n in leaves}
                if len(targets) == 1 and all(n in leaves for n in names):
                    lines[index] = line.replace(f"from {match.group('pkg')} import", f"from {targets.pop()} import", 1)
        return lines

    def tokens(self, text: str) -> str:
        """Replace old dotted names and old paths by their new spelling."""
        if self._dotted_re:
            text = self._dotted_re.sub(lambda m: self.dotted[m.group(0)], text)
        if self._path_re:
            text = self._path_re.sub(lambda m: self.paths[m.group(0)], text)
        return text

    def resolve(self, importer: str, spec: str) -> tuple[str, str] | None:
        """The tracked file an import specifier names, and the suffix that completed it."""
        base = (
            posixpath.normpath("web/src/" + spec[2:])
            if spec.startswith("@/")
            else posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
        )
        for suffix in RESOLVE_SUFFIXES:
            if base + suffix in self.files:
                return base + suffix, suffix
        return None

    def specifiers(self, text: str, old_importer: str, new_importer: str) -> str:
        """Recompute relative and `@/` import specifiers whose importer or target moved."""

        def replace(match: re.Match[str]) -> str:
            spec = match.group("spec")
            found = self.resolve(old_importer, spec)
            if found is None:
                return match.group(0)
            target, suffix = found
            new_target = self.plan.moves.get(target, target)
            if new_target == target and old_importer == new_importer:
                return match.group(0)
            stem = new_target.removesuffix(suffix) if suffix else new_target
            if spec.startswith("@/"):
                if new_target == target:
                    return match.group(0)
                new_spec = "@/" + stem.removeprefix("web/src/")
            else:
                new_spec = posixpath.relpath(stem, posixpath.dirname(new_importer))
                if not new_spec.startswith("."):
                    new_spec = "./" + new_spec
            return f"{match.group('q')}{new_spec}{match.group('q')}"

        return SPEC.sub(replace, text)


def _normalise_web(root: Path, paths: list[str]) -> None:
    """Let the repository's own biome re-sort the imports and re-wrap the lines that the moves changed."""
    if paths:
        relative = [p.removeprefix("web/") for p in paths]
        command = ["pnpm", "exec", "biome", "check", "--write", "--linter-enabled=false", *relative]
        subprocess.run(command, cwd=root / "web", check=True, timeout=600)  # noqa: S603


def _normalise(root: Path, paths: list[str]) -> None:
    """Let the repository's own ruff re-sort imports and re-wrap lines the longer names pushed over the limit."""
    if not paths:
        return
    for args in (["check", "--fix", "--exit-zero", "-q"], ["format", "-q"]):
        subprocess.run(["uv", "run", "--frozen", "ruff", *args, *paths], cwd=root, check=True, timeout=600)  # noqa: S603, S607


def apply(root: Path, stage: str) -> int:
    """Move the stage's files and rewrite every tracked text file that points at them."""
    plan = load(root, stage)
    old_names = {new: old for old, new in plan.moves.items()}
    for old, new in plan.moves.items():
        (root / new).parent.mkdir(parents=True, exist_ok=True)
        _git(root, "mv", old, new)
    files = set(_tracked(root))
    # Specifiers resolve against the OLD tree: a moved target is looked up under its old name.
    old_files = {old_names.get(f, f) for f in files}
    rewriter = Rewriter(plan, old_files)
    changed = 0
    written: set[str] = set()
    for rel in sorted(files):
        text = _read(root, rel)
        if text is None:
            continue
        old_rel = old_names.get(rel, rel)
        new_text = text
        for edit in plan.edits:
            if edit.file == old_rel and edit.old in new_text:
                new_text = new_text.replace(edit.old, edit.new)
        new_text = rewriter.python(new_text) if rel.endswith(".py") else rewriter.tokens(new_text)
        if rel.endswith(SOURCE_EXTENSIONS):
            new_text = rewriter.specifiers(new_text, old_rel, rel)
        if new_text != text:
            (root / rel).write_text(new_text, encoding="utf-8")
            written.add(rel)
            changed += 1
    _normalise(root, sorted(rel for rel in written if rel.endswith(".py")))
    _normalise_web(root, sorted(rel for rel in written if rel.startswith("web/") and rel.endswith(SOURCE_EXTENSIONS)))
    sys.stdout.write(f"{stage}: moved {len(plan.moves)} files, rewrote {changed}\n")
    return 0


def _blob(root: Path, rev: str, path: str) -> str | None:
    try:
        return _git(root, "show", f"{rev}:{path}")
    except subprocess.CalledProcessError:
        return None


def _mask(rewriter: Rewriter, line: str) -> str:
    """The line with every old or new dotted name, path and package header blanked, to compare what is left."""
    reverse_dotted = {**rewriter.dotted, **{v: v for v in rewriter.dotted.values()}}
    probe = _token_pattern(list(reverse_dotted), r"(?!\w)") if reverse_dotted else None
    out = probe.sub("\0", line) if probe else line
    paths = {**rewriter.paths, **{v: v for v in rewriter.paths.values()}}
    if paths:
        out = _token_pattern(list(paths), r"(?![\w])").sub("\0", out)
    packages = set(rewriter.packages) | {p for leaves in rewriter.packages.values() for p in leaves.values()}
    for pkg in packages:
        out = out.replace(f"from {pkg} import", "from \0 import")
    return SPEC.sub('"\0"', out)


def _resolved(rewriter: Rewriter, spec: str, importer: str, tree: str) -> str:
    """The file an import specifier names, in the old tree's names (the new tree's names mapped back)."""
    reverse = {new: old for old, new in rewriter.plan.moves.items()}
    base = (
        posixpath.normpath("web/src/" + spec[2:])
        if spec.startswith("@/")
        else posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    )
    for suffix in RESOLVE_SUFFIXES:
        candidate = base + suffix
        exists = candidate in reverse or (candidate in rewriter.files and candidate not in rewriter.plan.moves)
        if exists if tree == "new" else candidate in rewriter.files:
            return reverse.get(candidate, candidate)
    return f"unresolved:{spec}"


def _ts_canon(rewriter: Rewriter, text: str, importer: str, tree: str) -> tuple[tuple[tuple[str, ...], ...], str]:
    """A TypeScript file as its imports (specifiers resolved, names unordered) and the rest, without layout."""
    text = SPEC.sub(lambda m: f'"{_resolved(rewriter, m.group("spec"), importer, tree)}"', text)
    statements = re.findall(r"^import\b.*?;[ \t]*$", text, flags=re.MULTILINE | re.DOTALL)
    rest = re.sub(r"^import\b.*?;[ \t]*$", "", text, flags=re.MULTILINE | re.DOTALL)
    keys = tuple(sorted(tuple(sorted(re.findall(r"[\w$*@./:-]+", _mask(rewriter, st)))) for st in statements))
    return keys, re.sub(r"[\s(),]", "", _mask(rewriter, rest))


def _import_keys(rewriter: Rewriter, text: str) -> tuple[set[tuple[str, str]], str] | None:
    """A Python file as its imports (a moved module's package blanked) and the rest, free of layout."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    moved_leaves = {old.rsplit(".", 1)[1] for old in rewriter.dotted}
    keys: set[tuple[str, str]] = set()
    lines = text.split("\n")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in rewriter.dotted or module in rewriter.dotted.values():
                module = "\0"
            keys |= {("\0" if a.name in moved_leaves else module, a.name) for a in node.names}
        elif isinstance(node, ast.Import):
            keys |= {(_mask(rewriter, a.name), "") for a in node.names}
        else:
            continue
        for number in range(node.lineno - 1, (node.end_lineno or node.lineno)):
            lines[number] = ""
    rest = re.sub(r"[\s(),]", "", _mask(rewriter, "\n".join(lines)))
    return keys, rest


def _file_problems(root: Path, rewriter: Rewriter, base: str, head: str, old: str) -> list[str]:
    """The lines of one file that changed by more than a mapped token (and, in Python, ruff's re-layout)."""
    plan = rewriter.plan
    new = plan.moves.get(old, old)
    before, after = _blob(root, base, old), _blob(root, head, new)
    if before is None or after is None or "\0" in before:
        return []
    for edit in plan.edits:
        if edit.file == old:
            before = before.replace(edit.old, edit.new)
    if new.endswith(".py") and (left := _import_keys(rewriter, before)) and (right := _import_keys(rewriter, after)):
        return [] if left == right else [f"{new}: imports or code differ by more than moved names and layout"]
    if new.endswith(SOURCE_EXTENSIONS):
        same = _ts_canon(rewriter, before, old, "old") == _ts_canon(rewriter, after, new, "new")
        return [] if same else [f"{new}: imports or code differ by more than moved names and layout"]
    old_lines, new_lines = before.split("\n"), after.split("\n")
    if len(old_lines) != len(new_lines):
        return [f"{new}: line count {len(old_lines)} -> {len(new_lines)}"]
    return [
        f"{new}:{number}: {a.strip()!r} -> {b.strip()!r}"
        for number, (a, b) in enumerate(zip(old_lines, new_lines, strict=True), 1)
        if a != b and _mask(rewriter, a) != _mask(rewriter, b)
    ]


def check(root: Path, stage: str, base: str, head: str) -> int:
    """P2: the diff is renames of the map plus replaced tokens, and nothing else."""
    plan = load(root, stage)
    files = set(_git(root, "ls-tree", "-r", "--name-only", base).splitlines())
    rewriter = Rewriter(plan, files)
    problems: list[str] = []
    status = [line.split("\t") for line in _git(root, "diff", "-M", "--name-status", base, head).splitlines()]
    renames = {}
    for fields in status:
        kind = fields[0][0]
        if kind == "R":
            renames[fields[1]] = fields[2]
        elif kind != "M":
            problems.append(f"not a rename or an edit: {' '.join(fields)}")
    if renames != plan.moves:
        problems.append(f"renames differ from the map: {sorted(set(renames.items()) ^ set(plan.moves.items()))[:5]}")
    modified = {f[1] for f in status if f[0] == "M"} | set(plan.moves)
    for old in sorted(modified):
        problems += _file_problems(root, rewriter, base, head, old)
    for problem in problems[:40]:
        sys.stderr.write(problem + "\n")
    sys.stdout.write(
        f"{stage}: {len(renames)} renames, {len(modified) - len(plan.moves)} edited files, {len(problems)} problems\n"
    )
    return 1 if problems else 0


def repro(root: Path, stage: str, base: str, head: str) -> int:
    """P1: apply on a checkout of BASE yields exactly the tree of HEAD."""
    with tempfile.TemporaryDirectory(prefix="refactor-repro-") as scratch:
        work = Path(scratch) / "tree"
        _git(root, "worktree", "add", "--detach", str(work), base)
        try:
            if (root / "web/node_modules").is_dir():  # biome, for the web stage, runs from the installed packages
                (work / "web/node_modules").symlink_to(root / "web/node_modules")
            apply(work, stage)
            if (work / "web/node_modules").is_symlink():
                (work / "web/node_modules").unlink()
            _git(work, "add", "-A")
            mine, theirs = _git(work, "write-tree").strip(), _git(root, "rev-parse", f"{head}^{{tree}}").strip()
        finally:
            _git(root, "worktree", "remove", "--force", str(work))
    sys.stdout.write(f"{stage}: script tree {mine}, {head} tree {theirs}\n")
    return 0 if mine == theirs else 1


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["apply", "check", "repro"])
    parser.add_argument("stage", choices=["python", "web"])
    parser.add_argument("base", nargs="?")
    parser.add_argument("head", nargs="?")
    args = parser.parse_args()
    root = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    if args.action == "apply":
        return apply(root, args.stage)
    if not (args.base and args.head):
        parser.error("check and repro need BASE and HEAD")
    return (check if args.action == "check" else repro)(root, args.stage, args.base, args.head)


if __name__ == "__main__":
    raise SystemExit(main())
