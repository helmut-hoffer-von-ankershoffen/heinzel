# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Assemble the README demo's VHS tape from the core intro and the snippets modules contribute.

docs/assets/demo-intro.tape holds the settings and the core's part. A module (built-in, or a first-party plugin under
plugins/) adds its part as a `demo.tape` next to its `__init__.py`: a few real `hz` commands with
`Type`, `Enter`, `Wait`, `Sleep`, `Hide` and `Show`, plus `Require` for any other program it pipes into, which moves
to the header. Settings and `Output` belong to the intro only. The order is stable: the intro, then first-party plugins,
then built-in modules, each sorted by path; built-in commands such as `hz system doctor` come last because they report
on everything installed before them. Every snippet starts on a cleared screen.
Dependencies are installed before recording; snippets must not replace that running tool environment.

Usage: uv run --script scripts/demo_tape.py [--check]   writes docs/assets/demo.tape, or fails if it is out of date
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTRO = ROOT / "docs" / "assets" / "demo-intro.tape"
TAPE = ROOT / "docs" / "assets" / "demo.tape"
SNIPPET = "demo.tape"

ALLOWED = {"Type", "Enter", "Wait", "Sleep", "Hide", "Show", "Require"}
COMMAND = re.compile(r"^(?P<command>[A-Z][a-zA-Z]*)(?:[@+]\S*)?(?:\s+(?P<args>.*))?$")
TYPED = re.compile(r"""^(["'`])(?P<text>.*)\1$""")
REPLACES_TOOL = re.compile(r"^hz(?:\s+--expert)?\s+system\s+(?:update\b|plugin\s+(?:add|remove|develop)\b)")
CLEAR = ["Hide", 'Type "clear"', "Enter", "Show"]
# Holds the last output on screen for the final frames, which a render may otherwise cut short.
HOLD = "Sleep 2s"


def snippets(root: Path = ROOT) -> list[Path]:
    """The modules' demo snippets in demo order: first-party plugins, then built-in modules, each sorted by path."""
    plugins = sorted(root.glob(f"plugins/*/src/**/{SNIPPET}"))
    builtins = sorted((root / "src" / "heinzel" / "modules").rglob(SNIPPET))
    return plugins + builtins


def problems(snippet: Path, root: Path = ROOT) -> list[str]:
    """What a snippet may not do: live outside a module, set things only the intro sets, or type anything but hz."""
    where = snippet.relative_to(root)
    found = [] if (snippet.parent / "__init__.py").is_file() else [f"{where}: not next to a module's __init__.py"]
    for number, line in enumerate(snippet.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = COMMAND.match(stripped)
        if not match or match["command"] not in ALLOWED:
            found.append(f"{where}:{number}: only {', '.join(sorted(ALLOWED))} belong in a snippet: {stripped}")
            continue
        if match["command"] == "Type":
            typed = TYPED.match(match["args"] or "")
            text = typed["text"] if typed else ""
            if not (text.startswith("hz ") or text == "clear"):
                found.append(f"{where}:{number}: type real `hz` commands (or `clear`), nothing faked: {stripped}")
            elif REPLACES_TOOL.match(text):
                found.append(f"{where}:{number}: install plugins before recording; preserve the recording environment")
    return found


def _without_leading_comments(text: str) -> list[str]:
    lines = text.splitlines()
    while lines and (not lines[0].strip() or lines[0].startswith("#")):
        lines.pop(0)
    return lines


def assemble(root: Path = ROOT) -> str:
    """The whole tape: the intro with every snippet's `Require` in its header, then the snippets.

    Raises:
        ValueError: if a snippet breaks the rules in `problems`.
    """
    parts = snippets(root)
    if found := [problem for snippet in parts for problem in problems(snippet, root)]:
        raise ValueError("\n".join(found))
    intro = _without_leading_comments((root / INTRO.relative_to(ROOT)).read_text(encoding="utf-8"))
    for line in intro:
        match = COMMAND.match(line.strip())
        if match and match["command"] == "Type":
            typed = TYPED.match(match["args"] or "")
            text = typed["text"] if typed else ""
            if not (text.startswith("hz ") or text in {"clear", "export PS1='$ '"}):
                msg = "The intro types real hz commands only; install dependencies before recording"
                raise ValueError(msg)
    requires = [line for line in intro if line.startswith("Require ")]
    body: list[str] = []
    for snippet in parts:
        lines = snippet.read_text(encoding="utf-8").strip().splitlines()
        requires += [line.strip() for line in lines if line.startswith("Require ") and line.strip() not in requires]
        body += ["", f"# --- {snippet.parent.relative_to(root)} ---", *CLEAR]
        body += [line for line in lines if not line.startswith(("Require ", "#"))]
    last = max(index for index, line in enumerate(intro) if line.startswith("Require "))
    header = [line for line in intro[: last + 1] if not line.startswith("Require ")] + requires
    lines = [
        "# The README demo, generated by scripts/demo_tape.py from docs/assets/demo-intro.tape and the demo.tape",
        "# snippets of the modules; edit those and run `make fix`. Render it with `make demo`.",
        "",
        *header,
        *intro[last + 1 :],
        *body,
        "",
        HOLD,
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)) + "\n"


def main() -> int:
    """Write docs/assets/demo.tape, or with --check only report that it is out of date."""
    try:
        tape = assemble()
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if TAPE.exists() and TAPE.read_text(encoding="utf-8") == tape:
        return 0
    if "--check" in sys.argv:
        print("docs/assets/demo.tape is out of date; run `make fix`.", file=sys.stderr)
        return 1
    TAPE.write_text(tape, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
