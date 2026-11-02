# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Fail when a confidential term appears in files or commit messages.

The terms never live in the repository: a list in a public repository, even hashed, gives them away. They come from
`$CONFIDENTIAL_TERMS` (newline-separated; CI sets it from the repository secret of the same name) or from the file
`$CONFIDENTIAL_TERMS_FILE`, default `~/.config/heinzel-dev/confidential-terms`. One term per line, `#` comments.
`--push` scans what a push sends (the pre-push hook): the messages, author lines, paths and added lines of every
commit in the range, old ones included.
Findings name the location and the term's number, never the term, because CI logs of a public repository are public.

Matching is case-insensitive after Unicode normalization (NFKC, invisible characters removed), and always whole-word:
a term matches only where no letter or digit touches it, so it never fires inside an ordinary word (a short term in
`minimal`), but does inside punctuation, markdown and identifiers split by `_` or `-` (`term_cli`, `**term**`,
`term's`). The words of a phrase may be joined by anything that isn't a letter or digit, line breaks included, or by
nothing. Binary files are scanned through their printable
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
# A word boundary that counts `_` as punctuation (`\b` does not): no letter or digit may touch the term.
NOT_ALNUM_BEFORE, NOT_ALNUM_AFTER = r"(?<![^\W_])", r"(?![^\W_])"
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
        compiled.append(re.compile(NOT_ALNUM_BEFORE + body + NOT_ALNUM_AFTER, re.IGNORECASE))
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
    # Fixed Git operations with operand boundaries
    return subprocess.run([executable, *args], check=True, capture_output=True, text=True, timeout=60).stdout  # noqa: S603


def _revision(name: str) -> str:
    """Rebuild a revision from parts that cannot carry an option: Git's own ref names, hex digits or `HEAD` offsets.

    Raises:
        ValueError: If the input is none of these.
    """
    if offset := re.fullmatch(r"HEAD(?:([~^])([0-9]{1,4}))?", name):
        return f"HEAD{offset[1]}{int(offset[2])}" if offset[1] else "HEAD"
    if re.fullmatch(r"[0-9a-f]{7,64}", name):
        return f"{int(name, 16):0{len(name)}x}"
    for known in _git(
        "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes", "refs/tags"
    ).splitlines():
        if known == name:
            return known
    msg = f"Not a branch, tag, commit or HEAD offset: {name!r}"
    raise ValueError(msg)


def _range(text: str) -> list[str]:
    """A revision or a `from..to` / `from...to` range, each end rebuilt by `_revision`."""
    for separator in ("...", ".."):
        if separator in text:
            start, _, end = text.partition(separator)
            return [f"{_revision(start)}{separator}{_revision(end)}"]
    return [_revision(text)]


def _push_range() -> list[str]:
    """What a push sends.

    That is `$PRE_COMMIT_FROM_REF..$PRE_COMMIT_TO_REF` as the pre-push hook sets them, else every commit of HEAD that
    no remote branch has yet. Nothing is exempt: old commits in the range are scanned like new ones.
    """
    start, end = os.environ.get("PRE_COMMIT_FROM_REF", ""), os.environ.get("PRE_COMMIT_TO_REF") or "HEAD"
    if start and set(start) != {"0"}:
        return [f"{_revision(start)}..{_revision(end)}"]
    return [_revision(end), "--not", "--remotes"]


def added_lines(diff: str) -> Iterator[tuple[str, list[tuple[int, str]]]]:
    """The added lines of a zero-context diff, per file, with their line numbers in the new file.

    Yields:
        `(path, [(line number, text)])` for each file with added lines.
    """
    path, found, number = "", [], 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            if found:
                yield path, found
            path, found = line[4:].removeprefix("b/"), []
        elif hunk := re.match(r"@@ -\S+ \+(\d+)", line):
            number = int(hunk[1])
        elif line.startswith("+") and not line.startswith("+++"):
            found.append((number, line[1:]))
            number += 1
    if found:
        yield path, found


def _push_sources() -> Iterator[tuple[str, str]]:
    """The messages, author lines, paths and added content of the commits being pushed.

    Yields:
        `(where, text)` pairs to scan.
    """
    for sha in _git("rev-list", *_push_range(), "--").split():
        yield f"commit {sha[:12]}", _git("log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", sha)
        for path in _git("diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", sha).split("\0"):
            if path:
                yield f"commit {sha[:12]} {path} (path)", path
        diff = _git("diff-tree", "-p", "-U0", "--no-color", "--no-renames", "--root", sha)
        for path, lines in added_lines(diff):
            # Each added line sits on its real line number, so a finding names the right line; lines between are a
            # filler no phrase can span.
            rows = ["0"] * lines[-1][0]
            for number, text in lines:
                rows[number - 1] = text
            yield f"commit {sha[:12]} {path}", "\n".join(rows)


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


def _identities() -> Iterator[tuple[str, str]]:
    for role in ("author", "committer"):
        with contextlib.suppress(subprocess.CalledProcessError):  # no identity: git refuses to commit anyway
            yield role, _git("var", f"GIT_{role.upper()}_IDENT")


def sources(args: argparse.Namespace) -> Iterator[tuple[str, str]]:
    """Collect what the command line selects.

    Yields:
        `(where, text)` pairs to scan; a file contributes its path and its content.
    """
    if args.commit_msg:
        yield "commit message", Path(args.commit_msg).read_text(encoding="utf-8")
    elif args.messages:
        for sha in _git("rev-list", "--end-of-options", *_range(args.messages), "--").split():
            yield f"commit {sha[:12]}", _git("log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", sha)
    elif args.push:
        yield from _push_sources()
    elif args.branch:
        yield "branch", _git("rev-parse", "--abbrev-ref", "HEAD")
    elif args.identity:
        yield from _identities()
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
    parser.add_argument("--push", action="store_true", help="the commits being pushed: messages, paths, added content")
    parser.add_argument("--require", action="store_true")
    args = parser.parse_args(argv)
    if args.messages:
        try:
            _range(args.messages)
        except (ValueError, subprocess.CalledProcessError):
            parser.error("--messages must be a branch, tag, commit or HEAD offset, or a range of two")

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
