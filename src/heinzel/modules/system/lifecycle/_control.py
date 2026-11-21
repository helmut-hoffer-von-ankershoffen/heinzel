"""Answer one closed private lifecycle pipe request without entering ordinary CLI writer ownership.

The pipe itself (standard input and output) belongs to the entry point, `heinzel._lifecycle_control`; this module takes
the bytes and returns the answer, so it writes nothing to a terminal.
"""

from heinzel import HeinzelError
from heinzel.modules.system.lifecycle._authority import Request, execute

MAX_REQUEST = 128 * 1024


def _request(encoded: bytes) -> Request:
    if len(encoded) > MAX_REQUEST:
        msg = "The lifecycle control request exceeds its budget"
        raise ValueError(msg)
    return Request.model_validate_json(encoded)


def answer(encoded: bytes) -> dict[str, object]:
    """Run one bounded private request and return a safe structured result without raw paths or credentials."""
    try:
        return {"ok": True, "value": execute(_request(encoded))}
    except (OSError, ValueError, HeinzelError):
        return {"ok": False, "error": "The owned lifecycle action was refused; inspect this profile."}
