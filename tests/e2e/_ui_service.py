"""Owned child lifecycle diagnostics without installed-tool fixture imports."""

import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from itertools import islice
from pathlib import Path

import pytest

from heinzel.kernel import HeinzelError

_TRACEBACK_FILES = {
    "__init__.py",
    "__main__.py",
    "_boot.py",
    "_config.py",
    "_lifecycle.py",
    "_private_control.py",
    "_serve.py",
    "_shutdown.py",
    "_supervision.py",
    "server.py",
    "threading.py",
    "type_adapter.py",
}

_TRACEBACK_FUNCTIONS = {
    "__init__",
    "_read",
    "_run",
    "handle",
    "load",
    "private_directory",
    "private_read",
    "private_write",
    "publish",
    "request",
    "run",
    "serve",
    "start",
    "ui",
    "when_started",
}

_TRACEBACK_ERRORS = {
    "AssertionError",
    "AttributeError",
    "Exception",
    "FileNotFoundError",
    "HeinzelError",
    "ImportError",
    "KeyError",
    "ModuleNotFoundError",
    "NameError",
    "OSError",
    "PermissionError",
    "RecursionError",
    "RuntimeError",
    "TimeoutError",
    "TypeError",
    "ValidationError",
    "ValueError",
}
_NATIVE_SYMBOLS = {
    "__accept",
    "__psynch_cvwait",
    "__select",
    "_pthread_cond_wait",
    "_PyEval_Vector",
    "_TAIL_CALL_CALL",
    "PyImport_ImportModuleLevelObject",
    "_PyImport_ImportModuleLevelObject",
    "accept",
    "dlopen",
    "fdatasync",
    "fsync",
    "kevent",
    "kevent_qos",
    "lstat",
    "open",
    "poll",
    "pthread_join",
    "read",
    "select",
    "stat",
    "time_sleep",
    "write",
    "write_nocancel",
}


def _sample_role(line):
    """Map native thread labels to fixed roles without returning their text."""
    if "com.apple.main-thread" in line:
        return "main"
    return "publication" if "(when_started)" in line else "other"


class RunningService:
    """Only the processes this fixture creates may be killed or joined."""

    def __init__(self, service, manager, command, environment):
        self.service, self.manager = service, manager
        self.command, self.environment = command, environment
        self.processes = []
        self.original_control = service._control
        self.control_failure = None
        self.control_failure_count = 0
        self.ready_started = 0.0
        service._control = self.observe_control

    def observe_control(self, record, pid, command):
        """Record only safe metadata from an existing failed status call, then re-raise unchanged."""
        try:
            return self.original_control(record, pid, command)
        except (OSError, ValueError, HeinzelError) as exc:
            if command == "status":
                frames = deque(maxlen=4)
                trace = exc.__traceback__
                while trace is not None:
                    name = trace.tb_frame.f_code.co_name
                    frames.append((name if name.isascii() and name.isidentifier() else "unknown", trace.tb_lineno))
                    trace = trace.tb_next
                self.control_failure_count += 1
                name = type(exc).__name__
                number = exc.errno if isinstance(exc, OSError) else None
                self.control_failure = {
                    "exception": name if name.isascii() and name.isidentifier() else "unknown",
                    "errno": number if type(number) is int else None,
                    "frames": list(frames),
                    "elapsed": round(time.monotonic() - self.ready_started, 3),
                }
            raise

    def launch(self):
        process = subprocess.Popen(self.command, env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.processes.append(process)
        self.manager.pid = process.pid
        return process

    def ready(self, previous=None):
        status = None
        deadline = time.monotonic() + 6
        self.ready_started = deadline - 6
        self.control_failure = None
        self.control_failure_count = 0
        changed = threading.Event()
        while time.monotonic() < deadline:
            status = self.service.status()
            if status.ready and status.instance != previous:
                return status
            assert self.processes[-1].poll() is None, self.processes[-1].communicate(timeout=1)
            changed.wait(0.02)
        pytest.fail(
            "The owned web server did not become ready with a new lifecycle instance; "
            f"last status={status.model_dump_json() if status is not None else 'not sampled'}, "
            f"owned process exit={self.processes[-1].poll()}, wait channel={self.wait_channel()}, "
            f"control failure={self.control_failure}, failed status calls={self.control_failure_count}, "
            f"publication evidence={self.publication_evidence()}."
        )

    def publication_evidence(self):
        """Observe only after the unchanged readiness deadline has already failed."""
        pending = list(islice(self.service.directory.glob(".runtime.json.*"), 4))
        result = {"runtime_present": self.service.runtime.exists(), "pending_writes": len(pending)}
        result["native"] = self.native_state()
        result["runtime_after_sample"] = self.service.runtime.exists()
        return result

    def native_state(self):
        """Sample only the fixture-created child and emit static role/symbol labels, never raw reports."""
        process = self.processes[-1]
        pid = getattr(process, "pid", None)
        if sys.platform != "darwin" or type(pid) is not int or pid <= 0 or process.poll() is not None:
            return {"result": "unavailable"}
        try:
            with tempfile.TemporaryDirectory(prefix="hz-owned-sample-") as directory:
                path = Path(directory) / "sample.txt"
                sample = subprocess.run(
                    ["/usr/bin/sample", str(pid), "1", "20", "-file", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=4,
                    check=False,
                )
                if sample.returncode:
                    return {"result": "unavailable"}
                with path.open("rb") as report:
                    threads = self.sample_metadata(report.read(64 * 1024).decode("utf-8", errors="replace"))
        except (OSError, subprocess.TimeoutExpired):
            return {"result": "unavailable"}
        return {"result": "sampled", "threads": threads}

    @staticmethod
    def sample_metadata(report):
        """Drop report headers, paths, addresses and arbitrary symbols from a bounded native sample."""
        threads = []
        current = None
        for line in report.splitlines():
            if line.startswith(("Total number", "Binary Images:")):
                break
            if re.match(r"^\s*\d+\s+Thread_\d+", line):
                if len(threads) == 16:
                    break
                current = {"role": _sample_role(line), "symbols": set()}
                threads.append(current)
            elif current is not None:
                symbol = re.match(r"^[\s+!:|]*\d+\s+([A-Za-z_]\w*)", line)
                if symbol is not None and symbol[1] in _NATIVE_SYMBOLS:
                    current["symbols"].add(symbol[1])
        return [{"role": thread["role"], "symbols": sorted(thread["symbols"])} for thread in threads]

    def wait_channel(self):
        """Read only the owned child's bounded kernel wait symbol when Linux provides it."""
        try:
            with (Path("/proc") / str(self.processes[-1].pid) / "wchan").open(
                encoding="ascii", errors="replace"
            ) as handle:
                value = handle.read(128).strip()
        except OSError:
            return "unavailable"
        return value if value.isidentifier() or value == "0" else "unknown"

    def close(self):
        self.service._control = self.original_control
        session_url_emitted = False
        tracebacks = []
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            try:
                output, errors = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                output, errors = process.communicate(timeout=3)
            session_url_emitted |= b"#token=" in output + errors
            metadata = self.traceback_metadata(output + errors)
            if metadata is not None:
                tracebacks.append(metadata)
        assert not session_url_emitted, "The owned web server emitted a session URL."
        assert not tracebacks, f"The owned web server emitted an unexpected traceback: {tracebacks}"

    @staticmethod
    def traceback_metadata(output):
        """Expose only known source labels from a failed child, never its traceback text or locals."""
        if b"Traceback (most recent call last)" not in output:
            return None
        sample = output[-16 * 1024 :]
        classes = []
        for encoded in re.findall(rb"(?m)^([A-Za-z_][\w.]{0,127}):", sample):
            name = encoded.rsplit(b".", 1)[-1].decode("ascii")
            classes.append(name if name in _TRACEBACK_ERRORS else "other")
        frames = []
        pattern = rb'File "([^"\n]{1,512})", line ([1-9]\d{0,6}), in ([A-Za-z_]\w{0,127}|<module>)'
        for path, number, encoded_function in re.findall(pattern, sample):
            name = Path(path.decode("ascii", errors="replace")).name
            function = encoded_function.decode("ascii")
            frames.append(
                (
                    name if name in _TRACEBACK_FILES else "other",
                    int(number),
                    function if function in _TRACEBACK_FUNCTIONS else "other",
                )
            )
        return {"classes": classes[-4:], "frames": frames[-4:]}
