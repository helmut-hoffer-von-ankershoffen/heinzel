# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""The paths that are not CI: product code and its tests, which a head may change without separate hosted validation.

"Not CI" does not mean "not run" and never means "not reviewed". The merge-queue CI run of the head still executes and
checks every one of these files, and every head is still reviewed. A head that changes only these paths only skips
the separate hosted lane validation (`sm head validate`) before that run. Everything else counts as CI: the
workflows, actions, scripts, the Makefile, every tool configuration, every conftest and pytest plugin, the end-to-end
drivers, the gate tests at the top of tests/unit/, tests/integration/ and every path not listed here, including every
new top-level directory.

The list is fail-closed. Matching is `fnmatch.fnmatchcase(path, glob)` on the repository-relative POSIX path, so `*`
also crosses `/`. Each glob names a file type, and a file type's globs leave out the names a tool loads on its own
(conftest.py, setup or config modules, type declarations), so a new file of that kind is CI wherever it is added.
A matched Python file that configures pytest for the whole session (`pytest_plugins`, a `pytest_*` hook) is CI too: it
is named in the exception list, which the manifest carries next to the globs. A new plain product test matches a glob
and changes nothing in the manifest; a new or edited file that configures pytest makes the manifest stale, and CI
refuses a stale manifest. Release tooling reads both lists from scripts/release_policy.json (`not_ci`) at the commit it
checks; tests/unit/release_policy_test.py proves that no CI input is not CI.
"""

from __future__ import annotations

import fnmatch
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# Product packages: Python modules, except the names pytest or setuptools load by themselves.
PRODUCT_PACKAGES = ("src/heinzel/connectors/", "src/heinzel/heads/", "src/heinzel/kernel/", "src/heinzel/modules/")
# Tests of the product packages, one directory per package; the gate tests at the top of tests/unit/ stay CI. A matched
# file that configures pytest for the whole session (CONFIGURES_PYTEST) is an exception and stays CI.
PRODUCT_TESTS = ("tests/unit/connectors/", "tests/unit/heads/", "tests/unit/kernel/", "tests/unit/modules/")
CONFIGURES_PYTEST = re.compile(r"^(?:pytest_plugins\b|def pytest_(?!generate_tests\b))", re.MULTILINE)
# The web app's sources, except web/src/test/ (the Vitest setup and helpers): web/src/ then any name not starting t.
WEB_SOURCES = "web/src/[!t]"
LOADED_PYTHON = ("conftest.py", "setup.py")
LOADED_WEB = ("config.ts", "config.tsx", "setup.ts", "setup.tsx", ".d.ts")


def ending(extension: str, excluded: tuple[str, ...]) -> list[str]:
    """Glob tails for names ending in `extension` but in none of `excluded` (those that end in `extension`).

    fnmatch has no negation, so each tail fixes the characters before the extension one at a time: a name differs
    from every excluded suffix at the first character, counted from the end, where it leaves their shared tail.
    """
    tails = [suffix.removesuffix(extension)[::-1] for suffix in excluded if suffix.endswith(extension)]
    if any(not tail for tail in tails):
        return []

    def branch(shared: str, rest: list[str]) -> list[str]:
        here = sorted({tail[0] for tail in rest if tail})
        globs = [f"*[!{''.join(here)}]{shared[::-1]}{extension}"]
        for character in here:
            deeper = [tail[1:] for tail in rest if tail and tail[0] == character]
            if all(deeper):
                globs += branch(shared + character, deeper)
        return globs

    return branch("", tails)


GLOBS = (
    "docs/*.md",  # prose; the install scripts and data files under docs/ and the root README stay CI
    *(package + tail for package in PRODUCT_PACKAGES for tail in ending(".py", LOADED_PYTHON)),
    *(WEB_SOURCES + tail for extension in (".ts", ".tsx") for tail in ending(extension, LOADED_WEB)),
    WEB_SOURCES + "*.css",
    *(tests + "*_test.py" for tests in PRODUCT_TESTS),
)
LITERAL = re.compile(r"[^\[*?]+")


def exceptions(root: Path, tracked: list[str]) -> list[str]:
    """The tracked Python files a glob matches that configure pytest for the session: CI by name.

    Raises:
        ValueError: such a path contains a glob character, so it could not be named literally.
    """
    found = sorted(
        path
        for path in tracked
        if path.endswith(".py")
        and any(fnmatch.fnmatchcase(path, glob) for glob in GLOBS)
        and CONFIGURES_PYTEST.search((root / path).read_text(encoding="utf-8"))
    )
    unnamed = [path for path in found if not LITERAL.fullmatch(path)]
    if unnamed:
        message = f"cannot name a not-CI exception literally: {', '.join(unnamed)}"
        raise ValueError(message)
    return found


def not_ci(root: Path, tracked: list[str]) -> dict[str, list[str]]:
    """The not-CI rule of this checkout: the globs, and the matched files that are CI all the same."""
    return {"except": exceptions(root, tracked), "match": sorted(set(GLOBS))}


def is_not_ci(path: str, rule: dict[str, list[str]]) -> bool:
    """Whether a repository-relative path is not CI: a glob matches it and it is no exception; anything else is CI."""
    return bool(path) and path not in rule["except"] and any(fnmatch.fnmatchcase(path, glob) for glob in rule["match"])
