"""Owned child lifecycle diagnostics without installed-tool fixture imports."""

import math
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
_STARTUP_MODULES = {
    "_bootstrap",
    "_bootstrap_external",
    "site.py",
    "sitecustomize.py",
    "heinzel/__main__.py",
    "heinzel/heads/cli/__init__.py",
    "heinzel/heads/cli/_serve.py",
    "heinzel/heads/web/__init__.py",
    "heinzel/heads/web/_supervision.py",
    "heinzel/kernel/_boot.py",
    "heinzel/kernel/_discovery.py",
    "heinzel/kernel/_kernel.py",
    "heinzel/kernel/_private_control.py",
    "typer/main.py",
    "fastapi/routing.py",
    "pydantic/_internal/_generate_schema.py",
    "inspect.py",
    "threading.py",
    "uvicorn/server.py",
    "asyncio/base_events.py",
    "selectors.py",
}


# Fixed public identifiers, never names discovered from the installed SDK or a traceback. Builtin discovery
# imports each registration package and its model/service/render implementation before publishing readiness.
_STARTUP_BUILTINS = (
    "ai",
    "ai/access",
    "graph",
    "heinzel",
    "org",
    "system",
    "system/agent",
    "system/capability",
    "system/changelog",
    "system/claude",
    "system/config",
    "system/connector",
    "system/credential",
    "system/daemon",
    "system/directory",
    "system/doctor",
    "system/expert",
    "system/layer",
    "system/lifecycle",
    "system/notify",
    "system/plugin",
    "system/plugin/docs",
    "system/runner",
    "system/runner/chat",
    "system/skill",
    "system/sound",
    "system/state",
    "system/theme",
    "system/ui",
    "team",
    "telemetry",
    "ui",
)
_STARTUP_MODULES.update(
    f"heinzel/modules/{package}/{filename}"
    for package in _STARTUP_BUILTINS
    for filename in ("__init__.py", "_models.py", "_service.py", "_render.py")
)
_STARTUP_MODULES.update(
    {
        "heinzel/__init__.py",
        "heinzel/kernel/__init__.py",
        "heinzel/kernel/_agents.py",
        "heinzel/kernel/_bindings.py",
        "heinzel/kernel/_contributions.py",
        "heinzel/kernel/_lets.py",
        "heinzel/kernel/_module.py",
        "heinzel/kernel/_protocols.py",
        "heinzel/modules/heinzel/_autonomous_schedule.py",
        "heinzel/modules/heinzel/_session_crud.py",
        "heinzel/modules/heinzel/_session_models.py",
        "heinzel/modules/heinzel/_session_operations.py",
        "heinzel/modules/system/plugin/docs/_build.py",
        "dataclasses.py",
        "enum.py",
        "typing.py",
        "runpy.py",
        "pydantic/main.py",
        "pydantic/fields.py",
        "pydantic/_internal/_model_construction.py",
        "pydantic/_internal/_typing_extra.py",
        "pydantic/_internal/_fields.py",
        "pydantic/_internal/_decorators.py",
        "rich/markdown.py",
        "rich/syntax.py",
        "pygments/__init__.py",
        "pygments/lexer.py",
        "pygments/lexers/__init__.py",
        "markdown_it/__init__.py",
        "markdown_it/main.py",
        "markdown_it/parser_inline.py",
        "markdown_it/parser_block.py",
    }
)


def startup_diagnostics(prefix, directory):
    """Capture one early stack only inside this fixture's isolated SDK, never the owner's install."""
    packages = list((prefix / "lib").glob("python*/site-packages"))
    assert len(packages) == 1
    assert packages[0].resolve().is_relative_to(prefix.resolve()), "Startup diagnostics must stay in the fixture SDK."
    target = packages[0] / "sitecustomize.py"
    assert not target.exists(), "The fixture must not replace existing startup code."
    assert not target.is_symlink(), "The fixture must not replace an existing startup link."
    destination = directory / "startup-stacks.txt"
    descriptor = destination.open("xb")
    descriptor.close()
    destination.chmod(0o600)
    source = (
        "import faulthandler, os, sys\n"
        # The stable shim execs isolated Python before importing hz; observe that app, not its short-lived shim.
        "if sys.flags.isolated:\n"
        # -I implies -E, so PYTHONFAULTHANDLER is ignored after the shim exec.
        "    faulthandler.enable()\n"
        f"    _hz_stacks = os.open({str(destination)!r}, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)\n"
        "    faulthandler.dump_traceback_later(4, repeat=False, file=_hz_stacks)\n"
    )
    with target.open("x") as handle:
        handle.write(source)


def _sample_role(line):
    """Map native thread labels to fixed roles without returning their text."""
    if "com.apple.main-thread" in line:
        return "main"
    return "publication" if "(when_started)" in line else "other"


def _diagnostic_frame(line):
    match = re.match(r'  File "([^"\n]{1,512})", line ([1-9]\d{0,6}) in ', line)
    if match is None:
        return None
    normalized = match[1].replace("\\", "/")
    frozen = {
        "<frozen importlib._bootstrap>": "_bootstrap",
        "<frozen importlib._bootstrap_external>": "_bootstrap_external",
    }
    label = frozen.get(normalized) or next(
        (name for name in _STARTUP_MODULES if normalized.endswith("/" + name)), "other"
    )
    return label, int(match[2])


class RunningService:
    """Only the processes this fixture creates may be killed or joined."""

    def __init__(self, service, manager, command, environment):
        self.service, self.manager = service, manager
        self.command, self.environment = command, environment
        self.processes = []
        self.last_launch = None
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
        started = time.monotonic()
        process = subprocess.Popen(self.command, env=self.environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.processes.append(process)
        self.last_launch = process, started
        self.manager.pid = process.pid
        return process

    def ready(self, previous=None):
        status = None
        deadline = time.monotonic() + 12
        self.ready_started = deadline - 12
        self.control_failure = None
        self.control_failure_count = 0
        changed = threading.Event()
        while time.monotonic() < deadline:
            status = self.service.status()
            if status.ready and status.instance != previous:
                return status
            returncode = self.processes[-1].poll()
            if returncode is not None:
                output, errors = self.processes[-1].communicate(timeout=1)
                pytest.fail(
                    "The owned web server exited before readiness; "
                    f"owned process exit={returncode}, signal={-returncode if returncode < 0 else None}, "
                    f"publication failure={self.publication_failure(output + errors)}"
                    + (f", crash trace={self.crash_trace(errors)}." if returncode < 0 else ".")
                )
            changed.wait(0.02)
        pytest.fail(
            "The owned web server did not become ready with a new lifecycle instance; "
            f"last status={status.model_dump_json() if status is not None else 'not sampled'}, "
            f"owned process exit={self.processes[-1].poll()}, wait channel={self.wait_channel()}, "
            f"control failure={self.control_failure}, failed status calls={self.control_failure_count}, "
            f"publication evidence={self.publication_evidence(initial=previous is None and len(self.processes) == 1)}."
        )

    @staticmethod
    def crash_trace(errors):
        """Summarize fatal Python stderr without exposing arbitrary files, functions or output."""
        lines = errors[-64 * 1024 :].decode("utf-8", errors="replace").splitlines()
        prefix = "Fatal Python error: "
        for index, line in enumerate(lines):
            if line.startswith(prefix):
                kind = line.removeprefix(prefix)
                kinds = {
                    "Segmentation fault",
                    "Bus error",
                    "Aborted",
                    "Floating-point exception",
                    "Illegal instruction",
                }
                frames = (frame for line in lines[index + 1 :] if (frame := _diagnostic_frame(line)) is not None)
                return {"error": kind if kind in kinds else "other", "frames": list(islice(frames, 32))}
        return "none (no faulthandler output)"

    @staticmethod
    def publication_failure(output):
        """Return only a fixed category and bounded errno from owned publication diagnostics."""
        matches = re.findall(
            rb"Owned web publication failed: category=(os|value|ownership) errno=(none|[0-9]{1,4})\r?$",
            output[-16 * 1024 :],
            re.MULTILINE,
        )
        if not matches:
            return None
        category, encoded = matches[-1]
        number = int(encoded) if encoded != b"none" else None
        return {
            "category": category.decode("ascii"),
            "errno": number if number is not None and 1 <= number <= 4095 else None,
        }

    def publication_evidence(self, *, initial):
        """Observe only after the readiness deadline has already failed."""
        cpu = self.startup_cpu()
        pending = list(islice(self.service.directory.glob(".runtime.json.*"), 4))
        result = {"cpu": cpu, "runtime_present": self.service.runtime.exists(), "pending_writes": len(pending)}
        # A real tool replacement removes the fixture-only sitecustomize; never attribute its old stack to an exec.
        result["startup"] = self.startup_metadata() if initial else "not-initial-startup"
        result["native"] = self.native_state()
        result["runtime_after_sample"] = self.service.runtime.exists()
        return result

    def startup_cpu(self):
        """Read only lifetime CPU counters of this fixture's still-owned child, before native sampling."""
        result = {"result": "unavailable"}
        if not self.processes or self.last_launch is None or self.last_launch[0] is not self.processes[-1]:
            return result
        process, started = self.last_launch
        pid = getattr(process, "pid", None)
        now = time.monotonic()
        wall, waiting = now - started, now - self.ready_started
        if (
            type(pid) is not int
            or pid <= 0
            or not all(0 <= value <= 600 and math.isfinite(value) for value in (wall, waiting))
        ):
            return result
        result.update(process_wall_seconds=round(wall, 3), ready_wait_seconds=round(waiting, 3))
        if process.poll() is not None:
            return {**result, "result": "exited"}
        import psutil

        try:
            counters = psutil.Process(pid).cpu_times()
        except (OSError, psutil.Error):
            return result
        if process.poll() is not None:
            return {**result, "result": "exited"}
        user, system = getattr(counters, "user", None), getattr(counters, "system", None)
        if all(type(value) in {int, float} and 0 <= value <= 600 and math.isfinite(value) for value in (user, system)):
            result.update(
                result="sampled", process_user_seconds=round(user, 3), process_system_seconds=round(system, 3)
            )
        return result

    def startup_metadata(self):
        """Publish only fixed source labels and line numbers from bounded, fixture-private Python snapshots."""
        try:
            with (self.service.directory / "startup-stacks.txt").open("rb") as handle:
                report = handle.read(64 * 1024).decode("utf-8", errors="replace")
        except OSError:
            return []
        return self.startup_frames(report)

    @staticmethod
    def startup_frames(report):
        """Discard thread identifiers, paths, source text and arbitrary names before formatting diagnostics."""
        snapshots = []
        frames = None
        for line in report.splitlines():
            if line.startswith("Timeout ("):
                if len(snapshots) == 3:
                    break
                frames = []
                snapshots.append(frames)
            elif frames is not None and len(frames) < 32:
                if (frame := _diagnostic_frame(line)) is not None:
                    frames.append(frame)
        return snapshots

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
