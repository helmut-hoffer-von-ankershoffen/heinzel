"""The `gateway` option: a company CLI that grants access to AI, described by data in `[ai.access]` (ADR 0057).

hz runs only the two commands configured: a status command, which must not call a model, and a test command, run only
on `hz ai access test --name gateway`. Each is an argument list without a shell, resolved on PATH before it runs, in an
empty private directory, with an allowlisted environment that never holds `HZ_CREDENTIAL_*`, within a timeout after
which its whole process group is killed, and with its output cut at a limit. A company plugin with more to say ships its
own option instead (`module.provides("ai-access", name="acme")`).
"""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from heinzel import AccessStatus, AccessTest
from heinzel.kernel import clean_text, subprocess_environment
from heinzel.modules.ai.access._models import AccessSettings

NAME = "gateway"
STATUS_TIMEOUT = 10.0
TEST_TIMEOUT = 90.0
OUTPUT_LIMIT = 64 * 1024
"""Bytes of stdout and stderr kept from a gateway command; a status or a ping says little."""
DETAIL_LIMIT = 300
BASE_ENVIRONMENT = frozenset({"PATH", "HOME", "LANG", "TMPDIR"})
SET_STATUS = """hz system config set ai.access.gateway_status_command '["acme", "ai", "status", "--json"]'"""


@dataclass(frozen=True, slots=True)
class Finished:
    """How a gateway command ended: its exit code (None when stopped at its timeout) and what it printed."""

    returncode: int | None
    stdout: str
    stderr: str


Run = Callable[[list[str], Mapping[str, str], float], Finished]
"""Runs a gateway command; tests replace it so no process starts."""


def run_command(command: list[str], env: Mapping[str, str], timeout: float) -> Finished:
    """Run `command` in its own session in an empty private directory; past `timeout`, kill its process group."""
    # Output goes to files in the private directory, not pipes, so a chatty command can't fill memory: only the first
    # OUTPUT_LIMIT bytes of each are read back.
    with tempfile.TemporaryDirectory(prefix="hz-gateway-") as directory:
        work = Path(directory) / "work"
        work.mkdir()
        out_path, err_path = Path(directory) / "stdout", Path(directory) / "stderr"
        with out_path.open("wb") as out, err_path.open("wb") as err:
            process = subprocess.Popen(  # noqa: S603 - a configured argument list, executable resolved, no shell
                command, cwd=work, env=dict(env), stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                start_new_session=True,
            )  # fmt: skip
            returncode: int | None
            try:
                returncode = process.wait(timeout)
            except subprocess.TimeoutExpired:
                returncode = None
            except BaseException:  # Ctrl-C reaches only us (the command has its own session): stop it, then go on
                _kill_group(process.pid)
                process.wait()
                raise
            _kill_group(process.pid)  # past the timeout, or whatever it left running in its group
            process.wait()
        return Finished(returncode, _head(out_path), _head(err_path))


def _kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):  # it and its group are gone already
        os.killpg(pid, signal.SIGKILL)


def _head(path: Path) -> str:
    with path.open("rb") as file:
        return file.read(OUTPUT_LIMIT).decode("utf-8", errors="replace")


def environment(names: list[str]) -> dict[str, str]:
    """PATH, HOME, LANG, LC_* and TMPDIR plus `names`; never an `HZ_CREDENTIAL_*` variable."""
    wanted = BASE_ENVIRONMENT | set(names)
    return {key: value for key, value in subprocess_environment().items() if key in wanted or key.startswith("LC_")}


def _json(text: str) -> dict[str, Any]:
    """The JSON object `text` holds, or an empty one."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return cast("dict[str, Any]", data) if isinstance(data, dict) else {}


def _detail(value: object, fallback: str = "") -> str:
    return clean_text(value).strip()[:DETAIL_LIMIT] if isinstance(value, str) and value.strip() else fallback


def configured_hosts(settings: AccessSettings) -> list[str]:
    """The hosts `[ai.access] gateway_hosts` names, for the capability matrix."""
    return settings.gateway_hosts


class GatewayAccess:
    """The `gateway` option of `ai-access`: a company CLI described by `[ai.access]`."""

    def __init__(
        self,
        settings: AccessSettings,
        run: Run = run_command,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        """Create the option; settings are injected, `run` and `which` replace processes in tests."""
        self._settings = settings
        self._run = run
        self._which = which

    def _status(self, **update: Any) -> AccessStatus:  # noqa: ANN401 - fields of AccessStatus
        settings = self._settings
        base = AccessStatus(
            title=settings.gateway_title,
            summary=settings.gateway_summary,
            installed=False,
            models=list(settings.gateway_models),
            runner=settings.gateway_runner,
        )
        return base.model_copy(update=update)

    def _resolve(self, command: list[str]) -> list[str] | None:
        found = self._which(command[0])
        return [str(Path(found).absolute()), *command[1:]] if found else None

    def status(self) -> AccessStatus:
        """Whether the gateway CLI is there and signed in, from its status command; calls no model."""
        settings = self._settings
        if not settings.gateway_status_command:
            return self._status(detail="not set up: no status command", fix=f"Configure it, e.g. {SET_STATUS}")
        command = self._resolve(settings.gateway_status_command)
        if command is None:
            return self._status(
                detail=f"{settings.gateway_status_command[0]} is not on PATH",
                fix="Install your company's AI gateway CLI, or correct ai.access.gateway_status_command",
            )
        try:
            finished = self._run(command, environment(settings.gateway_env), STATUS_TIMEOUT)
        except OSError as exc:
            return self._status(detail=f"the status command didn't start: {exc}", fix=settings.gateway_sign_in)
        if finished.returncode is None:
            return self._status(installed=True, detail=f"the status command took longer than {STATUS_TIMEOUT:g}s")
        data = _json(finished.stdout)
        models = [clean_text(m) for m in data.get("models", []) if isinstance(m, str)] or list(settings.gateway_models)
        said = data.get("signed_in")
        signed_in = said if isinstance(said, bool) else finished.returncode == 0
        tail = _detail(finished.stderr.strip()[-DETAIL_LIMIT:])
        detail = _detail(data.get("detail"), "signed in" if signed_in else (tail or f"exit {finished.returncode}"))
        return self._status(
            installed=True,
            signed_in=signed_in,
            detail=detail,
            fix="" if signed_in else settings.gateway_sign_in,
            models=models,
        )

    def test(self) -> AccessTest:
        """Run the gateway's test command once; a failure is a result."""
        settings = self._settings
        if not settings.gateway_test_command:
            fix = """hz system config set ai.access.gateway_test_command '["acme", "ai", "ping"]'"""
            return AccessTest(ok=False, detail="no test command is configured", fix=fix)
        command = self._resolve(settings.gateway_test_command)
        if command is None:
            return AccessTest(ok=False, detail=f"{settings.gateway_test_command[0]} is not on PATH")
        started = time.monotonic()
        try:
            finished = self._run(command, environment(settings.gateway_env), TEST_TIMEOUT)
        except OSError as exc:
            return AccessTest(ok=False, detail=f"the test command didn't start: {exc}")
        duration = time.monotonic() - started
        if finished.returncode is None:
            return AccessTest(ok=False, detail=f"stopped after {TEST_TIMEOUT:g}s", duration_seconds=duration)
        data = _json(finished.stdout)
        said = data.get("ok")
        ok = said if isinstance(said, bool) else finished.returncode == 0
        fallback = _detail(finished.stdout if ok else finished.stderr.strip()[-DETAIL_LIMIT:]) or (
            "answered" if ok else f"exit {finished.returncode}"
        )
        cost = data.get("cost_usd")
        model = data.get("model")
        return AccessTest(
            ok=ok,
            detail=_detail(data.get("detail"), fallback) if data else fallback,
            fix="" if ok else settings.gateway_sign_in,
            model=clean_text(model) if isinstance(model, str) else None,
            cost_usd=float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None,
            duration_seconds=duration,
        )
