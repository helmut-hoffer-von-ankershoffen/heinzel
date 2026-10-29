# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Finalize a version already used by candidate SDK artifacts without downgrading their public version."""

import re
import sys

FINAL = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def latest_release(tags: str) -> str | None:
    """Select the highest canonical final core release, independent of Git's graph distance."""
    versions = {
        tag[1:]: tuple(int(part) for part in tag[1:].split("."))
        for tag in tags.splitlines()
        if tag.startswith("v") and FINAL.fullmatch(tag[1:])
    }
    return max(versions, key=lambda value: versions[value], default=None)


def select_version(current: str, suggested: str, released: str | None = None) -> str:
    """Finalize a staged candidate or apply the conventional-commit version.

    Raises:
        ValueError: If any version is not a canonical final semantic version.
    """
    versions: dict[str, tuple[int, ...]] = {}
    for value in (current, suggested, *([released] if released else [])):
        if not FINAL.fullmatch(value):
            msg = "Release versions must be canonical final semantic versions"
            raise ValueError(msg)
        versions[value] = tuple(int(part) for part in value.split("."))
    if released and versions[current] > versions[released]:
        return current
    return max((current, suggested), key=lambda value: versions[value])


if __name__ == "__main__":
    current, suggested, *tag_lists = sys.argv[1:]
    print(select_version(current, suggested, latest_release("\n".join(tag_lists))))
