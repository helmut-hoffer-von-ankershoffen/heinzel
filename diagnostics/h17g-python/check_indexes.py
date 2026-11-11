#!/usr/bin/env python3
"""Verify that every Packages index advertised by signed releases is present and trusted."""

import argparse
import re
from pathlib import Path

CHECKSUM_FIELDS = 3
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _source_scope(source: str) -> dict[str, set[str]]:
    scope: dict[str, set[str]] = {}
    text = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    for stanza in (part for part in text.split("\n\n") if part.strip()):
        fields = {key.lower(): value for key, value in (line.split(":", 1) for line in stanza.splitlines())}
        for suite in fields["suites"].split():
            scope.setdefault(suite, set()).update(fields["components"].split())
    return scope


def _advertised(release: Path, suite: str, components: set[str]) -> tuple[set[str], dict[str, tuple[str, str]]]:
    """Read signed Release checksum entries for the requested components.

    Raises:
        ValueError: The signed release has conflicting uncompressed entries.
    """
    section = False
    found: set[str] = set()
    raw_indexes: dict[str, tuple[str, str]] = {}
    for line in release.read_text().splitlines():
        if line == "SHA256:":
            section = True
            continue
        if section and not line.startswith(" "):
            break
        if section:
            fields = line.split()
            if len(fields) == CHECKSUM_FIELDS and re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
                match = re.fullmatch(r"([^/]+)/binary-amd64/Packages(\.(?:xz|gz|bz2|lz4|zst))?", fields[2])
                if match and match.group(1) in components:
                    component = match.group(1)
                    found.add(component)
                    if match.group(2) is None:
                        value = (fields[0].lower(), fields[1])
                        if component in raw_indexes and raw_indexes[component] != value:
                            message = f"Conflicting uncompressed Packages entries for {suite}/{component}"
                            raise ValueError(message)
                        raw_indexes[component] = value
    return found, raw_indexes


def expected_indexes(source: str, lists: Path) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Find every configured component's amd64 Packages index advertised in each signed pocket.

    Raises:
        ValueError: A signed pocket is missing, ambiguous or has no Packages indexes.
    """
    expected: set[tuple[str, str]] = set()
    empty: set[tuple[str, str]] = set()
    for suite, components in _source_scope(source).items():
        releases = list(lists.glob(f"*_dists_{suite}_InRelease"))
        if len(releases) != 1:
            message = f"Expected one signed InRelease for {suite}"
            raise ValueError(message)
        found, raw_indexes = _advertised(releases[0], suite, components)
        if not found:
            message = f"No configured amd64 Packages indexes advertised for {suite}"
            raise ValueError(message)
        for component in found:
            target = (suite, component)
            if raw_indexes.get(component) == (EMPTY_SHA256, "0"):
                empty.add(target)
            else:
                expected.add(target)
    return expected, empty


def check_targets(source: str, lists: Path, targets: str) -> tuple[int, int]:
    """Require APT's downloaded Packages targets to cover the signed indexes.

    Raises:
        ValueError: An expected target is missing, untrusted or outside the isolated lists directory.
    """
    expected, empty = expected_indexes(source, lists)
    observed: set[tuple[str, str]] = set()
    for line in targets.splitlines():
        suite, component, arch, trusted, filename = line.split("|", 4)
        if arch != "amd64":
            continue
        path = Path(filename)
        target = (suite, component)
        missing_allowed = target in empty and not path.exists() and not path.is_symlink()
        if (
            trusted.lower() != "yes"
            or not path.resolve().is_relative_to(lists.resolve())
            or (not path.is_file() and not missing_allowed)
        ):
            message = "Packages target is missing, untrusted or outside isolated lists"
            raise ValueError(message)
        observed.add(target)
    if not expected <= observed:
        message = f"Missing advertised Packages targets: {sorted(expected - observed)}"
        raise ValueError(message)
    return len(expected), len(empty)


def main() -> None:
    """Validate the signed source scope against APT's target inventory."""
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("lists", type=Path)
    parser.add_argument("targets", type=Path)
    args = parser.parse_args()
    count, empty = check_targets(args.source.read_text(), args.lists, args.targets.read_text())
    print(f"SIGNED_AMD64_PACKAGES_INDEXES_VERIFIED={count} SIGNED_EMPTY_INDEXES={empty}")  # noqa: T201 - diagnostic artifact


if __name__ == "__main__":
    main()
