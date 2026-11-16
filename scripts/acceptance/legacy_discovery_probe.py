#!/usr/bin/env python3
"""Instrument a published 0.9.43 update in a dedicated disposable VM; never acceptance evidence.

Run only with the isolated interpreter and private paths described in the accompanying guide.
The program executes a real update and may mutate its private tool installation.
"""

import argparse
import contextlib
import functools
import hashlib
import io
import json
import os
import re
import runpy
import select
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import zipfile
from pathlib import Path

BRIDGE_SHA256 = "f18571dae4d3533598eaa645367cbe7432d5f915360f01814faf64352bc93b7a"
EVENT_LIMIT = 64
OUTPUT_LIMIT = 65536


def safe_version(value, *, minor=False):
    pattern = r"\d{1,4}\.\d{1,4}" if minor else r"\d{1,4}(?:\.\d{1,4}){2}"
    return value if isinstance(value, str) and re.fullmatch(pattern, value, re.ASCII) else None


def failure_facts(error):
    """Fixed categories only; exception messages, URLs and response bodies are never retained."""
    if type(error).__name__ == "RateLimitedError" and type(error).__module__ == "heinzel.modules.system._github":
        return {"category": "rate_limited", "exception_class": "RateLimitedError", "http_status": None}
    if isinstance(error, urllib.error.HTTPError):
        code = error.code if isinstance(error.code, int) and 100 <= error.code <= 599 else None
        return {"category": "http_error", "exception_class": "HTTPError", "http_status": code}
    if isinstance(error, TimeoutError):
        return {"category": "timeout", "exception_class": "TimeoutError", "http_status": None}
    if isinstance(error, urllib.error.URLError):
        return {"category": "url_error", "exception_class": "URLError", "http_status": None}
    if isinstance(error, OSError):
        return {"category": "os_error", "exception_class": "OSError", "http_status": None}
    if isinstance(error, ValueError):
        return {"category": "invalid_response", "exception_class": "ValueError", "http_status": None}
    if isinstance(error, TypeError):
        return {"category": "invalid_response", "exception_class": "TypeError", "http_status": None}
    return {"category": "other_error", "exception_class": "other", "http_status": None}


class Probe:
    def __init__(self):
        self.events = []
        self.truncated = False

    def record(self, event):
        if len(self.events) < EVENT_LIMIT:
            self.events.append(event)
        else:
            self.truncated = True

    def wrap(self, original, name):
        @functools.wraps(original)
        def call(*args, **kwargs):
            start = time.monotonic()
            try:
                value = original(*args, **kwargs)
            except BaseException as error:
                self.record(
                    {
                        "operation": name,
                        "duration_ms": min(600000, round((time.monotonic() - start) * 1000)),
                        **failure_facts(error),
                    }
                )
                raise
            version = safe_version(value, minor=name == "needed_python") if name != "requires_python" else None
            category = "none" if value is None else "value"
            self.record(
                {
                    "operation": name,
                    "duration_ms": min(600000, round((time.monotonic() - start) * 1000)),
                    "category": category,
                    "version": version,
                    "http_status": None,
                }
            )
            return value

        return call


class Capture(io.TextIOBase):
    """Keep at most a bounded CLI result in memory, never in a file or diagnostic output."""

    def __init__(self):
        self.data = ""
        self.truncated = False

    def write(self, value):
        size = len(value)
        remaining = max(0, OUTPUT_LIMIT - len(self.data))
        self.data += value[:remaining]
        self.truncated |= size > remaining
        return size

    def flush(self):
        pass


def verified_bridge(wheel, prefix):
    """Bind installed Python source and metadata to the original published wheel before import."""
    fd = os.open(wheel, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024 * 1024:
            raise ValueError("unsafe wheel")
        data = stream.read(64 * 1024 * 1024 + 1)
    if hashlib.sha256(data).hexdigest() != BRIDGE_SHA256:
        raise ValueError("wheel digest mismatch")
    site = prefix / "lib/python3.12/site-packages"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [info for info in archive.infolist() if info.filename.endswith(".py")]
        if not members or len(members) > 1000:
            raise ValueError("wheel contents invalid")
        expected_sources = {info.filename for info in members if info.filename.startswith("heinzel/")}
        core_paths = list((site / "heinzel").rglob("*"))
        if any(path.is_symlink() for path in core_paths):
            raise ValueError("unexpected Core symlink")
        installed_sources = {str(path.relative_to(site)) for path in core_paths if path.suffix == ".py"}
        if expected_sources != installed_sources or any(
            path.suffix in {".pyc", ".so", ".pyd", ".dylib"} for path in core_paths
        ):
            raise ValueError("unexpected Core source or bytecode")
        if list((site / "__pycache__").glob("_heinzel_launcher.*.pyc")):
            raise ValueError("unexpected launcher bytecode")
        for info in members:
            path = site / info.filename
            if path.is_symlink() or not path.resolve(strict=True).is_relative_to(site.resolve(strict=True)):
                raise ValueError("unsafe installed source")
            if path.stat().st_size != info.file_size or path.read_bytes() != archive.read(info):
                raise ValueError("installed source differs from wheel")
        metadata = "heinzel-0.9.43.dist-info/METADATA"
        installed_metadata = site / metadata
        if installed_metadata.is_symlink() or not installed_metadata.resolve(strict=True).is_relative_to(
            site.resolve()
        ):
            raise ValueError("unsafe installed metadata")
        if installed_metadata.read_bytes() != archive.read(metadata):
            raise ValueError("installed metadata differs from wheel")


def isolated_root(root, *, environment=None, executable=None, flags=None, version=None):
    environment = os.environ if environment is None else environment
    executable = sys.executable if executable is None else executable
    flags = sys.flags if flags is None else flags
    version = sys.version_info if version is None else version
    root = root.resolve(strict=True)
    expected = {
        "HOME": "home",
        "TMPDIR": "tmp",
        "UV_TOOL_DIR": "tools",
        "UV_TOOL_BIN_DIR": "bin",
        "UV_PYTHON_INSTALL_DIR": "uv-python",
        "UV_CACHE_DIR": "uv-cache",
        "HZ_CONFIG_HOME": "config_home",
        "HZ_DATA_DIR": "data_dir",
        "HZ_CACHE_DIR": "cache_dir",
        "HZ_STATE_DIR": "state_dir",
        "HZ_LOG_DIR": "log_dir",
        "HZ_SYSTEM_DAEMON__UNIT_DIR": "units",
        "XDG_CACHE_HOME": "xdg-cache",
        "XDG_CONFIG_HOME": "xdg-config",
        "XDG_DATA_HOME": "xdg-data",
        "XDG_STATE_HOME": "xdg-state",
    }
    for key, relative in expected.items():
        if not environment.get(key) or Path(environment[key]).resolve() != root / relative:
            raise ValueError("private environment paths required")
    prefix = (root / "tools/heinzel").resolve(strict=True)
    if (
        prefix != root / "tools/heinzel"
        or Path(executable).absolute() != root / "tools/heinzel/bin/python"
        or not Path(executable).resolve().is_relative_to(root / "uv-python")
    ):
        raise ValueError("private tool and managed interpreter required")
    if version[:2] != (3, 12) or not flags.isolated or not flags.no_site:
        raise ValueError("isolated Python 3.12 with -S required")
    if environment.get("UV_NO_CONFIG") != "1" or environment.get("PATH") != f"{root}/bin:/usr/bin:/bin":
        raise ValueError("private command search path required")
    allowed = set(expected) | {
        "UV_NO_CONFIG",
        "PATH",
        "HZ_UPDATE_CHECK",
        "HZ_SYSTEM_CREDENTIAL__BACKEND",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "__CF_USER_TEXT_ENCODING",
    }
    if set(environment) - allowed or Path.cwd().resolve() != root:
        raise ValueError("private working directory and clean environment required")
    return root, prefix


def supervise(worker, environment, cwd):
    """Discard all worker/child output, including exit handlers; read only a bounded private result pipe."""
    read_fd, write_fd = os.pipe()
    process = None
    try:
        process = subprocess.Popen(
            [*worker, str(write_fd)],
            cwd=cwd,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(write_fd,),
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        os.close(write_fd)
        write_fd = -1
        data = b""
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline and len(data) <= OUTPUT_LIMIT:
            ready, _, _ = select.select([read_fd], [], [], min(1, max(0, deadline - time.monotonic())))
            if ready:
                chunk = os.read(read_fd, min(8192, OUTPUT_LIMIT + 1 - len(data)))
                if not chunk:
                    break
                data += chunk
        else:
            return None
        process.wait(timeout=max(1, deadline - time.monotonic()))
        if len(data) > OUTPUT_LIMIT or process.returncode not in {0, 1}:
            return None
        return json.loads(data)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            except ProcessLookupError:
                process.wait()


def post_interpreter(root):
    try:
        path = (root / "tools/heinzel/bin/python").resolve(strict=True)
        if path.is_relative_to(root / "uv-python") and re.fullmatch(r"python\d{1,2}\.\d{1,2}", path.name, re.ASCII):
            return path.name
    except OSError:
        pass
    return None


def sanitized_result(value):
    """Do not trust bytes returned by a process that loaded an installed tool."""
    if not isinstance(value, dict) or value.get("kind") != "instrumented-non-acceptance":
        return {"kind": "instrumented-non-acceptance", "refused": True}
    operations = {"highest_version", "requires_python", "target_release", "needed_python"}
    categories = {
        "rate_limited",
        "http_error",
        "timeout",
        "url_error",
        "os_error",
        "invalid_response",
        "other_error",
        "none",
        "value",
    }
    classes = {
        "RateLimitedError",
        "HTTPError",
        "TimeoutError",
        "URLError",
        "OSError",
        "ValueError",
        "TypeError",
        "other",
    }
    events = value.get("events")
    if not isinstance(events, list) or len(events) > EVENT_LIMIT:
        return {"kind": "instrumented-non-acceptance", "refused": True}
    clean = []
    for event in events:
        if (
            not isinstance(event, dict)
            or event.get("operation") not in operations
            or event.get("category") not in categories
        ):
            return {"kind": "instrumented-non-acceptance", "refused": True}
        operation = event["operation"]
        duration = event.get("duration_ms")
        status = event.get("http_status")
        clean.append(
            {
                "operation": operation,
                "category": event["category"],
                "duration_ms": duration if type(duration) is int and 0 <= duration <= 600000 else None,
                "http_status": status if type(status) is int and 100 <= status <= 599 else None,
                "exception_class": event.get("exception_class") if event.get("exception_class") in classes else None,
                "version": safe_version(event.get("version"), minor=operation == "needed_python"),
            }
        )
    update = value.get("update")
    result = {
        "kind": "instrumented-non-acceptance",
        "events": clean,
        "exit_code": value.get("exit_code") if type(value.get("exit_code")) is int else None,
        "post_interpreter": value.get("post_interpreter")
        if isinstance(value.get("post_interpreter"), str)
        and re.fullmatch(r"python\d{1,2}\.\d{1,2}", value["post_interpreter"], re.ASCII)
        else None,
        "events_truncated": value.get("events_truncated") is True,
        "output_truncated": value.get("output_truncated") is True,
        "native_output": "discarded",
    }
    result["update"] = (
        {
            "updated": update.get("updated") if type(update.get("updated")) is bool else None,
            **{key: safe_version(update.get(key)) for key in ("before", "after", "latest")},
        }
        if isinstance(update, dict)
        else None
    )
    failure = value.get("failure")
    result["failure"] = (
        {
            "category": failure.get("category") if failure.get("category") in categories else "other_error",
            "exception_class": failure.get("exception_class") if failure.get("exception_class") in classes else "other",
            "http_status": failure.get("http_status")
            if type(failure.get("http_status")) is int and 100 <= failure["http_status"] <= 599
            else None,
        }
        if isinstance(failure, dict)
        else None
    )
    return result


def run_worker(root, prefix):
    # -S prevents unverified .pth startup; restore the venv prefix and verified package search path.
    sys.prefix = sys.exec_prefix = str(prefix)
    site = prefix / "lib/python3.12/site-packages"
    sys.path.insert(0, str(site))
    probe = Probe()
    stdout, stderr = Capture(), Capture()
    status, failure = None, None
    sys.argv = [str(root / "bin/hz"), "system", "update", "--json"]
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                # These wrappers delegate exactly once and return or re-raise the original result.
                from heinzel.modules.system import _service, _update

                for module in (_service, _update):
                    if not Path(module.__file__).resolve().is_relative_to((site / "heinzel").resolve()):
                        raise ValueError("unexpected Core module")
                methods = (
                    (_service, _service.GitHubReleases.highest_version),
                    (_service, _service.GitHubReleases.requires_python),
                    (_update, _update.UpdateService._target_release),
                    (_update, _update.UpdateService.needed_python),
                )
                if any(
                    Path(method.__code__.co_filename).resolve() != Path(module.__file__).resolve()
                    for module, method in methods
                ):
                    raise ValueError("unexpected Core callable")
                _service.GitHubReleases.highest_version = probe.wrap(
                    _service.GitHubReleases.highest_version, "highest_version"
                )
                _service.GitHubReleases.requires_python = probe.wrap(
                    _service.GitHubReleases.requires_python, "requires_python"
                )
                _update.UpdateService._target_release = probe.wrap(
                    _update.UpdateService._target_release, "target_release"
                )
                _update.UpdateService.needed_python = probe.wrap(_update.UpdateService.needed_python, "needed_python")
                runpy.run_module("heinzel", run_name="__main__", alter_sys=True)
                status = 0
            except SystemExit as error:
                status = error.code if type(error.code) is int else None
            except BaseException as error:
                failure = failure_facts(error)
    finally:
        result = {
            "kind": "instrumented-non-acceptance",
            "exit_code": status,
            "events": probe.events,
            "post_interpreter": post_interpreter(root),
            "failure": failure,
            "events_truncated": probe.truncated,
            "output_truncated": stdout.truncated or stderr.truncated,
            "native_output": "discarded",
        }
        try:
            output = json.loads(stdout.data)
            result["update"] = {
                "updated": output.get("updated") if type(output.get("updated")) is bool else None,
                **{key: safe_version(output.get(key)) for key in ("before", "after", "latest")},
            }
        except (ValueError, AttributeError):
            result["update"] = None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--confirm-dedicated-vm", action="store_true")
    parser.add_argument("--worker-fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_fd is not None:
        if args.worker_fd <= 2:
            return 1
        # No native, child or exit-handler output may reach the operator's terminal.
        os.set_inheritable(args.worker_fd, False)
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, 1)
        os.dup2(null, 2)
        os.close(null)
    result = {"kind": "instrumented-non-acceptance", "refused": True}
    try:
        if not args.confirm_dedicated_vm:
            raise ValueError("dedicated VM confirmation required")
        root, prefix = isolated_root(args.case_root)
        if not args.wheel.resolve(strict=True).is_relative_to(root):
            raise ValueError("wheel must belong to the private case")
        if args.worker_fd is not None:
            verified_bridge(args.wheel, prefix)
            result = run_worker(root, prefix)
        else:
            worker = [
                sys.executable,
                "-I",
                "-S",
                str(Path(__file__).resolve()),
                "--case-root",
                str(root),
                "--wheel",
                str(args.wheel),
                "--confirm-dedicated-vm",
                "--worker-fd",
            ]
            result = sanitized_result(supervise(worker, dict(os.environ), root))
    except Exception:  # Diagnostics refuse safely; never print private paths or exception text.
        pass  # Emit a fixed refusal, never a path, environment value or traceback.
    encoded = json.dumps(result, sort_keys=True).encode()
    if args.worker_fd is not None:
        with os.fdopen(args.worker_fd, "wb", closefd=True) as stream:
            stream.write(encoded)
    else:
        print(encoded.decode())
    return 0 if result.get("exit_code") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
