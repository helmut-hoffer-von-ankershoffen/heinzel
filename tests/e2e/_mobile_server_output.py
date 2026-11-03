"""Bounded private server stderr capture for the mounted mobile test."""

import re
import threading

_TAIL = 32768
_LINE = 8192


def redact(text, token=""):
    """Remove URL fragments and authorization values before any diagnostic is printed."""
    if token:
        text = text.replace(token, "<redacted>")
    text = re.sub(r"(?i)(#token=|[?&]token=)[^\s&#]+", r"\1<redacted>", text)
    return re.sub(
        r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?(?:bearer\s+)?|bearer\s+)[^\s,;\"'}]+", r"\1<redacted>", text
    )


def check_driver(returncode, safe_stderr):
    """Report a driver failure using only an already-redacted message."""
    if returncode != 0:
        raise AssertionError(safe_stderr)


_MOBILE_STAGES = frozenset(
    {
        "launch",
        "launched",
        "phone-scroll",
        "phone-sound",
        "phone-notifications",
        "failure-diagnostics",
        "browser-cleanup",
        "complete",
    }
    | {
        f"viewport-{view}-{phase}"
        for view in (
            "narrow-portrait",
            "wide-portrait",
            "short-portrait",
            "narrow-landscape",
            "wide-landscape",
            "short-landscape",
        )
        for phase in ("start", "loaded", "routes", "settings", "cleanup", "done")
    }
)


def timeout_stage(stderr):
    """Extract only the last exact allowlisted checkpoint from captured driver stderr."""
    if not isinstance(stderr, (bytes, str)):
        return "unavailable"
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    found = "unavailable"
    for line in stderr.splitlines():
        if line.startswith("MOBILE_STAGE:") and line[13:] in _MOBILE_STAGES:
            found = line[13:]
    return found


def checkpoint_seconds(stderr):
    """Only the digits of the driver's elapsed time at each checkpoint, so a timeout shows where the time went."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    if not isinstance(stderr, str):
        return "unavailable"
    return " ".join(line[15:] for line in stderr.splitlines() if re.fullmatch(r"MOBILE_ELAPSED:\d+", line))


def sanitize_timeout(error):
    """Keep timeout type and deadline while withholding the command and captured output."""
    stage = timeout_stage(error.stderr)
    error.add_note(f"Mobile driver last checkpoint: {stage}")
    error.add_note(f"Mobile driver seconds per checkpoint: {checkpoint_seconds(error.stderr)}")
    error.cmd = ["<mobile-browser-command>"]
    error.args = (error.cmd, error.timeout)
    error.output = None
    error.stderr = None


class MobileServerOutput:
    """Drain stderr in bounded chunks while discovering the startup URL."""

    def __init__(self, process):
        self.process = process
        self.tail = b""
        self.url = None
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        stream = self.process.stderr
        assert stream is not None
        line = b""
        overflow = False
        try:
            while chunk := stream.read1(4096):
                with self.lock:
                    self.tail = (self.tail + chunk)[-_TAIL:]
                for part in chunk.splitlines(keepends=True):
                    if not overflow:
                        line += part
                        if len(line) > _LINE:
                            line = b""
                            overflow = True
                    if part.endswith((b"\n", b"\r")):
                        if not overflow and b"runs at" in line:
                            candidate = line.decode("utf-8", errors="replace").strip().rsplit(" ", 1)[-1]
                            if candidate.startswith("http://127.0.0.1:") and "#token=" in candidate:
                                self.url = candidate
                                self.ready.set()
                        line = b""
                        overflow = False
        finally:
            self.ready.set()

    def wait_url(self):
        """Wait for URL or owned process exit; the enclosing E2E timeout remains the deadline."""
        while not self.ready.wait(0.1):
            if self.process.poll() is not None:
                break
        if self.url is None:
            msg = "The mobile test server did not announce its loopback URL"
            raise RuntimeError(msg)
        return self.url

    def diagnostic(self, token=""):
        if not token:
            return "Unavailable: no verified startup token was captured."
        with self.lock:
            tail = self.tail
        if len(tail) == _TAIL:
            _, separator, tail = tail.partition(b"\n")
            if not separator:
                return "Unavailable: no complete stderr line in the bounded tail."
        return redact(tail.decode("utf-8", errors="replace"), token)

    def close(self):
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            msg = "Mobile test server stderr did not close"
            raise RuntimeError(msg)
