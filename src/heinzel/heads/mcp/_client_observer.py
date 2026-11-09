"""Read-only request boundary for a separately provisioned native MCP observer.

The protected helper is the execution boundary. Provision it only after review of a policy that permits the
pinned vendor and dependency closure, denies untrusted descendant execution, config writes, credential access
and network access, and reads the applicable managed policy. The helper must refuse if any guarantee, policy
read or exact source proof fails. In particular, a blocked managed preference API is never an empty policy.

One bounded request and response use private stdio, a fresh challenge, and the exact package, account, physical
workspace and registration. The helper must reap its own native children before replying and prevent escape
from confinement. This client does not install a helper or execute user-installed vendor paths.
"""

import json
import os
import platform
import pwd
import secrets
import selectors
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from heinzel.heads.mcp._client_config import (
    ClientBridgeBinding,
    ClientConfigContext,
    McpClient,
    load_config_json,
    same_config_value,
)
from heinzel.heads.mcp._client_config_native import MAX_NATIVE_CONFIG_OUTPUT, NativeInspection
from heinzel.heads.mcp._client_observer_install import ProtectedObserverInstall
from heinzel.kernel import HeinzelError
from heinzel.kernel._logging import get_logger, step

log = get_logger("mcp")
_VERSION = 1
_MAX_REQUEST = 4096
_MAX_RESPONSE = 1536 * 1024
_TIMEOUT = 15.0
_REAP_TIMEOUT = 2.0
_REFUSED = "The protected native MCP config observation is unavailable."


@dataclass(frozen=True, slots=True)
class ProtectedNativeObservation:
    """Host callback backed only by an approved, protected observer installation."""

    installation: ProtectedObserverInstall = field(repr=False)

    def __call__(self, context: ClientConfigContext, binding: ClientBridgeBinding) -> NativeInspection:
        """Inspect one standard profile and workspace without permitting config writes.

        Raises:
            HeinzelError: If installation, account, confinement or observation evidence is unavailable.
        """
        with step(log, "mcp.client_config.native_probe", client=context.client.value):
            if context.client is McpClient.CURSOR or context.client is not self.installation.client:
                raise HeinzelError(_REFUSED)
            _require_account(context)
            request: dict[str, JsonValue] = {
                "version": _VERSION,
                "operation": "inspect",
                "challenge": secrets.token_hex(32),
                "installation": self.installation.digest,
                "control": self.installation.control_digest,
                "client": context.client.value,
                "home": str(context.home),
                "workspace": str(context.workspace),
                "server": binding.name,
            }
            encoded = (
                json.dumps(request, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
                + b"\n"
            )
            if len(encoded) > _MAX_REQUEST:
                raise HeinzelError(_REFUSED)
            with self.installation.admitted():
                response = _run(self.installation.observer.path, encoded)
                return _inspection(context, request, response)


def _require_account(context: ClientConfigContext) -> None:
    try:
        uid = os.getuid()
        if uid == 0 or os.geteuid() != uid:
            raise HeinzelError(_REFUSED)
        actual = Path(pwd.getpwuid(uid).pw_dir).resolve(strict=True)
        if actual != context.home or context.workspace.resolve(strict=True) != context.workspace:
            raise HeinzelError(_REFUSED)
    except (KeyError, OSError, RuntimeError, ValueError):
        raise HeinzelError(_REFUSED) from None


def _run(observer: Path, encoded: bytes) -> bytes:
    deadline = time.monotonic() + _TIMEOUT
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(  # noqa: S603 - admitted protected image, fixed mode, no shell or user argv
            [str(observer), "observe-mcp-config"],
            cwd="/",
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
            bufsize=0,
        )
        output = _exchange(process, encoded, deadline)
        if process.wait(timeout=_remaining(deadline)) != 0:
            raise HeinzelError(_REFUSED)
    except (OSError, subprocess.TimeoutExpired):
        raise HeinzelError(_REFUSED) from None
    else:
        return output
    finally:
        if process is not None:
            _close_process(process)


def _exchange(process: subprocess.Popen[bytes], encoded: bytes, deadline: float) -> bytes:
    writer, reader = process.stdin, process.stdout
    if writer is None or reader is None:
        raise HeinzelError(_REFUSED)
    os.set_blocking(writer.fileno(), False)
    os.set_blocking(reader.fileno(), False)
    sent = 0
    output = bytearray()
    with selectors.DefaultSelector() as events:
        events.register(writer, selectors.EVENT_WRITE)
        events.register(reader, selectors.EVENT_READ)
        while events.get_map():
            ready = events.select(_remaining(deadline))
            if not ready:
                raise HeinzelError(_REFUSED)
            for key, event in ready:
                if event == selectors.EVENT_WRITE:
                    sent += _write_ready(key.fd, encoded[sent:])
                    if sent == len(encoded):
                        events.unregister(writer)
                        writer.close()
                else:
                    chunk = _read_ready(key.fd, min(65536, _MAX_RESPONSE + 1 - len(output)))
                    if chunk is None:
                        continue
                    if not chunk:
                        events.unregister(reader)
                    output.extend(chunk)
                    if len(output) > _MAX_RESPONSE:
                        raise HeinzelError(_REFUSED)
    return bytes(output)


def _write_ready(descriptor: int, data: bytes) -> int:
    with suppress(BlockingIOError):
        return os.write(descriptor, data)
    return 0


def _read_ready(descriptor: int, limit: int) -> bytes | None:
    with suppress(BlockingIOError):
        return os.read(descriptor, limit)
    return None


def _remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise HeinzelError(_REFUSED)
    return seconds


def _close_process(process: subprocess.Popen[bytes]) -> None:
    try:
        # A process with no collected status owns this PID even if it has just exited.
        if process.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            try:
                process.wait(timeout=_REAP_TIMEOUT)
            except subprocess.TimeoutExpired:
                raise HeinzelError(_REFUSED) from None
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()


def _inspection(context: ClientConfigContext, request: dict[str, JsonValue], encoded: bytes) -> NativeInspection:
    if not encoded.endswith(b"\n") or encoded.count(b"\n") != 1 or len(encoded) > _MAX_RESPONSE:
        raise HeinzelError(_REFUSED)
    try:
        payload = load_config_json(encoded.decode("utf-8"))
    except (ArithmeticError, ValueError, RecursionError):
        raise HeinzelError(_REFUSED) from None
    if not isinstance(payload, dict):
        raise HeinzelError(_REFUSED)
    response = cast("dict[str, object]", payload)
    expected_keys = {"request", "confinement", "managed_policy", "inspection"}
    if (
        response.keys() != expected_keys
        or not same_config_value(response.get("request"), request)
        or response.get("confinement") != "read-only-no-untrusted-exec"
        or response.get("managed_policy") != _managed_policy_evidence()
    ):
        raise HeinzelError(_REFUSED)
    return _fields(context, response.get("inspection"))


def _managed_policy_evidence() -> dict[str, str]:
    """Require the actual platform policy read plus a complete vendor policy evaluation.

    Raises:
        HeinzelError: If the platform has no supported managed policy reader.
    """
    system = platform.system()
    if system == "Darwin":
        return {"native_source": "macos-managed-preferences", "status": "verified"}
    if system == "Linux":
        return {"native_source": "linux-managed-policy", "status": "verified"}
    raise HeinzelError(_REFUSED)


def _fields(context: ClientConfigContext, value: object) -> NativeInspection:
    if not isinstance(value, dict) or value.keys() != {"returncode", "stdout", "stderr", "source", "codex_config_read"}:
        raise HeinzelError(_REFUSED)
    result = cast("dict[str, object]", value)
    returncode = result.get("returncode")
    stdout, stderr = result.get("stdout"), result.get("stderr")
    source, config = result.get("source"), result.get("codex_config_read")
    if (
        type(returncode) is not int
        or returncode not in {0, 1}
        or not isinstance(stdout, str)
        or not isinstance(stderr, str)
        or len(stdout) + len(stderr) > MAX_NATIVE_CONFIG_OUTPUT
        or source not in (None, str(context.path))
        or (returncode != 0 and source is not None)
    ):
        raise HeinzelError(_REFUSED)
    if context.client is McpClient.CODEX:
        if not isinstance(config, str):
            raise HeinzelError(_REFUSED)
    elif config is not None:
        raise HeinzelError(_REFUSED)
    return NativeInspection(
        returncode,
        stdout,
        stderr,
        context.path if source else None,
        context=context,
        codex_config_read=config,
    )
