# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Expand combine commits in one release section of CHANGELOG.md into one entry per member pull request.

A combine pull request lands several member pull requests as one squash commit. git-cliff sees only its subject
("feat(web): combine of 7 web changes (#679)"), so the changelog would not say what was released. The squash body
lists the members, one line each, as `hz-combine` writes them:

    Combines #659 feat(web): links standard, no text links in chrome

A line can wrap (GitHub and editors wrap long bodies): the lines that follow, up to a blank line or the next
`Combines`, continue the title. This replaces each combine entry with the members' entries, each under the group of
its own conventional type, linking the member pull request and the combine as `([#659](...), via [#679](...))`.
An introduction that only says "combine of" is rewritten from the members' feature and fix titles.

Usage: uv run --script scripts/changelog_combines.py CHANGELOG.md --version X.Y.Z [--repo DIR]
"""

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MEMBER = re.compile(r"^Combines #(?P<number>\d+) (?P<subject>\S.*)$")
SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?: (?P<title>\S.*?)(?: \(#\d+\))?$")
ENTRY = re.compile(r"^- .*\(\[[0-9a-f]{7}\]\((?P<url>(?P<repo>[^)]*)/commit/(?P<sha>[0-9a-f]{7,40}))\)\)\s*$")
COMBINE_PR = re.compile(r"\[#(\d+)\]\(")
INTRO_SAYS_COMBINE = re.compile(r"\bcombine of\b", re.IGNORECASE)
# The groups of git-cliff's commit_parsers in pyproject.toml, in their order.
GROUPS = {
    "breaking": "💥 Breaking changes",
    "security": "🛡️ Security",
    "feat": "✨ Features",
    "fix": "🐛 Bug fixes",
    "perf": "⚡ Performance",
    "docs": "📚 Documentation",
    "doc": "📚 Documentation",
    "refactor": "♻️ Refactoring",
    "test": "🧪 Testing",
    "build": "🏗️ Build and CI",
    "ci": "🏗️ Build and CI",
    "revert": "⏪ Reverts",
    "chore": "🧹 Miscellaneous",
    "style": "🧹 Miscellaneous",
}
ORDER = list(dict.fromkeys(GROUPS.values()))
INTRO_ITEMS = 4


@dataclass(frozen=True)
class Member:
    """One member pull request of a combine, from its `Combines #N type(scope): title` line."""

    number: int
    type: str
    scope: str | None
    breaking: bool
    title: str

    @property
    def group(self) -> str:
        """The changelog group git-cliff would have put the member's own commit in."""
        if self.breaking:
            return GROUPS["breaking"]
        if self.scope == "security":
            return GROUPS["security"]
        return GROUPS.get(self.type, GROUPS["chore"])


def members(body: str) -> list[Member]:
    """The members a combine commit's body lists; lines that are no conventional subject are skipped."""
    found: list[Member] = []
    pending: list[str] | None = None

    def flush() -> None:
        if pending is None:
            return
        number, subject = pending[0], " ".join(pending[1:])
        if match := SUBJECT.match(subject):
            found.append(
                Member(int(number), match["type"], match["scope"], bool(match["breaking"]), match["title"].strip())
            )

    for raw in body.splitlines():
        line = raw.strip()
        if match := MEMBER.match(line):
            flush()
            pending = [match["number"], match["subject"].strip()]
        elif pending is not None and line and not line.startswith(("<!--", "Combines")):
            pending.append(line)
        else:
            flush()
            pending = None
    flush()
    return found


def entry(member: Member, combine: str | None, commit_url: str, sha: str) -> str:
    """One changelog entry for a member, linking its pull request and the combine that landed it."""
    pull = commit_url.rsplit("/commit/", 1)[0] + "/pull/"
    scope = f"**{member.scope}:** " if member.scope else ""
    via = f", via [#{combine}]({pull}{combine})" if combine else ""
    title = member.title[:1].upper() + member.title[1:]
    return f"- {scope}{title} ([#{member.number}]({pull}{member.number}){via}) ([{sha[:7]}]({commit_url}))"


def intro(combines: list[list[Member]]) -> str:
    """A sentence naming the first features, then fixes, taking turns between the combines.

    For example "Links standard; install the plugins a plugin requires; ...; and 8 more.".
    """
    found = [m for members_of in combines for m in members_of]
    ranked = [
        m
        for kind in ("feat", "fix")
        for row in range(max(map(len, combines), default=0))
        for members_of in combines
        if row < len(members_of) and (m := members_of[row]).type == kind
    ]
    ranked = ranked or found
    titles = [m.title for m in ranked[:INTRO_ITEMS]]
    if not titles:
        return ""
    text = "; ".join(titles)
    text = text[:1].upper() + text[1:]
    rest = len(found) - len(titles)
    return f"{text}; and {rest} more." if rest > 0 else f"{text}."


def expand(changelog: str, version: str, body_of: Callable[[str], str]) -> str:
    """`changelog` with the combine entries of `version`'s section expanded; other sections are kept byte for byte.

    Raises:
        ValueError: If the changelog has no section for `version`.
    """
    version = version.removeprefix("v")
    heading = re.search(rf"(?m)^## \[{re.escape(version)}\].*\n", changelog)
    if heading is None:
        msg = f"CHANGELOG.md has no section for {version}"
        raise ValueError(msg)
    end_match = re.compile(r"(?m)^## \[").search(changelog, heading.end())
    end = end_match.start() if end_match else len(changelog)
    section = changelog[heading.end() : end]
    lines = section.split("\n")
    first = next((i for i, line in enumerate(lines) if line.startswith("### ")), len(lines))
    text = "\n".join(lines[:first]).strip()
    groups: dict[str, list[str]] = {}
    current = None
    expanded: list[list[Member]] = []
    for line in lines[first:]:
        if line.startswith("### "):
            current = line[4:].strip()
            groups.setdefault(current, [])
            continue
        if not line.strip() or current is None:
            continue
        match = ENTRY.match(line)
        found = members(body_of(match["sha"])) if match else []
        if not found:
            groups[current].append(line)
            continue
        # git-cliff's preprocessor turned the squash suffix "(#679)" into "([#679](...))": the combine's own number.
        combine = COMBINE_PR.search(line)
        pr = combine.group(1) if combine else None
        for member in found:
            groups.setdefault(member.group, []).append(entry(member, pr, match["url"], match["sha"]))
        expanded.append(found)
    if not expanded:
        return changelog
    if not text or INTRO_SAYS_COMBINE.search(text):
        text = intro(expanded)
    names = [g for g in ORDER if groups.get(g)] + [g for g in groups if g not in ORDER and groups[g]]
    parts = [text] if text else []
    parts += ["### " + name + "\n\n" + "\n".join(groups[name]) for name in names]
    tail = "\n" if end_match else ""
    rebuilt = "\n" + "\n\n".join(parts) + "\n" + tail
    return changelog[: heading.end()] + rebuilt + changelog[end:]


def git_body(repo: Path) -> Callable[[str], str]:
    """Read a commit's full message with git in `repo`."""

    def body(sha: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "log", "-1", "--format=%B", sha],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    return body


def main(argv: list[str] | None = None) -> int:
    """Rewrite the changelog in place."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("changelog", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--repo", type=Path, default=Path())
    args = parser.parse_args(argv)
    try:
        text = expand(args.changelog.read_text(encoding="utf-8"), args.version, git_body(args.repo))
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(exc, file=sys.stderr)
        return 1
    args.changelog.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
