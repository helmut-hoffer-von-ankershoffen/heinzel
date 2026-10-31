# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Run named steps quietly: one line per passing step, the tail of the output of a failing one.

Make targets call this so that people and agents read `✓ lint 3.1s` instead of pages of tool output, and so that a
failure is never hidden behind a pipe: the exit code is the failing step's.

A step prefixed with `&` runs concurrently with the step before it, so `"a=…" "&b=…" "&c=…" "d=…"` runs a, b and c
together, then d. Results print in the declared order; the first failure cancels the rest of its group and stops.
Every step is killed after `STEP_TIMEOUT` seconds (default 600). `VERBOSE=1` runs everything in order and streams.

Usage: uv run --script scripts/steps.py "name=command" ["&name=command" ...]
"""

import contextlib
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import IO

TAIL = 40
POLL = 0.05
STOP_GRACE = 0.5
REAP_TIMEOUT = 2.0
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# `uv run --script` points VIRTUAL_ENV at this script's own environment; steps run project commands (`uv run ...`),
# which would warn on every step that it doesn't match the project's .venv.
ENV = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"} | {
    "NO_COLOR": "1",
    "FORCE_COLOR": "",
    "UV_NO_PROGRESS": "1",
}


@dataclass
class Step:
    """A named shell command and, once started, its process."""

    name: str
    command: str
    output: IO[str] = field(default_factory=lambda: tempfile.TemporaryFile("w+", errors="replace"))  # noqa: SIM115 - lives as long as the step
    process: subprocess.Popen[str] | None = None
    started: float = 0.0
    seconds: float = 0.0
    code: int | None = None
    note: str = ""

    def start(self, *, verbose: bool = False) -> None:
        """Start the command in its own process group, so cancelling it also stops what it spawned."""
        self.started = time.monotonic()
        self.process = subprocess.Popen(  # noqa: S602 - commands come from the Makefile
            self.command,
            shell=True,
            env=ENV,
            stdout=None if verbose else self.output,
            stderr=None if verbose else subprocess.STDOUT,
            start_new_session=True,
            text=True,
        )

    def poll(self, timeout: float) -> bool:
        """Whether the step has finished; kills it once it runs longer than `timeout`."""
        if self.code is not None or self.process is None:
            return self.code is not None
        self.seconds = time.monotonic() - self.started
        if (code := self.process.poll()) is not None:
            self.code = code
        elif self.seconds > timeout:
            self.stop(f"timed out after {timeout:.0f}s", code=124)
        return self.code is not None

    def stop(self, note: str, code: int = -1) -> None:
        """Stop this step's owned process group; 124 marks a timeout."""
        stop_steps([self], note, code)

    def report(self) -> None:
        """One line, plus the tail of the output for a failure."""
        if self.process is None:
            print(f"- {self.name} cancelled", flush=True)
            return
        mark = "✓" if self.code == 0 else "✗" if self.note != "cancelled" else "-"
        print(f"{mark} {self.name} {self.seconds:.1f}s{f' ({self.note})' if self.note else ''}", flush=True)
        if self.code and self.note != "cancelled":
            self.output.seek(0)
            lines = ANSI.sub("", self.output.read()).rstrip().splitlines()
            if (hidden := len(lines) - TAIL) > 0:
                print(f"  … {hidden} earlier lines; rerun with VERBOSE=1 or run: {self.command}")
            if lines:
                print("\n".join(f"  {line}" for line in lines[-TAIL:]), flush=True)


def groups(argv: list[str]) -> list[list[Step]]:
    """Parse the arguments into groups of steps that run together.

    Raises:
        ValueError: If an argument is not `name=command`.
    """
    result: list[list[Step]] = []
    for arg in argv:
        concurrent = arg.startswith("&")
        name, _, command = arg.removeprefix("&").partition("=")
        if not command:
            msg = f"Not a step (want name=command): {shlex.quote(arg)}"
            raise ValueError(msg)
        if concurrent and result:
            result[-1].append(Step(name, command))
        else:
            result.append([Step(name, command)])
    return result


class Interruption:
    """Record the first termination signal without interrupting process creation or cleanup."""

    def __init__(self) -> None:
        """Start with no termination request."""
        self.signum = 0

    def receive(self, signum: int, _frame: object) -> None:
        """Record only the first termination signal."""
        if not self.signum:
            self.signum = signum

    def exit_code(self) -> int:
        """Return a conventional signal exit code, or zero if not interrupted."""
        return 128 + self.signum if self.signum else 0


def stop_steps(steps: list[Step], note: str, code: int = -1) -> None:
    """Stop only unreaped owned child process groups, then reap each with a deadline."""
    active = [step for step in steps if step.process is not None and step.process.returncode is None]
    for step in active:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(step.process.pid, signal.SIGTERM)  # type: ignore[union-attr]
    if active:
        # Keep each direct child unreaped while signalling, so its process-group ID cannot be reused.
        time.sleep(STOP_GRACE)
    for step in active:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(step.process.pid, signal.SIGKILL)  # type: ignore[union-attr]
    for step in active:
        try:
            step.process.wait(timeout=REAP_TIMEOUT)  # type: ignore[union-attr]
        except subprocess.TimeoutExpired:
            print(f"Could not confirm that {step.name} stopped.", file=sys.stderr, flush=True)
        step.code, step.note = code, note


def run_group(group: list[Step], timeout: float, interruption: Interruption, *, verbose: bool = False) -> int:  # noqa: C901 - bounded child lifecycle
    """Run owned steps together and cancel their process groups on failure or termination."""
    try:
        for step in group:
            if interruption.signum:
                break
            if verbose:
                print(f"--- {step.name}: {step.command}", flush=True)
            step.start(verbose=verbose)
        reported = 0
        while reported < len(group):
            if interruption.signum:
                stop_steps(group, "interrupted", 128 + interruption.signum)
                return 128 + interruption.signum
            for step in group:
                step.poll(timeout)
            if any(step.code for step in group):
                stop_steps([step for step in group if step.code is None], "cancelled")
            while reported < len(group) and group[reported].code is not None:
                if not verbose:
                    group[reported].report()
                reported += 1
            time.sleep(POLL)
        return next((step.code for step in group if step.code and step.note != "cancelled"), 0) or 0
    except BaseException:
        stop_steps(group, "interrupted")
        raise


def main(argv: list[str]) -> int:
    """Run groups in order, stopping their owned children on SIGINT or SIGTERM."""
    try:
        plan = groups(argv)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    timeout = float(os.environ.get("STEP_TIMEOUT", "600"))
    interruption = Interruption()
    handlers = {sig: signal.signal(sig, interruption.receive) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        verbose = os.environ.get("VERBOSE", "") not in {"", "0"}
        execution = [[step] for group in plan for step in group] if verbose else plan
        for index, group in enumerate(execution):
            if interrupted := interruption.exit_code():
                return interrupted
            if code := run_group(group, timeout, interruption, verbose=verbose):
                if interrupted := interruption.exit_code():
                    return interrupted
                if not verbose:
                    for later in execution[index + 1 :]:
                        for step in later:
                            step.report()
                return code
        return interruption.exit_code()
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
