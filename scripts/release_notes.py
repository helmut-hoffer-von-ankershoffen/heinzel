# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Print one release's section of CHANGELOG.md, for the GitHub release notes.

The notes come from the changelog as merged in the release pull request, so edits made during review are published.
Changes that merged after the changelog was written (during the candidate) are in the tag but not in that section, so
every commit between the previous tag and this tag that the section does not mention is listed under "Also in this
release". The tag is the truth, not the release pull request's view.

Usage: uv run --script scripts/release_notes.py v1.2.3 [CHANGELOG.md]
"""

import re
import subprocess
import sys
from pathlib import Path


def section(changelog: str, version: str) -> str:
    """The body of `## [version]` up to the next release heading.

    Raises:
        ValueError: If the changelog has no section for `version`.
    """
    number = re.escape(version.removeprefix("v"))
    match = re.search(rf"^## \[{number}\].*?$(.*?)(?=^## \[|\Z)", changelog, flags=re.MULTILINE | re.DOTALL)
    if match is None:
        msg = f"CHANGELOG.md has no section for {version}"
        raise ValueError(msg)
    return match.group(1).strip() + "\n"


def missing(notes: str, subjects: list[str]) -> list[str]:
    """The commit subjects the notes do not mention, by pull request number `(#N)` or else by the subject text."""
    out = []
    for subject in subjects:
        if subject.startswith("chore(release)"):
            continue
        pulls = re.findall(r"\(#(\d+)\)\s*$", subject)
        seen = bool(pulls) and re.search(rf"#{pulls[0]}\b", notes) is not None
        if not seen and subject not in notes:
            out.append(subject)
    return out


def tag_subjects(tag: str) -> list[str]:
    """Subjects of every commit in `previous tag..tag`, newest first; the previous is the nearest earlier tag."""
    previous = subprocess.run(  # noqa: S603 - fixed git argv, the tag comes from the workflow's own ref
        ["git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", "--exclude", "*-*", f"{tag}^"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if previous.returncode != 0:
        return []
    log = subprocess.run(  # noqa: S603
        ["git", "log", "--format=%s", f"{previous.stdout.strip()}..{tag}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return [line for line in log.stdout.splitlines() if line.strip()]


def with_missing(notes: str, subjects: list[str]) -> str:
    """The notes plus a list of the commits of the tag they do not mention (unchanged when none)."""
    extra = missing(notes, subjects)
    if not extra:
        return notes
    return notes + "\n### Also in this release\n\n" + "".join(f"- {subject}\n" for subject in extra)


def main() -> int:
    """Print the section, or explain why it is missing."""
    version, path = sys.argv[1], Path(sys.argv[2] if len(sys.argv) > 2 else "CHANGELOG.md")  # noqa: PLR2004
    try:
        print(with_missing(section(path.read_text(encoding="utf-8"), version), tag_subjects(version)), end="")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
