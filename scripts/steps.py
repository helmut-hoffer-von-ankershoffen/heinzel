"""Run named steps quietly: one line per passing step, the tail of the output of a failing one.

Make targets call this so that people and agents read `✓ lint 3.1s` instead of pages of tool output, and so that a
failure is never hidden behind a pipe: the exit code is the failing step's.

A step prefixed with `&` runs concurrently with the step before it, so `"a=…" "&b=…" "&c=…" "d=…"` runs a, b and c
together, then d. Results print in the declared order; the first failure cancels the rest of its group and stops.
Every step is killed after `STEP_TIMEOUT` seconds (default 600). `VERBOSE=1` runs everything in order and streams.

Usage: python scripts/steps.py "name=command" ["&name=command" ...]
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
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
ENV = os.environ | {"NO_COLOR": "1", "FORCE_COLOR": "", "UV_NO_PROGRESS": "1"}


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

    def start(self) -> None:
        """Start the command in its own process group, so cancelling it also stops what it spawned."""
        self.started = time.monotonic()
        self.process = subprocess.Popen(  # noqa: S602 - commands come from the Makefile
            self.command, shell=True, env=ENV, stdout=self.output, stderr=subprocess.STDOUT, start_new_session=True
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
        """Kill the step's process group; 124 marks a timeout, as with timeout(1)."""
        if self.process is not None and self.process.poll() is None:
            # The step may exit between poll() and killpg(); macOS then reports EPERM instead of ESRCH.
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()
        self.code, self.note = code, note

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


def run_group(group: list[Step], timeout: float) -> int:
    """Run the steps together, report them in order, cancel the rest on the first failure."""
    for step in group:
        step.start()
    reported = 0
    while reported < len(group):
        for step in group:
            step.poll(timeout)
        if any(step.code for step in group):  # a failure: stop whatever still runs
            for step in group:
                if step.code is None:
                    step.stop("cancelled")
        while reported < len(group) and group[reported].code is not None:
            group[reported].report()
            reported += 1
        time.sleep(POLL)
    return next((step.code for step in group if step.code and step.note != "cancelled"), 0) or 0


def main(argv: list[str]) -> int:
    """Run the groups in order and stop at the first failure."""
    try:
        plan = groups(argv)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    timeout = float(os.environ.get("STEP_TIMEOUT", "600"))
    if os.environ.get("VERBOSE", "") not in {"", "0"}:
        for step in (step for group in plan for step in group):
            print(f"--- {step.name}: {step.command}", flush=True)
            if code := subprocess.run(step.command, shell=True, env=ENV, check=False, timeout=timeout).returncode:  # noqa: S602
                return code
        return 0
    for index, group in enumerate(plan):
        if code := run_group(group, timeout):
            for later in plan[index + 1 :]:
                for step in later:
                    step.report()
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
