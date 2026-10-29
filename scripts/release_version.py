# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Finalize a version already used by candidate SDK artifacts without downgrading their public version."""

import re
import sys

FINAL = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def select_version(current: str, suggested: str) -> str:
    """Keep an advanced candidate version or apply the newer conventional-commit version.

    Raises:
        ValueError: If either version is not a canonical final semantic version.
    """
    versions: dict[str, tuple[int, ...]] = {}
    for value in (current, suggested):
        if not FINAL.fullmatch(value):
            msg = "Release versions must be canonical final semantic versions"
            raise ValueError(msg)
        versions[value] = tuple(int(part) for part in value.split("."))
    return max(versions, key=lambda value: versions[value])


if __name__ == "__main__":
    print(select_version(*sys.argv[1:]))
