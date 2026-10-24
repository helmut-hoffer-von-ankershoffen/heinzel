"""A fake GitHub CLI: answers `gh --version` and `gh api` from a routes file and records every call. Offline.

conftest.py installs it as an executable named `gh` on a `PATH` of its own, with `ROUTES` and `LOG` (the routes file
and the call log) written above this docstring, so the adapter's real `Gh` runner starts it as it would the real one.
The routes map `<path>?page=<n>` to an answer: `fixture` (a file in `tests/fixtures`) or `stdout`, plus optional
`stderr`, `exit`, `sleep` (seconds before answering), `linger` (seconds after) and `pad` (extra bytes of output);
`version` is what `gh --version` prints. Each call is appended to the log as JSON: its arguments, its whole
environment and its working directory.
"""

import json
import os
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    """Answer one `gh` call; the exit code is the fake's answer."""
    routes = json.loads(Path(ROUTES).read_text(encoding="utf-8"))  # noqa: F821 - written by conftest.py
    with Path(LOG).open("a", encoding="utf-8") as log:  # noqa: F821 - written by conftest.py
        log.write(json.dumps({"argv": argv, "env": dict(os.environ), "cwd": str(Path.cwd())}) + "\n")
    if argv == ["--version"]:
        answer = routes.get("version", {"stdout": "gh version 2.103.2 (2026-10-23)\nhttps://example.invalid\n"})
        time.sleep(answer.get("sleep", 0))
        sys.stdout.write(answer["stdout"])
        return 0
    path = next(arg for arg in argv if arg.startswith("repos/"))
    fields = dict(argv[i + 1].split("=", 1) for i, arg in enumerate(argv) if arg == "--raw-field")
    answer = routes.get("api", {}).get(f"{path}?page={fields.get('page', '1')}")
    if answer is None:
        sys.stdout.write('{"message": "Not Found", "status": "404"}')
        sys.stderr.write("gh: Not Found (HTTP 404)\n")
        return 1
    time.sleep(answer.get("sleep", 0))
    if "fixture" in answer:
        data = (Path(routes["fixtures"]) / answer["fixture"]).read_bytes()
    else:
        data = answer.get("stdout", "").encode()
    sys.stdout.buffer.write(data + b" " * answer.get("pad", 0))
    sys.stdout.flush()
    sys.stderr.write(answer.get("stderr", ""))
    sys.stderr.flush()
    time.sleep(answer.get("linger", 0))
    return int(answer.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
