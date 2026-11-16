"""Read-only trace of the published bridge updater inside one disposable hosted case."""

import http.client
import json
import os
import re
import runpy
import stat
import sys
import urllib.error
from http import HTTPStatus
from pathlib import Path

FILENAME = "legacy-discovery-trace.json"
SELECTOR = "tests/integration/legacy_discovery_trace_hosted_test.py"
EVENT_LIMIT = 16
DISCOVERY = {
    "_service.py": frozenset({"highest_version", "requires_python"}),
    "_update.py": frozenset({"_asked_latest", "_target_release", "_interpreter_for", "needed_python"}),
    "_github.py": frozenset({"get_json", "_open"}),
}
EVENTS = frozenset().union(*DISCOVERY.values(), {"http_status"})
CATEGORIES = frozenset({"rate_limited", "http_error", "timeout", "url_error", "os_error", "invalid_response", "other"})
CONSOLE_BODY = b"""# -*- coding: utf-8 -*-
import sys
from _heinzel_launcher import cli
if __name__ == "__main__":
    if sys.argv[0].endswith("-script.pyw"):
        sys.argv[0] = sys.argv[0][:-11]
    elif sys.argv[0].endswith(".exe"):
        sys.argv[0] = sys.argv[0][:-4]
    sys.exit(cli())
"""


def require_hosted(env, platform):
    expected = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_WORKFLOW": "Focused job",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "macOS",
        "GITHUB_RUN_ATTEMPT": "1",
        "FOCUSED_KIND": "pytest",
        "FOCUSED_SELECTOR": SELECTOR,
        "FOCUSED_REPEAT": "1",
        "FOCUSED_STRESS": "false",
    }
    if platform != "darwin" or any(env.get(key) != value for key, value in expected.items()):
        raise ValueError("requires the explicit first-attempt hosted macOS diagnostic")
    if not re.fullmatch(r"[a-f0-9]{40}", env.get("GITHUB_SHA", "")):
        raise ValueError("requires the hosted checkout identity")
    if not env.get("FOCUSED_REF") or env["FOCUSED_REF"] != env.get("GITHUB_REF_NAME"):
        raise ValueError("diagnostic branch mismatch")


def console(root, interpreter):
    path = root / "bin/hz"
    target = path.resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_file() or target.stat().st_size > 2048:
        raise ValueError("unexpected bridge console script")
    executable = os.fsencode(interpreter)
    headers = [b"#!" + executable + b"\n"]
    if b"'" not in executable and b"\n" not in executable:
        headers.append(b"#!/bin/sh\n'''exec' '" + executable + b"' \"$0\" \"$@\"\n' '''\n")
    if target.read_bytes() not in [header + CONSOLE_BODY for header in headers]:
        raise ValueError("unexpected bridge console entry point")
    return path


def http_status(value):
    return int(value) if type(value) in (int, HTTPStatus) and 100 <= value <= 599 else None


def version(value):
    return value if type(value) is str and re.fullmatch(r"[0-9]{1,4}(?:\.[0-9]{1,4}){1,2}", value) else None


def exception_facts(error, github_path):
    cls = type(error)
    if cls.__name__ == "RateLimitedError" and cls.__module__ == "heinzel.modules.system._github" and github_path:
        return "rate_limited", None
    if isinstance(error, urllib.error.HTTPError):
        return "http_error", http_status(error.code)
    if isinstance(error, TimeoutError):
        return "timeout", None
    if isinstance(error, urllib.error.URLError):
        return "url_error", None
    if isinstance(error, OSError):
        return "os_error", None
    if isinstance(error, ValueError | TypeError):
        return "invalid_response", None
    return "other", None


class Trace:
    def __init__(self, site):
        self.paths = {str(site / "heinzel/modules/system" / filename): names for filename, names in DISCOVERY.items()}
        self.github = str(site / "heinzel/modules/system/_github.py")
        self.events = []
        self.active_http = 0

    def __call__(self, frame, event, arg):
        if event != "call" or len(self.events) == EVENT_LIMIT:
            return None
        name, path = frame.f_code.co_name, frame.f_code.co_filename
        tracked = name in self.paths.get(path, ())
        reading_status = self.active_http and path == http.client.__file__ and name == "_read_status"
        if not tracked and not reading_status:
            return None
        frame.f_trace_lines = False
        opening = path == self.github and name == "_open"
        if opening:
            self.active_http += 1

        def local(_frame, kind, value):
            if opening and kind == "return":
                self.active_http -= 1
            if len(self.events) == EVENT_LIMIT:
                return None
            if reading_status and kind == "return" and type(value) is tuple and len(value) == 3:
                status = http_status(value[1])
                if status is not None:
                    self.events.append({"operation": "http_status", "event": "return", "status": status})
            elif tracked and kind == "exception":
                category, status = exception_facts(value[1], path == self.github)
                self.events.append({"operation": name, "event": category, "status": status})
            elif (
                tracked
                and kind == "return"
                and name in {"highest_version", "_asked_latest", "_target_release", "needed_python"}
            ):
                self.events.append({"operation": name, "event": "return", "version": version(value)})
            return local

        return local


def sidecar(root, events):
    path = root / FILENAME
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        json.dump({"kind": "instrumented-non-acceptance", "events": events}, stream)


def read_sidecar(root):
    fd = os.open(root / FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("unsafe trace sidecar")
        data = stream.read(4097)
    document = json.loads(data) if len(data) <= 4096 else None
    if (
        not isinstance(document, dict)
        or document.keys() != {"kind", "events"}
        or document["kind"] != "instrumented-non-acceptance"
    ):
        raise ValueError("wrong trace schema")
    events = document["events"]
    if not isinstance(events, list) or len(events) > EVENT_LIMIT:
        raise ValueError("wrong trace event count")
    for row in events:
        if not isinstance(row, dict) or row.get("operation") not in EVENTS:
            raise ValueError("unknown trace operation")
        event = row.get("event")
        if event not in CATEGORIES | {"return"}:
            raise ValueError("unknown trace event")
        if event == "return" and row["operation"] != "http_status":
            if row.keys() != {"operation", "event", "version"} or (
                row["version"] is not None and not version(row["version"])
            ):
                raise ValueError("unsafe trace version")
        elif row.keys() != {"operation", "event", "status"} or (
            row["status"] is not None and http_status(row["status"]) is None
        ):
            raise ValueError("unsafe trace status")
    return document


def run(root):
    prefix = root / "tools/heinzel"
    interpreter = prefix / "bin/python"
    site = prefix / "lib/python3.12/site-packages"
    folder = root.stat()
    if (
        root.resolve(strict=True) != root
        or Path.cwd() != root
        or folder.st_uid != os.getuid()
        or stat.S_IMODE(folder.st_mode) != 0o700
        or Path(sys.executable) != interpreter
        or not interpreter.resolve(strict=True).is_relative_to(root / "uv-python")
        or not site.resolve(strict=True).is_relative_to(root)
        or sys.version_info[:2] != (3, 12)
        or not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.flags.dont_write_bytecode
        or sys.pycache_prefix != str(root / "trace-pycache")
        or os.path.lexists(sys.pycache_prefix)
    ):
        raise ValueError("requires owned isolated bridge interpreter")
    paths = {
        "HOME": "home",
        "UV_TOOL_DIR": "tools",
        "UV_TOOL_BIN_DIR": "bin",
        "UV_PYTHON_INSTALL_DIR": "uv-python",
        "UV_CACHE_DIR": "uv-cache",
        "HZ_CONFIG_HOME": "config_home",
        "HZ_DATA_DIR": "data_dir",
        "HZ_CACHE_DIR": "cache_dir",
        "HZ_STATE_DIR": "state_dir",
        "HZ_LOG_DIR": "log_dir",
    }
    if any(os.environ.get(key) != str(root / value) for key, value in paths.items()) or (
        os.environ.get("PATH") != f"{root}/guard:{root}/bin:/usr/bin:/bin"
    ):
        raise ValueError("requires the private case environment")
    launch = console(root, interpreter)
    sys.argv = [str(launch), "system", "update", "--json"]
    sys.prefix = sys.exec_prefix = str(prefix)
    sys.path.insert(0, str(site))
    trace = Trace(site)
    # The verified console launcher would exec this module with -I and these arguments.
    sys.settrace(trace)
    try:
        runpy.run_module("heinzel", run_name="__main__", alter_sys=True)
    finally:
        sys.settrace(None)
        sidecar(root, trace.events)


if __name__ == "__main__":
    run(Path(sys.argv[1]))
