# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Put the hand-written release introductions back into a CHANGELOG.md that git-cliff regenerated.

git-cliff writes each release's entries from the commits, so prose a maintainer wrote between a `## [x.y.z]` heading
and its first `### ` section would be lost on the next release. The release script saves the changelog, regenerates
it, and runs this to restore the published release sections verbatim, plus the new release's introduction from
`--intro`. Published history can include entries from commits that are no longer reachable from the release tag.
An introduction is plain prose: a line starting with `## ` or `### ` ends it.

Usage: uv run --script scripts/changelog_intros.py PREVIOUS CHANGELOG [--version X.Y.Z --intro TEXT]
"""

import argparse
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\]")
SECTION = ("## ", "### ")


def intros(changelog: str) -> dict[str, str]:
    """The introduction under each release heading that has one, by version (`0.2.0`, `Unreleased`)."""
    return {version: text for version, (text, _) in _blocks(changelog.splitlines(keepends=True)).items() if text}


def restore(changelog: str, kept: dict[str, str]) -> str:
    """`changelog` with the introductions in `kept` under their release headings, replacing what is there.

    A heading without an introduction is followed by one blank line; git-cliff's template writes two.
    """
    lines = changelog.splitlines(keepends=True)
    blocks = _blocks(lines)
    out: list[str] = []
    skip_to = 0
    for index, line in enumerate(lines):
        if index < skip_to:
            continue
        out.append(line)
        if match := HEADING.match(line):
            current, end = blocks[match["version"]]
            if intro := kept.get(match["version"], "").strip() or current:
                out.append(f"\n{intro}\n\n" if end < len(lines) else f"\n{intro}\n")
            elif end < len(lines):
                out.append("\n")
            skip_to = end
    return "".join(out)


def preserve_history(generated: str, previous: str, version: str) -> str:
    """Keep the new generated release and the previously published sections byte for byte.

    Raises:
        ValueError: If release headings are missing or ambiguous.
    """
    version = version.removeprefix("v")
    generated_headings = list(re.finditer(r"(?m)^## \[([^\]]+)\].*$", generated))
    previous_headings = list(re.finditer(r"(?m)^## \[([^\]]+)\].*$", previous))
    if not generated_headings or generated_headings[0].group(1) != version:
        msg = "generated changelog must begin with the new release"
        raise ValueError(msg)
    if sum(match.group(1) == version for match in generated_headings) != 1:
        msg = "new release heading is ambiguous"
        raise ValueError(msg)
    if any(match.group(1) == version for match in previous_headings):
        msg = "previous changelog contains the new release heading"
        raise ValueError(msg)
    if len({match.group(1) for match in previous_headings}) != len(previous_headings):
        msg = "previous changelog has duplicate release headings"
        raise ValueError(msg)
    if any(match.group(1) == "Unreleased" for match in previous_headings[1:]):
        msg = "unreleased heading must precede published releases"
        raise ValueError(msg)
    published = [match for match in previous_headings if match.group(1) != "Unreleased"]
    end = generated_headings[1].start() if len(generated_headings) > 1 else len(generated)
    new_release = generated[:end].rstrip("\n") + "\n"
    return new_release + "\n" + previous[published[0].start() :] if published else new_release


def _blocks(lines: list[str]) -> dict[str, tuple[str, int]]:
    """Per release heading: its introduction, and the index of the line that ends it."""
    blocks: dict[str, tuple[str, int]] = {}
    for index, line in enumerate(lines):
        if match := HEADING.match(line):
            end = index + 1
            while end < len(lines) and not lines[end].startswith(SECTION):
                end += 1
            blocks.setdefault(match["version"], ("".join(lines[index + 1 : end]).strip(), end))
    return blocks


def main(argv: list[str] | None = None) -> int:
    """Restore the introductions of PREVIOUS into CHANGELOG, in place."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("previous", type=Path, help="the changelog before git-cliff regenerated it")
    parser.add_argument("changelog", type=Path, help="the regenerated changelog, rewritten in place")
    parser.add_argument("--version", help="the version being released, e.g. 1.2.3")
    parser.add_argument("--intro", default="", help="the introduction of --version")
    args = parser.parse_args(argv)
    if args.intro.strip() and not args.version:
        parser.error("--intro needs --version")

    kept = intros(args.previous.read_text(encoding="utf-8")) if args.previous.is_file() else {}
    if args.intro.strip():
        kept[args.version.removeprefix("v")] = args.intro
    generated = restore(args.changelog.read_text(encoding="utf-8"), kept)
    if args.version and args.previous.is_file():
        try:
            generated = preserve_history(generated, args.previous.read_text(encoding="utf-8"), args.version)
        except ValueError as exc:
            parser.error(str(exc))
    args.changelog.write_text(generated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
