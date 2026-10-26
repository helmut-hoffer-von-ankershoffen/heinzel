"""Every `hz` command the onboarding guides cite parses against the real command tree, so the guides can't drift.

Commands are read from docs/guides/*.md: fenced `bash` blocks (full invocations, parsed strictly) and inline code
(a mention such as `hz team new`, so a missing argument is allowed, an unknown command or option is not). Shell
chains (`&&`, `|`, `;`) are split, continuations joined, comments dropped and `<placeholders>` become dummy values.
Each command is resolved through the CLI head and parsed by click without running it. A command that isn't built yet
must therefore never be written as `hz ...` code in a bash block; inline, it is allowed only on a line that carries
the marker "coming in the next release". Integration level: it goes through the CLI head's real command tree.

Typer vendors click, so `click.Group` and `click.UsageError` don't match its objects; the tree is walked by duck
typing and exceptions are told apart by class name.
"""

import re
import shlex
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import typer

from heinzel.heads.cli import build
from heinzel.kernel import boot

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
GUIDES = sorted((REPO / "docs" / "guides").glob("*.md"))
BASH_BLOCK = re.compile(r"```bash\n(.*?)\n```", re.DOTALL)
INLINE = re.compile(r"`(hz(?: [^`\n]*)?)`")
PLACEHOLDER = re.compile(r"<[^<>\n]+>")
COMING = "coming in the next release"
INVITE = "uvx --from heinzel heinzel join "


def _split(line: str) -> list[str]:
    """The `hz ...` commands of one shell line: comments dropped, chains split, placeholders made parseable."""
    try:
        words = shlex.split(PLACEHOLDER.sub("placeholder", line), comments=True)
    except ValueError:
        return []
    commands: list[list[str]] = [[]]
    redirected = False
    for word in words:
        if word in {"&&", "||", "|", ";"}:
            commands.append([])
            redirected = False
        elif word in {">", ">>"} or redirected:
            redirected = True  # a redirection's target isn't an argument
        else:
            commands[-1].append(word)
    return [shlex.join(c) for c in commands if c and c[0] == "hz"]


def commands_in(text: str) -> tuple[list[str], list[str]]:
    """The commands of a document: those of its bash blocks (invocations) and those of its inline code (mentions)."""
    invocations: list[str] = []
    for block in BASH_BLOCK.findall(text):
        for line in block.replace("\\\n", " ").splitlines():
            invocations += _split(line)
    mentions = [c for line in text.splitlines() if COMING not in line for m in INLINE.findall(line) for c in _split(m)]
    return invocations, mentions


def _is(exc: BaseException, name: str) -> bool:
    return any(cls.__name__ == name for cls in type(exc).__mro__)


def _problem(exc: BaseException, *, mention: bool) -> str | None:
    """What a parse exception means: None when the command parsed after all, else the message; re-raised if neither."""
    if _is(exc, "Exit"):
        return None  # an eager option such as --version or --help ran its callback: it parsed
    if mention and _is(exc, "MissingParameter"):
        return None  # a mention names the command, not every argument
    if _is(exc, "UsageError"):
        return str(exc) or exc.__class__.__name__
    raise exc


def parses(hz: Any, command: str, *, mention: bool = False) -> str | None:
    """None when `command` parses against the tree rooted at `hz`; otherwise what is wrong with it."""
    args = shlex.split(command)[1:]
    group, path = hz, ["hz"]
    with hz.make_context("hz", [], resilient_parsing=True) as root:
        parent = root
        while hasattr(group, "resolve_command") and args and not args[0].startswith("-"):
            head = args[0]
            try:
                name, sub, args = group.resolve_command(parent, args)
            except Exception as exc:  # noqa: BLE001 - click's vendored UsageError, told apart by name
                return _problem(exc, mention=mention)
            if sub is None:
                return f"no command {head!r} under {' '.join(path)}"
            group, path = sub, [*path, str(name)]
            parent = parent.__class__(group, info_name=path[-1], parent=parent)
        if hasattr(group, "resolve_command") and not args:
            return None  # a bare group such as `hz` or `hz team`, which prints its help
        try:
            group.make_context(path[-1], list(args), parent=parent)
        except Exception as exc:  # noqa: BLE001 - see above
            return _problem(exc, mention=mention)
    return None


Checker = Callable[..., str | None]


@pytest.fixture(scope="module")
def check() -> Checker:
    hz = typer.main.get_command(build(boot()))
    return lambda command, **kw: parses(hz, command, **kw)


def _cases() -> list[tuple[str, str, bool]]:
    cases: set[tuple[str, str, bool]] = set()
    for guide in GUIDES:
        invocations, mentions = commands_in(guide.read_text(encoding="utf-8"))
        cases |= {(guide.name, c, False) for c in invocations} | {(guide.name, c, True) for c in mentions}
    return sorted(cases)


CASES = _cases()


def test_the_guides_exist_and_cite_commands():
    assert [g.name for g in GUIDES] == ["joining.md", "manager.md"]
    assert len({c for _, c, mention in CASES if not mention}) >= 30


def test_the_checker_rejects_what_the_cli_rejects(check):
    assert check("hz nosuch") is not None
    assert check("hz heinzel wake x --nope") is not None
    assert check("hz team new") is not None  # a required argument is missing
    assert check("hz team new", mention=True) is None
    assert check("hz heinzel wake x --dry-run") is None
    assert check("hz --version") is None


@pytest.mark.parametrize(
    ("guide", "command", "mention"),
    CASES,
    ids=[f"{guide}: {command}{' (mention)' if mention else ''}" for guide, command, mention in CASES],
)
def test_guide_command_parses(check, guide, command, mention):
    problem = check(command, mention=mention)
    assert problem is None, f"{guide}: {command!r} doesn't parse: {problem}"


def test_the_invite_line_is_the_console_script_and_marked_lines_carry_the_whole_phrase():
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["heinzel"] == scripts["hz"]
    for guide in GUIDES:
        text = guide.read_text(encoding="utf-8")
        assert INVITE in text, f"{guide.name} doesn't show the invite line"
        for number, line in enumerate(text.splitlines(), start=1):
            assert not (("**coming" in line or "coming in" in line) and COMING not in line), (
                f"{guide.name}:{number}: write the whole phrase {COMING!r} on one line"
            )
