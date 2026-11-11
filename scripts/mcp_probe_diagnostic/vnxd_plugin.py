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


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, Any, None]:  # noqa: C901
    """Observe the probe without changing its arguments, streams or timeout."""
    if TARGET not in item.nodeid:
        yield
        return
    original = subprocess.Popen
    done = threading.Event()
    lock = threading.Lock()
    observed: dict[str, Any] = {"start": None, "sample": "not_needed", "elapsed": 0, "sampler": None}
    threads: list[threading.Thread] = []

    def sample(pid: int) -> None:
        if done.wait(2):
            return
        observed["sample"] = "unavailable"
        collect_sample(pid, Path(os.environ["VNXD_PHASE_DIR"]), done, lock, observed, launcher=original)

    class Observed(original):  # type: ignore[valid-type,misc]
        def __init__(self, args: Any, *a: Any, **kw: Any) -> None:  # noqa: ANN401
            super().__init__(args, *a, **kw)
            if isinstance(args, (tuple, list)) and list(args[1:4]) == ["-I", "-m", PROBE]:
                observed["start"] = time.monotonic()
                thread = threading.Thread(target=sample, args=(self.pid,), daemon=True)
                try:
                    thread.start()
                except RuntimeError:
                    observed["sample"] = "unavailable"
                else:
                    threads.append(thread)

        def communicate(self, *a: Any, **kw: Any) -> Any:  # noqa: ANN401
            try:
                return super().communicate(*a, **kw)
            finally:
                if observed["start"] is not None:
                    observed["elapsed"] = round((time.monotonic() - observed["start"]) * 1000)
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
        valid = "false" if "[False]" in item.nodeid else "true"
        line = f"VNXD case={valid} elapsed_ms={observed['elapsed']} sample={observed['sample']}"
        (Path(os.environ["VNXD_PHASE_DIR"]) / valid).write_text(line + "\n")
