"""Present one closed private lifecycle pipe request without entering ordinary CLI writer ownership."""

import json
import sys

from heinzel import HeinzelError
from heinzel.modules.system.lifecycle._authority import Request, execute

MAX_REQUEST = 128 * 1024


def _request() -> Request:
    encoded = sys.stdin.buffer.read(MAX_REQUEST + 1)
    if len(encoded) > MAX_REQUEST:
        msg = "The lifecycle control request exceeds its budget"
        raise ValueError(msg)
    return Request.model_validate_json(encoded)


def main() -> None:
    """Read one bounded private pipe message and emit safe structured results without raw paths or credentials."""
    try:
        result = execute(_request())
    except (OSError, ValueError, HeinzelError):
        answer: dict[str, object] = {
            "ok": False,
            "error": "The owned lifecycle action was refused; inspect this profile.",
        }
    else:
        answer = {"ok": True, "value": result}
    sys.stdout.write(json.dumps(answer) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
