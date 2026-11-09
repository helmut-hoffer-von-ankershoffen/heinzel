"""Terminal reviewer loaded from verified source bytes in the isolated local CLI."""

import hashlib
import json
import os
import re
import secrets
import select
import socket
import termios
import time

_REVIEW_BYTES = 16 * 1024
_ANSWER_BYTES = 64
_REVIEW_SECONDS = 60
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _read_line(descriptor: int, limit: int, deadline: float) -> bytes:
    answer = bytearray()
    while len(answer) <= limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([descriptor], [], [], remaining)[0]:
            return b""
        try:
            chunk = os.read(descriptor, limit + 1 - len(answer))
        except BlockingIOError:
            continue
        if not chunk:
            return b""
        answer.extend(chunk)
        if b"\n" in chunk:
            return (
                bytes(answer) if len(answer) <= limit and answer.endswith(b"\n") and answer.count(b"\n") == 1 else b""
            )
    return b""


def _write(descriptor: int, data: bytes, deadline: float) -> bool:
    while data:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([], [descriptor], [], remaining)[1]:
            return False
        try:
            written = os.write(descriptor, data)
        except BlockingIOError:
            continue
        if written <= 0:
            return False
        data = data[written:]
    return True


def _confirm(terminal: int, channel: int, deadline: float) -> bool:
    offer = json.loads(_read_line(channel, _REVIEW_BYTES, deadline))
    if not isinstance(offer, dict) or set(offer) != {"review", "digest"}:
        return False
    review, digest = offer["review"], offer["digest"]
    if not isinstance(review, dict) or not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        return False
    # Match the broker's versioned canonical arguments hash without importing the application.
    canonical = json.dumps(review, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    actual = hashlib.sha256(b"hz-approval-arguments-v1\0" + canonical.encode("ascii")).hexdigest()
    if not secrets.compare_digest(digest, actual):
        return False
    challenge = f"approve MCP {secrets.token_hex(4)}"
    visible = {key: value for key, value in review.items() if key != "challenge"}
    shown = json.dumps(visible, ensure_ascii=True, sort_keys=True, indent=2)
    subject = (
        "MCP foreground lifecycle action" if review.get("kind") == "foreground-lifecycle" else "private MCP connection"
    )
    prompt = f"Review this exact {subject}:\n{shown}\n\nType exactly: {challenge}\n> "
    termios.tcflush(terminal, termios.TCIFLUSH)
    return (
        _write(terminal, prompt.encode("ascii"), deadline)
        and secrets.compare_digest(_read_line(terminal, _ANSWER_BYTES, deadline), f"{challenge}\n".encode("ascii"))
        and _write(channel, f"approve {digest}\n".encode("ascii"), deadline)
    )


def review(control: socket.socket) -> bool:
    """Use only the broker's unnamed socket and a live foreground controlling terminal."""
    try:
        if (
            control.family != socket.AF_UNIX
            or control.type != socket.SOCK_STREAM
            or control.getsockname() != ""
            or control.getpeername() != ""
        ):
            return False
        deadline = time.monotonic() + _REVIEW_SECONDS
        terminal = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            if not os.isatty(terminal) or os.tcgetpgrp(terminal) != os.getpgrp():
                return False
            descriptor = control.fileno()
            os.set_blocking(descriptor, False)
            return _confirm(terminal, descriptor, deadline)
        finally:
            os.close(terminal)
    except (OSError, OverflowError, RecursionError, ValueError):
        return False
