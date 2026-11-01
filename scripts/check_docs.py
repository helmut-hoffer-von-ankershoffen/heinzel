# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Check what a machine can about the style of hand-written documentation and user-visible copy.

Rules, allowlist and baseline are explained in docs/dev/DOCS_CHECK.md. The check reads Markdown, the strings of
web/src/i18n.ts and the `description=` of settings fields; `--site DIR` instead scans a built site for raw pipes.
"""

import argparse
import ast
import fnmatch
import json
import re
import subprocess
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

type Tree = dict[str, "Tree"]

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = Path("docs/.docs-check-allow.toml")
BASELINE = Path("docs/.docs-check-baseline.txt")
ROOT_DOCUMENTS = ("README.md", "INTENT.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "PYPI.md")
GENERATED = (
    "docs/CLI_REFERENCE.md",
    "docs/MODULES.md",
    "docs/CONFIGURATION.md",
    "docs/plugins/*",
    "docs/catalog/*",
    "docs/_data/*",
    "docs/research/*",
)
I18N = "web/src/i18n.ts"
CLAIM_SCOPE = ("docs/README*.md", "docs/guides/*", "docs/VISION.md", "docs/CATALOG.md", I18N, "src/heinzel/*")
VERSION_SCOPE = ("docs/guides/*", "docs/README.md", "README.md")
TABLE_SCOPE = ("docs/guides/*", "README.md", "docs/README.md")
COPY_SCOPE = (I18N, "src/heinzel/*")
SHELL = {"sh", "bash", "shell", "zsh", "console"}
MAX_COLUMNS = 4
WARN_SENTENCES = 3
FAIL_SENTENCES = 5
HEX_COMMIT = re.compile(r"\b[0-9a-f]{40}\b")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w-]*)")
INLINE_CODE = re.compile(r"(`+)(.+?)\1")
LINK_TARGET = re.compile(r"(\]\()[^)\s]*")
HZ_COMMAND = re.compile(r"(?<![\w./$~-])hz((?:[ \t]+[^\s`|;&)<>]+)*)")
WORD = re.compile(r"[a-z][a-z0-9_-]*$")
ISO_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
AMENDMENT_NOTE = re.compile(r"Amended 20\d\d-\d\d-\d\d: ")
VERSION = re.compile(r"(?<![\w.])0\.\d+\.\d+(?![\w]|\.\d)")
BLOCK_START = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|>|<|!!!|\?\?\?|:::|\[[^\]]+\]:|\s{4})")
ABBREVIATION = re.compile(r"\b(e\.g|i\.e|etc|vs|cf|approx)\.", re.IGNORECASE)

# (rule, pattern, message, scope or None for everywhere)
PATTERNS: tuple[tuple[str, re.Pattern[str], str, tuple[str, ...] | None], ...] = (
    (
        "filler",
        re.compile(
            r"\b(?:simply|just(?![-\w])|seamless\w*|powerful|robust\w*|leverag(?:e|es|ed|ing)"
            r"|in order to|this section describes|as mentioned above|it is important to note)\b",
            re.IGNORECASE,
        ),
        "filler; say the thing, or cut it",
        None,
    ),
    (
        "claims",
        re.compile(r"\b(?:autonomous\w*|automatically|while you sleep|sends|publishes|delivers)\b", re.IGNORECASE),
        "product claim; name what hz does and who starts it",
        CLAIM_SCOPE,
    ),
    (
        "residue",
        re.compile(
            r"\b(?:retrofit\w*|migrat(?:ion|ions|ed)|decided by the maintainer|director['\u2019]s decision|scratchpad"
            r"|PR #|pull request #)",
            re.IGNORECASE,
        ),
        "process residue; documentation states the result, not how it came about",
        None,
    ),
)


@dataclass(frozen=True)
class Finding:
    """One violation, located by file and line."""

    path: str
    line: int
    rule: str
    message: str
    snippet: str = ""
    warning: bool = False

    def __str__(self) -> str:
        """The line printed for it."""
        kind = "warning" if self.warning else "error"
        return f"{self.path}:{self.line}: {kind}: [{self.rule}] {self.message}"


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    """Whether a repository-relative path matches any glob (`*` crosses directories)."""
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def scope_files(root: Path) -> list[Path]:
    """The hand-written Markdown documents the style rules apply to."""
    found = [root / name for name in ROOT_DOCUMENTS if (root / name).is_file()]
    docs = sorted((root / "docs").rglob("*.md")) if (root / "docs").is_dir() else []
    found += [p for p in docs if not matches(p.relative_to(root).as_posix(), GENERATED)]
    return found


def front_matter_lines(lines: list[str]) -> int:
    """How many leading lines are YAML front matter (0 without any)."""
    if not lines or lines[0].strip() != "---":
        return 0
    for number, line in enumerate(lines[1:], start=2):
        if line.strip() == "---":
            return number
    return 0


def fenced_blocks(lines: list[str]) -> list[tuple[int, int, str]]:
    """Fenced code blocks as (first content line, last content line, language), 1-based and inclusive."""
    blocks: list[tuple[int, int, str]] = []
    opener: tuple[str, int, str, int] | None = None
    for number, line in enumerate(lines, start=1):
        match = FENCE.match(line)
        if opener is None:
            if match:
                opener = (match[1][0], len(match[1]), match[2].lower(), number + 1)
        elif match and match[1][0] == opener[0] and len(match[1]) >= opener[1] and not match[2]:
            blocks.append((opener[3], number - 1, opener[2]))
            opener = None
    return blocks


class Document:
    """A Markdown file seen three ways: raw lines, prose without code, and its shell blocks."""

    def __init__(self, path: str, text: str) -> None:
        """Split the text into lines, prose and code blocks."""
        self.path = path
        self.lines = text.splitlines()
        self.front = front_matter_lines(self.lines)
        self.blocks = fenced_blocks(self.lines)
        hidden = {n for first, last, _ in self.blocks for n in range(first - 1, last + 2)} | set(
            range(1, self.front + 1)
        )
        self.prose = [
            "" if n in hidden else LINK_TARGET.sub(r"\1", INLINE_CODE.sub(lambda m: " " * len(m[0]), line))
            for n, line in enumerate(self.lines, start=1)
        ]
        self.body = ["" if n <= self.front else line for n, line in enumerate(self.lines, start=1)]

    def shell_blocks(self) -> list[tuple[int, list[str]]]:
        """Shell blocks as (first line number, lines)."""
        return [(first, self.lines[first - 1 : last]) for first, last, lang in self.blocks if lang in SHELL]


def pattern_findings(path: str, lines: list[str]) -> list[Finding]:
    """The filler, claim and residue words on the given lines."""
    found: list[Finding] = []
    for number, line in enumerate(lines, start=1):
        for rule, pattern, message, scope in PATTERNS:
            if scope is not None and not matches(path, scope):
                continue
            found += [Finding(path, number, rule, f"{message}: {m[0]!r}", m[0].lower()) for m in pattern.finditer(line)]
    return found


def date_and_version_findings(doc: Document) -> list[Finding]:
    """ISO dates in prose and semantic versions anywhere outside front matter.

    An ADR may carry a dated amendment note: a line that starts `Amended YYYY-MM-DD:` (ADR 0001).
    """
    found: list[Finding] = []
    adr = doc.path.startswith("docs/adr/")
    for number, (prose, body) in enumerate(zip(doc.prose, doc.body, strict=True), start=1):
        found += [
            Finding(doc.path, number, "date", f"date in prose: {m[0]}; documentation is not dated", m[0])
            for m in ISO_DATE.finditer(prose)
            if not (adr and number <= doc.front) and not (adr and AMENDMENT_NOTE.match(prose))
        ]
        if matches(doc.path, VERSION_SCOPE):
            found += [
                Finding(doc.path, number, "version", f"version number {m[0]} goes stale; link to the release", m[0])
                for m in VERSION.finditer(body)
            ]
    return found


def install_findings(doc: Document) -> list[Finding]:
    """Plugin install commands name the plugin, never a commit or a subdirectory."""
    found: list[Finding] = []
    for first, lines in doc.shell_blocks():
        if not any("plugin add" in line for line in lines):
            continue
        for offset, line in enumerate(lines):
            found.extend(
                Finding(doc.path, first + offset, "install", f"install command pins {m[0][:12]!r}", m[0])
                for m in [*HEX_COMMIT.finditer(line), *re.finditer(r"#subdirectory=", line)]
            )
    return found


def table_findings(doc: Document) -> list[Finding]:
    """Tables wider than four columns do not read on a phone."""
    found: list[Finding] = []
    for number, line in enumerate(doc.prose[:-1], start=1):
        following = doc.prose[number].strip()
        if line.strip().startswith("|") and re.fullmatch(r"\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?", following):
            columns = len(re.split(r"(?<!\\)\|", line.strip().strip("|")))
            if columns > MAX_COLUMNS:
                message = f"table with {columns} columns; more than {MAX_COLUMNS} do not fit a phone"
                found.append(Finding(doc.path, number, "table", message, warning=True))
    return found


def sentence_count(text: str) -> int:
    """Sentences in a paragraph, split on `. `, `? ` and `! `, leaving common abbreviations whole."""
    text = ABBREVIATION.sub(lambda m: m[0].replace(".", "\0"), " ".join(text.split()))
    return len([part for part in re.split(r"[.?!]+(?:\s+|$)", text) if part.strip()])


def paragraph_findings(doc: Document) -> list[Finding]:
    """Paragraphs of more than three sentences warn, more than five fail."""
    found: list[Finding] = []
    start = 0
    block: list[str] = []

    def close() -> None:
        if block and not BLOCK_START.match(block[0]) and not any(line.lstrip().startswith("|") for line in block):
            count = sentence_count(" ".join(block))
            if count > WARN_SENTENCES:
                fails = count > FAIL_SENTENCES
                limit = FAIL_SENTENCES if fails else WARN_SENTENCES
                message = f"paragraph of {count} sentences; more than {limit} is a wall of text"
                found.append(Finding(doc.path, start, "paragraph", message, warning=not fails))
        block.clear()

    for number, line in enumerate(doc.prose, start=1):
        if not line.strip() or line.lstrip().startswith(("#", "|")):
            close()
            continue
        if not block:
            start = number
        block.append(line)
    close()
    return found


def command_tree(root: Path) -> Tree:
    """The `hz` command tree: from `hz --command-tree`, else from the headings of docs/CLI_REFERENCE.md."""
    tree: Tree = {}

    def add(path: list[str]) -> None:
        node = tree
        for word in path:
            node = node.setdefault(word, {})

    try:
        out = subprocess.run(
            ["uv", "run", "--quiet", "hz", "--command-tree"],  # noqa: S607 - the project's own CLI
            cwd=root, capture_output=True, text=True, check=True, timeout=60,
        ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        reference = root / "docs/CLI_REFERENCE.md"
        for heading in re.findall(r"^#+ `hz( [^`]*)`", reference.read_text(encoding="utf-8"), re.MULTILINE):
            add(heading.split())
        return tree

    def walk(node: dict[str, Any]) -> None:
        add(node["command"].split()[1:])
        for child in node.get("commands", []):
            walk(child)

    walk(json.loads(out)["root"])
    return tree


def command_findings(doc: Document, tree: Tree) -> list[Finding]:
    """Every `hz <command>` in backticks or a shell block names a command that exists."""
    spans: list[tuple[int, str]] = []
    in_shell = {n for first, lines in doc.shell_blocks() for n in range(first, first + len(lines))}
    for number, line in enumerate(doc.lines, start=1):
        if number in in_shell:
            spans.append((number, re.sub(r"\s#\s.*$", "", line)))
        elif number > doc.front and not any(first - 1 <= number <= last + 1 for first, last, _ in doc.blocks):
            spans += [(number, m[2]) for m in INLINE_CODE.finditer(line)]
    found: list[Finding] = []
    for number, span in spans:
        for m in HZ_COMMAND.finditer(span):
            node, path = tree, ["hz"]
            for word in m[1].split():
                if not WORD.match(word):
                    break
                if word in node:
                    node = node[word]
                    path.append(word)
                elif node:
                    shown = " ".join([*path, word])
                    found.append(Finding(doc.path, number, "command", f"no such command: `{shown}`", shown))
                    break
                else:
                    break
    return found


def i18n_strings(text: str) -> list[tuple[int, str]]:
    """The values of the catalogue's `"key": "value"` pairs, with their line numbers."""
    pair = re.compile(r'"[\w.]+":\s*"((?:[^"\\]|\\.)*)"')
    return [(text.count("\n", 0, m.start(1)) + 1, m[1]) for m in pair.finditer(text)]


def setting_descriptions(path: Path) -> list[tuple[int, str]]:
    """The `description=` strings of fields declared on a settings class."""
    try:
        module = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    found: list[tuple[int, str]] = []
    for cls in (n for n in ast.walk(module) if isinstance(n, ast.ClassDef)):
        names = {b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in cls.bases}
        if not any(name.endswith("Settings") for name in names):
            continue
        for stmt in cls.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.value, ast.Call):
                found += [
                    (kw.value.lineno, kw.value.value)
                    for kw in stmt.value.keywords
                    if kw.arg == "description"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ]
    return found


def copy_findings(root: Path) -> list[Finding]:
    """Filler and claims in the web app's strings and the settings' help texts."""
    found: list[Finding] = []
    sources: list[tuple[str, list[tuple[int, str]]]] = []
    catalogue = root / I18N
    if catalogue.is_file():
        sources.append((I18N, i18n_strings(catalogue.read_text(encoding="utf-8"))))
    source = root / "src/heinzel"
    for path in sorted(source.rglob("*.py")) if source.is_dir() else []:
        text = path.read_text(encoding="utf-8")
        if "Settings" in text and "description=" in text:
            sources += [(path.relative_to(root).as_posix(), setting_descriptions(path))]
    for name, strings in sources:
        for number, string in strings:
            for rule, pattern, message, scope in PATTERNS:
                if rule == "residue" or (scope is not None and not matches(name, COPY_SCOPE)):
                    continue
                found += [
                    Finding(name, number, rule, f"{message}: {m[0]!r}", m[0].lower()) for m in pattern.finditer(string)
                ]
    return found


def load_allowlist(root: Path) -> list[dict[str, str]]:
    """The allowlist entries: `rule`, `path` (glob), optional `text`, and the `reason` each needs."""
    path = root / ALLOWLIST
    entries = tomllib.loads(path.read_text(encoding="utf-8")).get("allow", []) if path.is_file() else []
    for entry in entries:
        if not entry.get("reason") or not entry.get("rule") or not entry.get("path"):
            sys.exit(f"{ALLOWLIST}: every entry needs rule, path and reason: {entry}")
    return entries


def allowed(finding: Finding, entries: list[dict[str, str]]) -> bool:
    """Whether an allowlist entry covers the finding."""
    return any(
        entry["rule"] == finding.rule
        and fnmatch.fnmatchcase(finding.path, entry["path"])
        and entry.get("text", "").lower() in finding.snippet
        for entry in entries
    )


def load_baseline(root: Path) -> set[str]:
    """The tolerated `file:rule` pairs."""
    path = root / BASELINE
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    return {line.split("#")[0].strip() for line in lines if line.split("#")[0].strip()}


def collect(root: Path, tree: Tree | None = None, *, commands: bool = True) -> list[Finding]:
    """Every finding of every rule, before the allowlist and baseline."""
    if commands and tree is None:
        tree = command_tree(root)
    found: list[Finding] = []
    for file in scope_files(root):
        doc = Document(file.relative_to(root).as_posix(), file.read_text(encoding="utf-8"))
        found += pattern_findings(doc.path, doc.prose)
        found += date_and_version_findings(doc) + install_findings(doc) + paragraph_findings(doc)
        if commands and tree is not None:
            found += command_findings(doc, tree)
        if matches(doc.path, TABLE_SCOPE):
            found += table_findings(doc)
    return found + copy_findings(root)


class PipeScanner(HTMLParser):
    """Collects text outside code, preformatted text and tables that holds a line with two or more pipes."""

    IGNORED = frozenset({"code", "pre", "table", "script", "style", "head", "title", "template", "svg"})

    def __init__(self) -> None:
        """Start outside any ignored element."""
        super().__init__()
        self.depth = 0
        self.hits: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Enter an ignored element."""
        del attrs
        if tag in self.IGNORED:
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        """Leave an ignored element."""
        if tag in self.IGNORED and self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        """Record lines of visible text with two pipes: Markdown that did not become a table."""
        if self.depth:
            return
        first = self.getpos()[0]
        self.hits += [(first + n, line.strip()) for n, line in enumerate(data.split("\n")) if line.count("|") >= 2]  # noqa: PLR2004


def site_findings(site: Path) -> list[Finding]:
    """Raw table syntax in the rendered pages of a built site."""
    found: list[Finding] = []
    for page in sorted(site.rglob("*.html")):
        scanner = PipeScanner()
        scanner.feed(page.read_text(encoding="utf-8"))
        name = page.relative_to(site).as_posix()
        found += [
            Finding(name, n, "pipes", f"raw table syntax in rendered text: {t[:60]!r}", t) for n, t in scanner.hits
        ]
    return found


def report(findings: list[Finding], root: Path, *, strict: bool, verbose: bool, mode: str = "source") -> int:
    """Print the findings that count and return the exit status."""
    entries, baseline = load_allowlist(root), load_baseline(root)
    live = [f for f in findings if not allowed(f, entries)]
    errors = [f for f in live if not f.warning and f"{f.path}:{f.rule}" not in baseline]
    tolerated = [f for f in live if not f.warning and f"{f.path}:{f.rule}" in baseline]
    warnings = [f for f in live if f.warning]
    # Entries for rendered pages are judged only by a site scan, and command entries only when commands were checked.
    unjudged = {"site": None, "source": (".html:",), "fast": (".html:", ":command")}[mode]
    fired = {f"{f.path}:{f.rule}" for f in tolerated}
    stale = [] if unjudged is None else sorted(e for e in baseline - fired if not any(u in e for u in unjudged))
    for finding in errors:
        print(finding)
    if verbose:
        for finding in warnings:
            print(finding)
    elif warnings:
        counts = Counter(f.rule for f in warnings)
        print("warnings (--verbose lists them): " + ", ".join(f"{rule} {n}" for rule, n in sorted(counts.items())))
    for entry in stale:
        print(f"{BASELINE}: stale entry {entry!r} no longer fires; delete it")
    print(f"docs-check: {len(errors)} errors, {len(tolerated)} in the baseline, {len(warnings)} warnings")
    return 1 if errors or (strict and warnings) else 0


def main() -> int:
    """Run the check; the exit status is non-zero when it fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, help="scan the HTML pages of a built site for raw pipes instead")
    parser.add_argument("--strict", action="store_true", help="warnings fail too")
    parser.add_argument("--verbose", action="store_true", help="list every warning")
    parser.add_argument(
        "--no-commands",
        action="store_true",
        help="skip the stale command rule, which runs `hz --command-tree` (seconds)",
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this repository)")
    args = parser.parse_args()
    findings = site_findings(args.site) if args.site else collect(args.root, commands=not args.no_commands)
    mode = "site" if args.site else "fast" if args.no_commands else "source"
    return report(findings, args.root, strict=args.strict, verbose=args.verbose, mode=mode)


if __name__ == "__main__":
    sys.exit(main())
