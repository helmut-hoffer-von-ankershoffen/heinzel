# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Fail when a confidential term appears in files or commit messages.

The terms never live in the repository: a list in a public repository, even hashed, gives them away. They come from
`$CONFIDENTIAL_TERMS` (newline-separated; CI sets it from the repository secret of the same name) or from the file
`$CONFIDENTIAL_TERMS_FILE`, default `~/.config/heinzel-dev/confidential-terms`. One term per line, `#` comments.
Findings name the location and the term's number, never the term, because CI logs of a public repository are public.

Matching is case-insensitive after Unicode normalization (NFKC, invisible characters removed). Words of six letters or
more match anywhere, also inside identifiers (`term_cli`, `termdb`); shorter ones, typically first names, only where
no letter touches them, so they don't fire inside ordinary words. The words of a phrase may be joined by anything
that isn't a letter or digit, line breaks included, or by nothing. Binary files are scanned through their printable
strings, as `strings` would show them.

`--identity` also refuses an email git made up from the machine's name (`name@Some-MacBook-Air.local`), with or
without terms: it names a machine, not a mailbox, and would be published with every commit.

Usage: uv run --script scripts/check_confidentiality.py [OPTION] [FILE ...]
    [FILE ...]          files, or every tracked file
    --commit-msg FILE   a commit message (commit-msg hook)
    --messages A..B     the messages of a range of commits
    --text TEXT         a string, such as a pull request title or branch
    --identity          the author and committer git will record, and that their emails are not machine hostnames
    --branch            the current branch's name (pre-push hook)
    --require           fail instead of skipping when no terms are set
"""

import argparse
import contextlib
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TERMS_FILE = Path("~/.config/heinzel-dev/confidential-terms")
ANYWHERE = 6  # words at least this long match inside other words too
NOT_A_LETTER_BEFORE, NOT_A_LETTER_AFTER = r"(?<![^\W\d_])", r"(?![^\W\d_])"
PRINTABLE = re.compile(rb"[\x20-\x7e]{4,}")
# Domains that name a machine or a home or company network, not a mailbox; git builds such an email from the hostname
# when user.email isn't set.
HOST_DOMAINS = (".local", ".localdomain", ".lan", ".home", ".internal")


@dataclass(frozen=True)
class Finding:
    """Where a term was found; `term` is its 1-based number in the list, so the term itself stays private."""

    where: str
    line: int
    term: int

    def __str__(self) -> str:
        """`path:line: confidential term #N`."""
        return f"{self.where}:{self.line}: confidential term #{self.term}"


def load_terms() -> list[str]:
    """The configured terms, or none."""
    if text := os.environ.get("CONFIDENTIAL_TERMS"):
        return parse(text)
    path = Path(os.environ.get("CONFIDENTIAL_TERMS_FILE") or DEFAULT_TERMS_FILE).expanduser()
    return parse(path.read_text(encoding="utf-8")) if path.is_file() else []


def parse(text: str) -> list[str]:
    """One term per line; blank lines and `#` comments are skipped."""
    return [term for line in text.splitlines() if (term := line.split("#", 1)[0].strip())]


def patterns(terms: Iterable[str]) -> list[re.Pattern[str]]:
    """Case-insensitive patterns, as described in the module docstring."""
    compiled: list[re.Pattern[str]] = []
    for term in terms:
        words = normalize(term).split()
        body = r"[\W_]*".join(map(re.escape, words))
        if len(words) > 1 or len(words[0]) < ANYWHERE:
            body = NOT_A_LETTER_BEFORE + body + NOT_A_LETTER_AFTER
        compiled.append(re.compile(body, re.IGNORECASE))
    return compiled


def normalize(text: str) -> str:
    """Fold compatibility characters and drop invisible ones (zero-width spaces, joiners, soft hyphens)."""
    return "".join(c for c in unicodedata.normalize("NFKC", text) if unicodedata.category(c) != "Cf")


def scan(where: str, text: str, compiled: list[re.Pattern[str]]) -> Iterator[Finding]:
    """Find the terms in `text`.

    Yields:
        A finding per line and term.
    """
    text = normalize(text)
    seen: set[tuple[int, int]] = set()
    for index, pattern in enumerate(compiled, start=1):
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            if (line, index) not in seen:
                seen.add((line, index))
                yield Finding(where, line, index)


def _git(*args: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        msg = "Git is required to inspect repository content"
        raise RuntimeError(msg)
    return subprocess.run([executable, *args], check=True, capture_output=True, text=True, timeout=60).stdout  # noqa: S603 - fixed Git operations with operand boundaries


def _read(path: Path) -> str | None:
    """A file's text; for a binary file its printable strings, one per line; `None` if unreadable."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data or not _is_utf8(data):
        return "\n".join(run.decode("ascii") for run in PRINTABLE.findall(data))
    return data.decode("utf-8")


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def hostname_email(ident: str) -> str | None:
    """The email of a `git var GIT_*_IDENT` line if its domain is a machine's hostname, else `None`."""
    match = re.search(r"<([^>]*)>", ident)
    email = match[1] if match else ""
    domain = email.rpartition("@")[2].lower().rstrip(".")
    return email if "." not in domain or domain.endswith(HOST_DOMAINS) else None


def identity_problems() -> list[str]:
    """Why the author or committer email git will record is not one to publish."""
    problems: list[str] = []
    for role in ("author", "committer"):
        with contextlib.suppress(subprocess.CalledProcessError):  # no identity: git refuses to commit anyway
            if email := hostname_email(_git("var", f"GIT_{role.upper()}_IDENT")):
                problems.append(
                    f"The {role} email {email} is a machine's hostname, not a mailbox, and would be published with "
                    "the commit. Set yours: git config --global user.email you@example.com (GitHub's noreply address "
                    "works: ID+USERNAME@users.noreply.github.com, see https://github.com/settings/emails)."
                )
    return problems


def sources(args: argparse.Namespace) -> Iterator[tuple[str, str]]:
    """Collect what the command line selects.

    Yields:
        `(where, text)` pairs to scan; a file contributes its path and its content.
    """
    if args.commit_msg:
        yield "commit message", Path(args.commit_msg).read_text(encoding="utf-8")
    elif args.messages:
        for sha in _git("rev-list", "--end-of-options", args.messages, "--").split():
            yield f"commit {sha[:12]}", _git("log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", sha)
    elif args.branch:
        yield "branch", _git("rev-parse", "--abbrev-ref", "HEAD")
    elif args.identity:
        for role in ("author", "committer"):
            with contextlib.suppress(subprocess.CalledProcessError):  # no identity: git refuses to commit anyway
                yield role, _git("var", f"GIT_{role.upper()}_IDENT")
    elif args.text is not None:
        yield "text", args.text
    else:
        for name in args.files or _git("ls-files", "-z").split("\0"):
            path = Path(name)
            yield f"{name} (path)", name
            if name and (text := _read(path)) is not None:
                yield name, text


def main(argv: list[str] | None = None) -> int:
    """Scan what the arguments select and report findings."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*")
    parser.add_argument("--commit-msg")
    parser.add_argument("--messages")
    parser.add_argument("--text")
    parser.add_argument("--identity", action="store_true")
    parser.add_argument("--branch", action="store_true")
    parser.add_argument("--require", action="store_true")
    args = parser.parse_args(argv)
    if args.messages and args.messages.startswith("-"):
        parser.error("--messages must be a revision range, not a Git option")

    # Independent of the term list, which plugin developers usually don't have.
    problems = identity_problems() if args.identity else []
    for problem in problems:
        print(problem, file=sys.stderr)
    terms = load_terms()
    if not terms:
        if args.require:
            print("No confidential terms configured; set CONFIDENTIAL_TERMS.", file=sys.stderr)
            return 1
        print("Confidentiality check skipped: no terms configured (see scripts/check_confidentiality.py).")
        return 1 if problems else 0
    compiled = patterns(terms)
    findings: list[Finding] = []
    for where, text in sources(args):
        findings += scan(where, text, compiled)
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        print(f"{len(findings)} confidential term(s) found; remove them before committing.", file=sys.stderr)
        return 1
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
