# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Decide and stamp an edge release: what is releasable, its version, and the channel pointer (ADR 0114).

Usage:
  uv run --script scripts/release_channel.py releasable <changed-paths-file>
  uv run --script scripts/release_channel.py version <current> <suggested> <commit-count> [<newest-edge-version>]
  uv run --script scripts/release_channel.py stamp <version>
  uv run --script scripts/release_channel.py pointer <version> <commit> <wheel>
"""

import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FINAL = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
EDGE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.dev([1-9][0-9]*)")
COMMIT = re.compile(r"[0-9a-f]{40}")
TAG_PREFIX = "edge-v"
REPOSITORY = "helmut-hoffer-von-ankershoffen/heinzel"
NON_SHIPPING_DIRECTORIES = ("docs", "tests", ".github", ".claude")
NON_SHIPPING_FILES = ("mkdocs.yml",)
ARGUMENTS = {"releasable": 1, "version": (3, 4), "stamp": 1, "pointer": 3}


class ChannelError(ValueError):
    """The input is not a valid edge release."""


def releasable(paths: list[str]) -> bool:
    """Whether changes reach users; a path not known to be non-shipping counts as shipping."""
    return any(not _non_shipping(path) for path in paths)


def _non_shipping(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if len(parts) > 1:
        return parts[0] in NON_SHIPPING_DIRECTORIES
    return path in NON_SHIPPING_FILES or path.endswith(".md")


def _final(version: str) -> tuple[int, int, int]:
    match = FINAL.fullmatch(version)
    if match is None:
        msg = f"{version!r} is not a final version"
        raise ChannelError(msg)
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def edge_version(current: str, suggested: str, count: int, newest: str | None = None) -> str | None:
    """`<next>.dev<count>`, above the current stable and every earlier edge; None if a newer edge already exists.

    Raises:
        ChannelError: If a version or the commit count is invalid.
    """
    base, proposal = _final(current), _final(suggested)
    if count < 1:
        msg = "The commit count must be positive"
        raise ChannelError(msg)
    following = (base[0], base[1], base[2] + 1)
    release = max(following, proposal)
    if newest is not None:
        match = EDGE.fullmatch(newest)
        if match is None:
            msg = f"{newest!r} is not an edge version"
            raise ChannelError(msg)
        major, minor, patch, number = (int(part) for part in match.groups())
        if count <= number:
            return None
        release = max(release, (major, minor, patch))
    return f"{release[0]}.{release[1]}.{release[2]}.dev{count}"


def _replace_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, found = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if found != 1:
        msg = f"Expected exactly one core version in {path.name}"
        raise ChannelError(msg)
    path.write_text(updated, encoding="utf-8")


def stamp(version: str, root: Path = ROOT) -> None:
    """Set the core version in this build checkout only; nothing is committed.

    Raises:
        ChannelError: If `version` is no edge version, or the core version isn't found exactly once.
    """
    if EDGE.fullmatch(version) is None:
        msg = f"{version!r} is not an edge version"
        raise ChannelError(msg)
    pattern = r'^(name = "heinzel"\nversion = )"[^"]+"'
    _replace_once(root / "pyproject.toml", pattern, rf'\g<1>"{version}"')
    _replace_once(root / "uv.lock", pattern, rf'\g<1>"{version}"')


def pointer(version: str, commit: str, wheel: Path) -> dict[str, object]:
    """What the opt-in path reads to find the newest edge release; the digest binds the immutable wheel.

    Raises:
        ChannelError: If the version, commit or wheel filename is not this edge release.
    """
    if EDGE.fullmatch(version) is None or COMMIT.fullmatch(commit) is None:
        msg = "The pointer needs an edge version and a full commit"
        raise ChannelError(msg)
    filename = f"heinzel-{version}-py3-none-any.whl"
    if wheel.name != filename or wheel.is_symlink() or not wheel.is_file():
        msg = f"Expected the regular file {filename}"
        raise ChannelError(msg)
    tag = f"{TAG_PREFIX}{version}"
    return {
        "schema": 1,
        "channel": "edge",
        "version": version,
        "tag": tag,
        "commit": commit,
        "wheel": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{filename}",
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }


def main(argv: list[str]) -> int:
    """Run one command; a refusal is one line on standard error."""
    command, *rest = argv or [""]
    expected = ARGUMENTS.get(command)
    allowed = expected if isinstance(expected, tuple) else (expected,)
    if len(rest) not in allowed:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        if command == "releasable":
            paths = [line for line in Path(rest[0]).read_text(encoding="utf-8").splitlines() if line]
            print("true" if releasable(paths) else "false")
        elif command == "version":
            print(edge_version(rest[0], rest[1], int(rest[2]), rest[3] if len(rest) > 3 else None) or "")  # noqa: PLR2004
        elif command == "stamp":
            stamp(rest[0])
        else:
            print(json.dumps(pointer(rest[0], rest[1], Path(rest[2])), indent=2))
    except (ChannelError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
