"""A fake GitHub CLI: answers `gh api` and `gh auth token` from a routes file and records every call. Offline.

conftest.py installs it as an executable named `gh` on a `PATH` of its own, so the adapter's real `Gh` runner starts
it as it would start the real one. The routes file (`FAKE_GH_ROUTES`) maps `<path>?page=<n>` to an answer:
`fixture` (a file in `tests/fixtures`) or `stdout`, plus optional `stderr`, `exit`, `sleep` (seconds) and `pad` (extra
bytes of output); `logins` lists the hosts `gh auth token` has a login for. Each call is appended to `FAKE_GH_LOG` as
JSON: its arguments and the `GH_*`, `HZ_*`, `DEBUG` and `NO_COLOR` variables it saw.
"""

import json
import os
import sys
import time
from pathlib import Path

RECORDED = ("GH_", "HZ_", "DEBUG", "NO_COLOR")


def main(argv: list[str]) -> int:
    """Answer one `gh` call; the exit code is the fake's answer."""
    routes = json.loads(Path(os.environ["FAKE_GH_ROUTES"]).read_text(encoding="utf-8"))
    env = {key: value for key, value in os.environ.items() if key.startswith(RECORDED)}
    with Path(os.environ["FAKE_GH_LOG"]).open("a", encoding="utf-8") as log:
        log.write(json.dumps({"argv": argv, "env": env}) + "\n")
    if argv[:2] == ["auth", "token"]:
        host = argv[argv.index("--hostname") + 1]
        if host in routes.get("logins", []):
            sys.stdout.write("gho_fakeTokenOnlyTheFakeGhPrints0000\n")
            return 0
        sys.stderr.write(f"no oauth token found for {host}\n")
        return 1
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
    return int(answer.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
