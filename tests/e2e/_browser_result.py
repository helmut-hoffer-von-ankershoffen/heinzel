"""Keep fixture subprocess arguments out of browser-failure output."""

import subprocess


def check_browser(status, safe_output):
    """Raise with already-redacted text without pytest rendering the token-bearing expression."""
    if status != 0:
        raise AssertionError(safe_output)


def wait_browser(guarded, timeout):
    """Keep the original timeout while withholding the token-bearing command on failure."""
    try:
        return guarded.wait(timeout)
    except subprocess.TimeoutExpired:
        return None


def run_browser(command, **kwargs):
    """Run the original command and withhold token-bearing arguments on timeout."""
    try:
        return subprocess.run(command, check=False, **kwargs)
    except subprocess.TimeoutExpired:
        return None


def require_browser(value):
    """Turn the timeout sentinel into a fixed error without a command in the traceback."""
    if value is None:
        msg = "The browser fixture timed out."
        raise AssertionError(msg)
    return value
