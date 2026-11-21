"""`python -m heinzel._lifecycle_control`: the pipe entry the desktop apps call; the logic lives in lifecycle.

The path is a contract with already installed desktop apps, so it stays. It owns only the pipe: one bounded read of
standard input and one JSON line on standard output.
"""

import json
import sys

from heinzel.modules.system.lifecycle._control import MAX_REQUEST, answer

__all__ = ["MAX_REQUEST", "main"]


def main() -> None:
    """Read one bounded private pipe message and print the answer."""
    sys.stdout.write(json.dumps(answer(sys.stdin.buffer.read(MAX_REQUEST + 1))) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
