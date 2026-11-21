# /// script
# requires-python = ">=3.15"
# dependencies = []
# ///
"""Read `contexts.toml`: who owns a file, and the import contracts that follow from the map.

contexts.py check      every file has exactly one owner and pyproject.toml holds the generated contracts
contexts.py write      regenerate the contracts block in pyproject.toml
contexts.py owner FILE the context that owns FILE
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ("src/", "web/", "desktop/", "apple/")
BEGIN = "# BEGIN generated from contexts.toml (scripts/contexts.py write); do not edit by hand"
END = "# END generated from contexts.toml"


def load(root: Path = ROOT) -> dict:
    """The parsed `contexts.toml`."""
    return tomllib.loads((root / "contexts.toml").read_text(encoding="utf-8"))


def tracked(root: Path = ROOT) -> list[str]:
    """Files git tracks inside the mapped trees."""
    out = subprocess.run(["git", "ls-files", *SCOPE], cwd=root, check=True, capture_output=True, text=True, timeout=60)  # noqa: S603, S607
    return [line for line in out.stdout.splitlines() if line]


def _matches(path: str, patterns: list[str]) -> bool:
    return any(PurePosixPath(path).full_match(pattern) for pattern in patterns)


def owners(config: dict, files: list[str]) -> dict[str, list[str]]:
    """File to the names of the contexts that own it (a healthy map has exactly one)."""
    found: dict[str, list[str]] = {}
    for name, context in config["context"].items():
        for path in files:
            if _matches(path, context["src"]) and not _matches(path, context.get("exclude", [])):
                found.setdefault(path, []).append(name)
    return {path: found.get(path, []) for path in files}


def _ignores(entries: list[str]) -> str:
    if not entries:
        return ""
    body = ",\n".join(f'    "{entry}"' for entry in entries)
    return f"ignore_imports = [\n{body},\n]\n"


def contracts_block(config: dict) -> str:
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
    parser.add_argument("action", choices=["check", "write", "owner"])
    parser.add_argument("file", nargs="?")
    args = parser.parse_args()
    if args.action == "write":
        write()
    elif args.action == "owner":
        sys.stdout.write(" ".join(owners(load(), [args.file])[args.file]) + "\n")
    else:
        found = problems()
        sys.stderr.write("\n".join(found[:40]) + ("\n" if found else ""))
        return 1 if found else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
