# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Decide how much source review a change needs from the paths it touches: full, light or none.

A path's tier comes from the first rule that matches it, and a change takes the highest tier of its paths, so one
risky path in a large documentation change still makes the whole change full. A path nothing recognizes is full.
Deleted and renamed paths count on both sides: pass the old and the new name (or a `git diff --name-status` listing
with `--status`).

- full: CI and release workflows, the installer and updater, credentials and authentication, approvals and grants,
  release and publishing code, dependency and build files, agent instructions, and anything unrecognized.
- light: product, web, desktop and script code outside those areas.
- none: documentation text and images, tests, and Markdown outside agent-instruction directories.

Usage: uv run --script scripts/review_tier.py [--why] [--status | --json] [PATH ...]
Paths come from the arguments or, without any, from standard input (one per line; `--json`: one JSON string or list
of strings per line, as `gh api` prints them). Prints the tier; `--why` adds the paths that decided it on standard
error. Exit 2 for no paths or a malformed listing.
"""

import argparse
import fnmatch
import json
import sys
from collections.abc import Iterable
from typing import NamedTuple

TIERS = ("none", "light", "full")  # ordered: a change takes the highest of its paths


class Decision(NamedTuple):
    """The tier of one path and the rule that decided it."""

    tier: str
    rule: str


# Matched case-sensitively against the repository-relative path; `*` also crosses `/`. Git paths are case-sensitive,
# so a directory spelled `Tests/` is not `tests/` and must not inherit its tier.
PINNED_FULL = (
    ".github/*",
    "*/.github/*",
    "*.github[[]*",  # the starter's templated `.github[% endif %]` directory
    "*workflows/*",
    ".claude/*",
    "agent-skills/*",
    "docs/install.sh",
    "docs/_data/install*",
    "docs/_data/catalog.json",
    "docs/catalog-sources.toml",
    "docs/plugins/registry.toml",
    "scripts/source_review.py",
    "scripts/review.sh",
    "scripts/review_tier.py",
)
AGENT_FILES = ("AGENTS.md", "CLAUDE.md")  # instructions to agents: changing them changes behaviour, so never none
INERT_DOCS = (".md", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4", ".tape")
MARKDOWN_NEEDS_REVIEW = (".github/", ".claude/", "agent-skills/", "src/", "starter/")  # Markdown that is shipped or run
# Matched case-insensitively (the path is lowered first), anywhere in the path: the names of the risk areas.
RISK_NAMES = (
    "*install*",
    "*update*",
    "*bootstrap*",
    "*credential*",
    "*secret*",
    "*token*",
    "*keychain*",
    "*oauth*",
    "*auth*",
    "*access*",
    "*approv*",
    "*grant*",
    "*admission*",
    "*pairing*",
    "*attest*",
    "*verifier*",
    "*exposure*",
    "*identity*",
    "*policy*",
    "*budget*",
    "*release*",
    "*publish*",
    "*pypi*",
    "*review*",
)
RISK_PATHS = (  # areas whose files carry no telling name
    "src/heinzel/modules/system/lifecycle/*",
    "src/heinzel/modules/system/daemon/*",
    "src/heinzel/modules/system/runner/*",
    "src/heinzel/modules/system/claude/*",
    "src/heinzel/modules/system/connector/*",
    "src/heinzel/modules/ai/access/*",
    "src/heinzel/heads/api/*",
    "src/heinzel/heads/mcp/*",
    "desktop/ci/*",
    "desktop/managed/*",
    "scripts/tools*",
    "scripts/pr.sh",
    "makefile",
    "*/makefile",
    "hatch_build.py",
    "copier.yml",
    "*pyproject.toml*",
    "*uv.lock",
    "*package.json",
    "*pnpm-lock.yaml",
)
LIGHT = (
    "src/heinzel/*",
    "web/src/*",
    "web/e2e/*",
    "desktop/src/*",
    "desktop/test/*",
    "desktop/e2e/*",
    "fuzz/*",
    "scripts/*.py",
    "scripts/*.sh",
    "template/*",
    "docs/assets/website/*",
    "docs/overrides/*",
    *(f"*{name}" for name in AGENT_FILES),
)


def normalize(path: str) -> str | None:
    """The path as Git spells it, or None when it cannot name a file inside the repository."""
    cleaned = path.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if not cleaned or cleaned.startswith("/") or any(not char.isprintable() for char in cleaned):
        return None
    if any(part in {"", ".", ".."} for part in cleaned.split("/")):
        return None
    return cleaned


def _matches(path: str, patterns: Iterable[str]) -> str | None:
    return next((pattern for pattern in patterns if fnmatch.fnmatchcase(path, pattern)), None)


def decide(path: str) -> Decision:  # noqa: PLR0911 - one early return per rule, in order
    """The tier of one path, and why."""
    clean = normalize(path)
    if clean is None:
        return Decision("full", "not a plain repository path")
    if rule := _matches(clean, PINNED_FULL):
        return Decision("full", f"pinned: {rule}")
    if clean.rsplit("/", 1)[-1] in AGENT_FILES:
        return Decision("light", "agent instructions")
    if clean.startswith("tests/"):
        return Decision("none", "tests")
    if clean.startswith("docs/") and clean.endswith(INERT_DOCS):
        return Decision("none", "documentation")
    if clean.endswith(".md") and not clean.startswith(MARKDOWN_NEEDS_REVIEW):
        return Decision("none", "Markdown")
    if rule := _matches(clean.lower(), (*RISK_NAMES, *RISK_PATHS)):
        return Decision("full", f"risk area: {rule}")
    if rule := _matches(clean, LIGHT):
        return Decision("light", f"code: {rule}")
    return Decision("full", "unrecognized")


def tier_of(paths: Iterable[str]) -> str:
    """The tier of a change; an empty change is full, because nothing was looked at."""
    return max((decide(path).tier for path in paths), key=TIERS.index, default="full")


def deciding(paths: Iterable[str]) -> list[tuple[str, Decision]]:
    """The paths whose tier is the change's tier."""
    decisions = [(path, decide(path)) for path in paths]
    top = tier_of(paths)
    return [(path, decision) for path, decision in decisions if decision.tier == top]


def parse_status(text: str) -> list[str]:
    """Paths from `git diff --name-status` lines: a deletion or rename lists its old name too.

    Raises:
        ValueError: If a line has no status or no path.
    """
    paths: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        status, *names = line.split("\t")
        if not status.strip() or not names or not all(names):
            msg = f"not a name-status line: {line!r}"
            raise ValueError(msg)
        paths.extend(names)
    return paths


def parse_json_lines(text: str) -> list[str]:
    """Paths from lines holding a JSON string or a list of strings.

    Raises:
        ValueError: If a line is neither.
    """
    paths: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        names = [value] if isinstance(value, str) else value
        if not isinstance(names, list) or not all(isinstance(name, str) and name for name in names):
            msg = f"not a path or a list of paths: {line!r}"
            raise ValueError(msg)
        paths.extend(names)
    return paths


def main(argv: list[str] | None = None) -> int:
    """Print the tier of the given paths."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--why", action="store_true", help="also print the deciding paths to standard error")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="read `git diff --name-status` lines")
    mode.add_argument("--json", action="store_true", help="read JSON strings or lists of strings, one per line")
    args = parser.parse_args(argv)
    try:
        if args.paths:
            paths = args.paths
        else:
            text = sys.stdin.read()
            if args.status:
                paths = parse_status(text)
            elif args.json:
                paths = parse_json_lines(text)
            else:
                paths = [line for line in text.split("\n") if line]
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if not paths:
        print("error: no paths", file=sys.stderr)
        return 2
    print(tier_of(paths))
    if args.why:
        for path, decision in deciding(paths)[:10]:
            print(f"  {decision.tier}: {path} ({decision.rule})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
