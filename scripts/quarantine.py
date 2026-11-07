# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""The quarantine lane: a known-flaky test leaves the required CI jobs for at most seven days, and still runs.

`tests/quarantine.toml` lists the entries. The pytest side (`tests/_quarantine.py`, registered by `tests/conftest.py`)
deselects an entry's tests from every ordinary run, so a known flake cannot fail `CI OK`; the `Quarantine (<os>)` jobs
run only those tests, retry a failure, and report each result without blocking. The rules keep it from hiding bugs:

- an entry names one test (`tests/<level>/<file>_test.py::<name>`, a parametrized id or its prefix), a bead, the
  date it was `added`, an `until` date at most 7 days later and a `reason`; nothing else, at most 8 entries;
- an entry past `until` is no longer quarantined: its tests run in the required jobs again, and the required
  `Quarantine registry` job fails until the test is fixed or the entry removed;
- a malformed registry fails every pytest run (fail closed), never quarantines a test.

Commands (standard library only):
  check    exit 1 on any invalid entry, an entry past `until` or added in the future (the required registry job)
  list     one line per entry with its age and days left
  plan     `selected=true|false` and `browsers=true|false` for GitHub's step output (any entry in date, any e2e one)
  run      run only the quarantined tests (HZ_QUARANTINE=only) with up to `--attempts` tries per failing test, write
           `--report` (JSON) and `--summary` (Markdown); exit 0 whatever the tests do, 2 on an invalid registry
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tests" / "quarantine.toml"
MAX_ENTRIES = 8
MAX_DAYS = 7
MAX_ATTEMPTS = 3
MAX_REASON = 200
FIELDS = frozenset({"test", "bead", "added", "until", "reason"})
TEST = re.compile(
    r"tests/(?:unit|integration|e2e)/[A-Za-z0-9_/]+_test\.py"
    r"::[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)?(?:\[[^\]\s]+\])?\Z"
)
BEAD = re.compile(r"[a-z0-9]{2,6}\Z")


class RegistryError(ValueError):
    """The registry breaks a rule; every pytest run and the registry job fail on it."""

    def __init__(self, problems: Sequence[str]) -> None:
        """Every broken rule, one per line."""
        super().__init__("\n".join(problems))


@dataclass(frozen=True)
class Entry:
    """One quarantined test and the bead and dates that bound it."""

    test: str
    bead: str
    added: dt.date
    until: dt.date
    reason: str

    @property
    def path(self) -> str:
        """The test file, which the lane collects."""
        return self.test.split("::", 1)[0]

    def matches(self, nodeid: str) -> bool:
        """The node itself, one of its parametrized cases, or (for a class) one of its methods."""
        return nodeid == self.test or nodeid.startswith((self.test + "[", self.test + "::"))

    def active(self, today: dt.date) -> bool:
        """Added and not past `until`: only then does it take the test out of the required jobs."""
        return self.added <= today <= self.until


def today() -> dt.date:
    """The UTC date, which every runner agrees on."""
    return dt.datetime.now(dt.UTC).date()


def _date(row: dict[str, object], key: str, where: str, problems: list[str]) -> dt.date | None:
    value = row.get(key)
    if type(value) is not dt.date:  # a TOML local date; a datetime or a string is refused
        problems.append(f"{where}: `{key}` must be a date like 2026-11-07")
        return None
    return value


def _entry(row: object, index: int, problems: list[str]) -> Entry | None:
    where = f"entry {index}"
    if not isinstance(row, dict):
        problems.append(f"{where}: not a table")
        return None
    if set(row) != FIELDS:
        missing, extra = sorted(FIELDS - set(row)), sorted(set(row) - FIELDS)
        problems.append(f"{where}: needs exactly {', '.join(sorted(FIELDS))} (missing {missing}, unknown {extra})")
        return None
    test, bead, reason = row["test"], row["bead"], row["reason"]
    if not isinstance(test, str) or not TEST.fullmatch(test):
        problems.append(f"{where}: `test` must be one node id like tests/unit/x_test.py::test_y")
        return None
    where = f"entry {index} ({test})"
    if not isinstance(bead, str) or not BEAD.fullmatch(bead):
        problems.append(f"{where}: `bead` must be a bead id (2-6 lowercase letters or digits)")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON or "\n" in reason:
        problems.append(f"{where}: `reason` must be one line of 1-{MAX_REASON} characters")
    added, until = _date(row, "added", where, problems), _date(row, "until", where, problems)
    if added and until and not 0 <= (until - added).days <= MAX_DAYS:
        problems.append(f"{where}: `until` must be 0-{MAX_DAYS} days after `added`")
    if len(problems) or added is None or until is None:
        return None
    return Entry(test, bead, added, until, reason)


def parse(text: str) -> list[Entry]:
    """The entries of a registry text.

    Raises:
        RegistryError: naming every broken rule.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise RegistryError([f"tests/quarantine.toml is not valid TOML: {error}"]) from None
    problems: list[str] = []
    if set(data) - {"quarantine"}:
        problems.append(f"unknown top-level keys: {sorted(set(data) - {'quarantine'})}")
    rows = data.get("quarantine", [])
    if not isinstance(rows, list):
        raise RegistryError(["`quarantine` must be an array of tables ([[quarantine]])"])
    if len(rows) > MAX_ENTRIES:
        problems.append(f"{len(rows)} entries, at most {MAX_ENTRIES}: fix a flake before quarantining another")
    entries: list[Entry] = []
    for index, row in enumerate(rows, 1):
        found: list[str] = []
        entry = _entry(row, index, found)
        problems += found
        if entry:
            overlap = next((e for e in entries if e.matches(entry.test) or entry.matches(e.test)), None)
            if overlap:
                problems.append(f"entry {index} ({entry.test}): overlaps {overlap.test}")
            entries.append(entry)
    if problems:
        raise RegistryError(problems)
    return entries


def load(path: Path = REGISTRY) -> list[Entry]:
    """No registry file is an empty registry."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return parse(text)


def problems(entries: Sequence[Entry], on: dt.date) -> list[str]:
    """What fails the required registry job besides the format: dates past `until` or in the future."""
    found = []
    for entry in entries:
        if on > entry.until:
            found.append(
                f"{entry.test}: past until {entry.until} (bead {entry.bead}); it runs in the required jobs again: "
                "fix the test or remove the entry"
            )
        elif on < entry.added:
            found.append(f"{entry.test}: added {entry.added} is in the future")
    return found


def quarantined(nodeid: str, entries: Sequence[Entry], on: dt.date) -> Entry | None:
    """The entry in date that takes this test out of the required jobs, if any."""
    return next((entry for entry in entries if entry.active(on) and entry.matches(nodeid)), None)


def describe(entry: Entry, on: dt.date) -> str:
    """One `list` line: bead, test, age and days left."""
    left = (entry.until - on).days
    state = f"{left} day(s) left" if left >= 0 else f"OVERDUE by {-left} day(s)"
    return (
        f"{entry.bead}  {entry.test}  added {entry.added} ({(on - entry.added).days} d)  until {entry.until}: {state}"
    )


def plan(entries: Sequence[Entry], on: dt.date) -> dict[str, bool]:
    """Whether the lane has anything to run, and whether an end-to-end entry needs browsers."""
    active = [entry for entry in entries if entry.active(on)]
    return {"selected": bool(active), "browsers": any(entry.test.startswith("tests/e2e/") for entry in active)}


Runner = Callable[[list[str], dict[str, str]], int]


SESSION_SECONDS = 300  # per attempt: three attempts stay inside the job's 20 minutes


def _pytest(args: list[str], env: dict[str, str]) -> int:
    command = [sys.executable, "-m", "pytest", *args, "-p", "no:cacheprovider", "-q", "-rA"]
    command.append(f"--session-timeout={SESSION_SECONDS}")
    try:
        done = subprocess.run(command, cwd=ROOT, env=env, check=False, timeout=SESSION_SECONDS + 60)  # noqa: S603
    except subprocess.TimeoutExpired:
        return 124  # a hung session: every node it was asked for counts as failed
    return done.returncode


def _outcomes(path: Path) -> dict[str, str]:
    """Nodeid -> passed|failed|skipped from the plugin's JSON lines; a failure in any phase is a failure."""
    found: dict[str, str] = {}
    if not path.is_file():
        return found
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        nodeid, outcome = row.get("nodeid"), row.get("outcome")
        if not isinstance(nodeid, str) or outcome not in {"passed", "failed", "skipped"}:
            continue
        if outcome == "failed" or found.get(nodeid) is None or (found[nodeid] == "skipped" and outcome == "passed"):
            found[nodeid] = outcome
    return found


def _status(attempts: list[str]) -> str:
    if not attempts:
        return "not-collected"
    if attempts[-1] == "passed":
        return "pass" if len(attempts) == 1 else "flaky"
    return "skipped" if attempts[-1] == "skipped" else "fail"


def run(
    entries: Sequence[Entry], on: dt.date, attempts: int = MAX_ATTEMPTS, runner: Runner = _pytest
) -> dict[str, object]:
    """Run the entries in date; each failing node is retried by node id, up to `attempts` tries in all."""
    attempts = max(1, min(attempts, MAX_ATTEMPTS))
    active = [entry for entry in entries if entry.active(on)]
    history: dict[str, list[str]] = {}
    targets = sorted({entry.path for entry in active})
    with tempfile.TemporaryDirectory(prefix="quarantine-") as scratch:
        for attempt in range(1, attempts + 1):
            if not targets:
                break
            log = Path(scratch) / f"attempt-{attempt}.jsonl"
            env = {**os.environ, "HZ_QUARANTINE": "only", "HZ_QUARANTINE_REPORT": str(log)}
            code = runner(targets, env)
            outcomes = _outcomes(log)
            if not outcomes and code not in (0, 5):  # the session itself broke: every node it was asked for failed
                named = [target for target in targets if "::" in target] or [entry.test for entry in active]
                outcomes = dict.fromkeys(named, "failed")
            for nodeid, outcome in outcomes.items():
                history.setdefault(nodeid, []).append(outcome)
            targets = sorted(nodeid for nodeid, outcome in outcomes.items() if outcome == "failed")
    rows = []
    for entry in entries:
        nodes = {nodeid: tries for nodeid, tries in history.items() if entry.matches(nodeid)}
        if not entry.active(on):
            status = "overdue" if on > entry.until else "not-started"
        elif not nodes:
            status = "not-collected"
        else:
            statuses = {_status(tries) for tries in nodes.values()}
            status = next(s for s in ("fail", "flaky", "skipped", "pass") if s in statuses or s == "pass")
        rows.append(
            {
                "test": entry.test,
                "bead": entry.bead,
                "added": entry.added.isoformat(),
                "until": entry.until.isoformat(),
                "status": status,
                "nodes": nodes,
            }
        )
    return {
        "schema": 1,
        "date": on.isoformat(),
        "os": os.environ.get("RUNNER_OS", sys.platform),
        "sha": os.environ.get("GITHUB_SHA", ""),
        "ref": os.environ.get("GITHUB_REF", ""),
        "event": os.environ.get("GITHUB_EVENT_NAME", ""),
        "attempts": attempts,
        "entries": rows,
    }


def summary(report: dict[str, object]) -> str:
    """The Markdown job summary: one row per entry with its result and the tries of every failing node."""
    rows = report["entries"] if isinstance(report["entries"], list) else []
    lines = [
        f"## Quarantine lane ({report['os']})",
        "",
        (
            "Non-blocking: these tests are out of the required jobs (`tests/quarantine.toml`, at most "
            f"{MAX_DAYS} days each); a failing test was retried up to {report['attempts']} times."
        ),
        "",
    ]
    if not rows:
        return "\n".join([*lines, "No entries."]) + "\n"
    lines += ["| Test | Bead | Until | Result | Tries |", "| --- | --- | --- | --- | --- |"]
    for row in rows:
        failed = {node: t for node, t in row["nodes"].items() if "failed" in t}
        tries = "; ".join(f"{node.partition('::')[2]}: {' '.join(t)}" for node, t in failed.items())
        tries = tries or (f"{len(row['nodes'])} node(s) passed first time" if row["nodes"] else "")
        lines.append(f"| `{row['test']}` | {row['bead']} | {row['until']} | {row['status']} | {tries or '-'} |")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None, on: dt.date | None = None) -> int:
    """Parse the command line and run one command; `on` replaces today's date in tests."""
    parser = argparse.ArgumentParser(prog="quarantine.py", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    commands.add_parser("list")
    commands.add_parser("plan")
    runs = commands.add_parser("run")
    runs.add_argument("--attempts", type=int, default=MAX_ATTEMPTS)
    runs.add_argument("--report", type=Path)
    runs.add_argument("--summary", type=Path)
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    arguments = parser.parse_args(argv)
    on = on or today()
    try:
        entries = load(arguments.registry)
    except RegistryError as error:
        print(f"quarantine: invalid registry\n{error}", file=sys.stderr)
        return 2 if arguments.command == "run" else 1
    return COMMANDS[arguments.command](entries, on, arguments)


def _check(entries: Sequence[Entry], on: dt.date, _: argparse.Namespace) -> int:
    found = problems(entries, on)
    for line in found:
        print(f"quarantine: {line}", file=sys.stderr)
    if not found:
        print(f"quarantine: {len(entries)}/{MAX_ENTRIES} entries, all in date")
    return 1 if found else 0


def _list(entries: Sequence[Entry], on: dt.date, _: argparse.Namespace) -> int:
    for entry in entries:
        print(describe(entry, on))
    return 0


def _plan(entries: Sequence[Entry], on: dt.date, _: argparse.Namespace) -> int:
    for key, value in plan(entries, on).items():
        print(f"{key}={str(value).lower()}")
    return 0


def _run(entries: Sequence[Entry], on: dt.date, arguments: argparse.Namespace) -> int:
    report = run(entries, on, arguments.attempts)
    text = summary(report)
    print(text)
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if arguments.summary:
        arguments.summary.parent.mkdir(parents=True, exist_ok=True)
        with arguments.summary.open("a", encoding="utf-8") as handle:
            handle.write(text)
    return 0


COMMANDS: dict[str, Callable[[Sequence[Entry], dt.date, argparse.Namespace], int]] = {
    "check": _check,
    "list": _list,
    "plan": _plan,
    "run": _run,
}


if __name__ == "__main__":
    sys.exit(main())
