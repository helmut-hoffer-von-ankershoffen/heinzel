# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Read `contexts.toml`: who owns a file, and the import contracts that follow from the map.

contexts.py check      every file has exactly one owner and pyproject.toml holds the generated contracts
contexts.py write      regenerate the contracts block in pyproject.toml
contexts.py owner FILE the context that owns FILE
contexts.py select     changed paths on stdin: python=true|false (level L0)
contexts.py upgrade    changed paths on stdin: upgrade=true|false (the upgrade lane and the macOS cells of a PR)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ("src/", "web/", "desktop/", "apple/")
BEGIN = "# BEGIN generated from contexts.toml (scripts/contexts.py write); do not edit by hand"
END = "# END generated from contexts.toml"


def load(root: Path = ROOT) -> dict[str, Any]:
    """The parsed `contexts.toml`."""
    return tomllib.loads((root / "contexts.toml").read_text(encoding="utf-8"))


def tracked(root: Path = ROOT) -> list[str]:
    """Files git tracks inside the mapped trees."""
    out = subprocess.run(["git", "ls-files", *SCOPE], cwd=root, check=True, capture_output=True, text=True, timeout=60)  # noqa: S603, S607
    return [line for line in out.stdout.splitlines() if line]


@cache
def _glob(pattern: str) -> re.Pattern[str]:
    """A repository glob as a regex: `**` crosses directories, `*` and `?` stay inside one."""
    out, index = "", 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out, index = out + "(?:.*/)?", index + 3
        elif pattern.startswith("**", index):
            out, index = out + ".*", index + 2
        elif pattern[index] == "*":
            out, index = out + "[^/]*", index + 1
        elif pattern[index] == "?":
            out, index = out + "[^/]", index + 1
        else:
            out, index = out + re.escape(pattern[index]), index + 1
    return re.compile(out)


def matches(path: str, patterns: list[str]) -> bool:
    """Whether `path` matches any of the repository globs."""
    return any(_glob(pattern).fullmatch(path) for pattern in patterns)


_matches = matches


def owners(config: dict[str, Any], files: list[str]) -> dict[str, list[str]]:
    """File to the names of the contexts that own it (a healthy map has exactly one)."""
    found: dict[str, list[str]] = {}
    for name, context in config["context"].items():
        for path in files:
            if _matches(path, context["src"]) and not _matches(path, context.get("exclude", [])):
                found.setdefault(path, []).append(name)
    return {path: found.get(path, []) for path in files}


def python_needed(config: dict[str, Any], changed: list[str]) -> tuple[bool, str]:
    """Whether a change needs the Python jobs, and why not when it does not (the selector's level L0).

    Only a change that stays inside the contexts listed in `[select] skip_python` or inside `[select] docs` skips them;
    a path no context owns, a context that is not listed and every shared path keep them.
    """
    select = config["select"]
    skip = set(select["skip_python"])
    prose = [path for path in changed if matches(path, select["docs"]) and not matches(path, select["keep"])]
    owned = owners(config, [path for path in changed if path not in prose])
    outside = [path for path, names in owned.items() if len(names) != 1 or names[0] not in skip]
    if outside:
        return True, f"{outside[0]}: needs the Python jobs"
    return False, "only " + ", ".join(sorted({n for names in owned.values() for n in names} | {"docs"}))


def upgrade_needed(config: dict[str, Any], changed: list[str]) -> tuple[bool, str]:
    """Whether a change runs the in-place upgrade lane and the macOS cells on its pull request, and why.

    A path owned by, or a test of, a context in `[select] upgrade`, and every path of `[select] upgrade_paths` select
    them; an empty change selects them too (fail closed).
    """
    select = config["select"]
    if not changed:
        return True, "no changed paths: the upgrade lane runs"
    names = select["upgrade"]
    contexts = [config["context"][name] for name in names]
    for path in changed:
        if matches(path, select["upgrade_paths"]):
            return True, f"{path}: an upgrade path"
        owned = owners(config, [path])[path]
        if any(name in names for name in owned) or any(matches(path, c.get("tests", [])) for c in contexts):
            return True, f"{path}: in an upgrade context ({', '.join(names)})"
    return False, f"no path in {', '.join(names)} or the upgrade paths"


def _ignores(entries: list[str]) -> str:
    if not entries:
        return ""
    body = ",\n".join(f'    "{entry}"' for entry in entries)
    return f"ignore_imports = [\n{body},\n]\n"


def contracts_block(config: dict[str, Any]) -> str:
    """The `[[tool.importlinter.contracts]]` tables that follow from the contexts."""
    contexts = config["context"]
    inner = [c["package"] for c in contexts.values() if c.get("package") and not c.get("head")]
    heads = [c["package"] for c in contexts.values() if c.get("head")]
    allow = config.get("allow", {})
    blocks = [
        "[[tool.importlinter.contracts]]\n"
        'name = "Contexts import the spec and the ports, not each other (contexts.toml)"\n'
        'type = "independence"\n'
        f"modules = {inner!r}\n".replace("'", '"')
        + _ignores(allow.get("contexts", [])),
        (
            "[[tool.importlinter.contracts]]\n"
            'name = "The ports import no head and no module: they are spec (contexts.toml)"\n'
            'type = "forbidden"\n'
            'source_modules = ["heinzel.ports"]\n'
            'forbidden_modules = ["heinzel.heads", "heinzel.modules"]\n'
        ),
        "[[tool.importlinter.contracts]]\n"
        'name = "Heads import the spec and the head kit, not each other (contexts.toml)"\n'
        'type = "independence"\n'
        f"modules = {heads!r}\n".replace("'", '"')
        + _ignores(allow.get("heads", [])),
    ]
    return "\n".join([BEGIN, "", *["\n".join(b.rstrip("\n").split("\n")) + "\n" for b in blocks], END, ""])


def current_block(text: str) -> str | None:
    """The generated block as it stands in pyproject.toml, or None."""
    if BEGIN not in text or END not in text:
        return None
    start = text.index(BEGIN)
    return text[start : text.index(END) + len(END) + 1]


def problems(root: Path = ROOT) -> list[str]:
    """What is wrong with the map and the generated contracts."""
    config = load(root)
    found = [
        f"{path}: {', '.join(names) or 'no context'}"
        for path, names in owners(config, tracked(root)).items()
        if len(names) != 1
    ]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    if current_block(pyproject) != contracts_block(config):
        found.append(
            "pyproject.toml: the generated import contracts are stale; run `uv run python scripts/contexts.py write`"
        )
    return found


def write(root: Path = ROOT) -> None:
    """Regenerate the contracts block in pyproject.toml."""
    path = root / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    block = contracts_block(load(root))
    old = current_block(text)
    path.write_text(text.replace(old, block) if old else text.rstrip("\n") + "\n\n" + block, encoding="utf-8")


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["check", "write", "owner", "select", "upgrade"])
    parser.add_argument("file", nargs="?")
    args = parser.parse_args()
    if args.action == "write":
        write()
    elif args.action == "select":
        needed, why = python_needed(load(), [line.strip() for line in sys.stdin if line.strip()])
        sys.stdout.write(f"python={'true' if needed else 'false'}\n")
        sys.stderr.write(f"{why}\n")
    elif args.action == "upgrade":
        needed, why = upgrade_needed(load(), [line.strip() for line in sys.stdin if line.strip()])
        sys.stdout.write(f"upgrade={'true' if needed else 'false'}\n")
        sys.stderr.write(f"{why}\n")
    elif args.action == "owner":
        sys.stdout.write(" ".join(owners(load(), [args.file])[args.file]) + "\n")
    else:
        found = problems()
        sys.stderr.write("\n".join(found[:40]) + ("\n" if found else ""))
        return 1 if found else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
