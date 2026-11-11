"""Sample only a slow probe; emit constant labels and numeric timings."""

import contextlib
import os
import re
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest

PROBE = "heinzel.heads.mcp._http_probe"
TARGET = "test_check_initializes_and_lists_actual_authenticated_server"
FAKE = "test_fake_auth_rejection"
PATTERNS = (
    ("import", r"\b(?:importlib|_find_and_load|_load_unlocked)\b"),
    ("network", r"\b(?:connect|recv|send|read|write)(?:\$\w+)?\b"),
    ("wait", r"\b(?:poll|select|kevent|semaphore_wait_trap)\b"),
)


def classify(raw: bytes) -> str:
    """Discard raw samples and return only fixed categories."""
    text = raw.decode("utf-8", "replace").lower()
    categories = [label for label, pattern in PATTERNS if re.search(pattern, text)]
    return "_".join(categories) if categories else "other"


def record_negative_clock(phase: str) -> None:
    """Record an optional monotonic timestamp without changing the probe result."""
    with contextlib.suppress(OSError):
        (Path(os.environ["VNXD_PHASE_DIR"]) / f"negative.{phase}").write_text(str(time.monotonic_ns()))


def collect_sample(  # noqa: PLR0913 - explicit state for bounded sampler cleanup
    pid: int,
    phase_dir: Path,
    done: threading.Event,
    lock: threading.Lock,
    observed: dict[str, Any],
    *,
    launcher: Callable[..., subprocess.Popen[bytes]],
) -> None:
    """Keep the raw report in a private directory and remove it after reaping."""
    try:
        info = phase_dir.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return
        with tempfile.TemporaryDirectory(prefix="sample-", dir=phase_dir) as private:
            report = Path(private) / "report.txt"
            with lock:
                if done.is_set():
                    return
                sampler = launcher(
                    ["/usr/bin/sample", str(pid), "1", "1", "-file", str(report)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                observed["sampler"] = sampler
            try:
                sampler.communicate(timeout=2)
                if sampler.returncode == 0:
                    observed["sample"] = classify(report.read_bytes())
            finally:
                if sampler.poll() is None:
                    sampler.kill()
                    sampler.communicate()
    except (OSError, subprocess.SubprocessError):
        return


def phase_record(case: str, phase: str, outcome: str, exc: BaseException | None, duration: float) -> str:
    """Reduce a pytest phase to fixed labels; never return exception contents."""
    kind = "none"
    if outcome == "failed":
        if exc is not None and type(exc).__name__ == "Failed" and str(exc).startswith("Timeout (>5.0s)"):
            kind = "timeout"
        elif isinstance(exc, AssertionError):
            kind = "assertion"
        else:
            kind = "exception" if exc is not None else "unknown"
    return f"VNXD case={case} phase={phase} outcome={outcome} kind={kind} duration_ms={max(0, round(duration * 1000))}"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[Any]) -> Generator[None, Any, None]:
    """Persist only the outcome of each requested case and test phase."""
    result = yield
    if item.nodeid.endswith("companion_test.py::test_cpu_companion"):
        if call.when == "call":
            report = result.get_result()
            if report.outcome in ("passed", "failed", "skipped"):
                directory = Path(os.environ["VNXD_PHASE_DIR"])
                (directory / "companion.call").write_text(report.outcome)
                (directory / "worker.companion").write_text(os.environ.get("PYTEST_XDIST_WORKER", ""))
        return
    if (TARGET not in item.nodeid and FAKE not in item.nodeid) or call.when not in ("setup", "call", "teardown"):
        return
    report = result.get_result()
    if report.outcome not in ("passed", "failed", "skipped"):
        return
    case = "fake" if FAKE in item.nodeid else "false" if "[False]" in item.nodeid else "true"
    exception = call.excinfo.value if call.excinfo is not None else None
    line = phase_record(case, call.when, report.outcome, exception, report.duration)
    directory = Path(os.environ["VNXD_PHASE_DIR"])
    (directory / f"{case}.{call.when}").write_text(line + "\n")
    if call.when == "setup":
        (directory / f"worker.{case}").write_text(os.environ.get("PYTEST_XDIST_WORKER", ""))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, Any, None]:  # noqa: C901
    """Observe the probe without changing its arguments, streams or timeout."""
    if TARGET not in item.nodeid and FAKE not in item.nodeid:
        yield
        return
    case = "fake" if FAKE in item.nodeid else "false" if "[False]" in item.nodeid else "true"
    original = subprocess.Popen
    done = threading.Event()
    lock = threading.Lock()
    observed: dict[str, Any] = {"start": None, "sample": "not_needed", "elapsed": 0, "sampler": None, "fd": None}

    def mark(event: str) -> None:
        fd = observed["fd"]
        if fd is not None:
            with contextlib.suppress(OSError):
                os.write(fd, f"{event} {time.monotonic_ns()}\n".encode("ascii"))

    threads: list[threading.Thread] = []

    def sample(pid: int) -> None:
        if done.wait(2):
            return
        observed["sample"] = "unavailable"
        collect_sample(pid, Path(os.environ["VNXD_PHASE_DIR"]), done, lock, observed, launcher=original)

    class Observed(original):  # type: ignore[valid-type,misc]
        def __init__(self, args: Any, *a: Any, **kw: Any) -> None:  # noqa: ANN401, C901
            matches = isinstance(args, (tuple, list)) and list(args[1:4]) == ["-I", "-m", PROBE]
            if matches:
                observed["start"] = time.monotonic()
            if matches and len(args) == len(("python", "-I", "-m", PROBE, "url")):
                try:
                    path = Path(os.environ["VNXD_PHASE_DIR"]) / f"{case}.events"
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)
                    observed["fd"] = fd
                    bootstrap = (Path(__file__).parent / "bootstrap.py").read_text()
                except OSError:
                    if observed["fd"] is not None:
                        os.close(observed["fd"])
                        observed["fd"] = None
                else:
                    observed["fd"] = fd
                    mark("launch.before")
                    args = [args[0], "-I", "-c", bootstrap, str(fd), args[-1]]
                    kw["pass_fds"] = (*kw.get("pass_fds", ()), fd)
            try:
                super().__init__(args, *a, **kw)
            except BaseException:
                if observed["fd"] is not None:
                    os.close(observed["fd"])
                    observed["fd"] = None
                raise
            if matches:
                mark("launch.after")
                if case == "false":
                    record_negative_clock("start")
                thread = threading.Thread(target=sample, args=(self.pid,), daemon=True)
                try:
                    thread.start()
                except RuntimeError:
                    observed["sample"] = "unavailable"
                else:
                    threads.append(thread)

        def communicate(self, *a: Any, **kw: Any) -> Any:  # noqa: ANN401
            try:
                mark("communicate.before")
                return super().communicate(*a, **kw)
            finally:
                if observed["start"] is not None:
                    mark("communicate.after")
                    observed["elapsed"] = round((time.monotonic() - observed["start"]) * 1000)
                    if "[False]" in item.nodeid:
                        record_negative_clock("end")
                    done.set()

    subprocess.Popen = Observed
    try:
        yield
    finally:
        done.set()
        subprocess.Popen = original
        with lock:
            sampler = observed["sampler"]
            if sampler is not None and sampler.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    sampler.kill()
        for thread in threads:
            thread.join(timeout=1)
        if observed["fd"] is not None:
            os.close(observed["fd"])
        if observed["start"] is not None:
            line = f"VNXD case={case} elapsed_ms={observed['elapsed']} sample={observed['sample']}"
            (Path(os.environ["VNXD_PHASE_DIR"]) / case).write_text(line + "\n")
