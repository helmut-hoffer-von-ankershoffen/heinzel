# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Say whether the Claude review can run, and what a review that failed means, for the review workflow.

`probe` runs before the review. It asks the Anthropic API for one token with the review's key. When the account can't
serve a request for a reason outside the pull request (credit balance, usage or spending limit, rate limit, overload),
there is nothing to review with: it writes `ready=false` and the reason, and the workflow reports a neutral "skipped"
check instead of a review. Any other answer lets the review run, so a bad key still fails the review with its own error.

`judge EXECUTION_FILE` runs after a review step failed. A failed review never passes: it prints the error the action
hides from its log, says whether the review could not start or failed, and exits 1.

Usage: python3 scripts/review_outcome.py probe
       python3 scripts/review_outcome.py judge EXECUTION_FILE
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.anthropic.com/v1"
VERSION = "2023-06-01"
EXTERNAL_LIMIT = re.compile(
    r"credit balance|usage limit|spending limit|rate.?limit|hit your .*limit|overloaded|API Error: (429|5\d\d)",
    re.IGNORECASE,
)
LIMIT_STATUS = {429, 529}
OK = range(200, 300)
JUDGE_ARGS = 3
MAX_SHOWN = 300
TIMEOUT = 30


def one_line(text: object) -> str:
    """Text on one short line that cannot start a workflow command."""
    return " ".join(str(text).split()).replace("::", ": :")[:MAX_SHOWN]


def api_error(body: str) -> str:
    """The error message of an Anthropic API response body, or the body itself."""
    try:
        data = json.loads(body)
    except ValueError:
        return body
    error = data.get("error") if isinstance(data, dict) else None
    return str(error.get("message") or error) if isinstance(error, dict) else body


def verdict(status: int, body: str) -> tuple[bool, str]:
    """(ready, reason) from one API answer: not ready only on a limit outside the pull request."""
    if status in OK:
        return True, "the API key can make requests"
    message = one_line(api_error(body))
    if status in LIMIT_STATUS or EXTERNAL_LIMIT.search(message):
        return False, f"{message} (HTTP {status})"
    return True, f"the API answered HTTP {status}: {message}; the review runs and reports its own error"


def request(method: str, path: str, key: str, payload: dict[str, object] | None = None) -> tuple[int, str]:
    """Status and body of one API request; status 0 when the API can't be reached."""
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"x-api-key": key, "anthropic-version": VERSION, "content-type": "application/json"}
    req = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)  # noqa: S310 - fixed https URL
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:  # noqa: S310 - fixed https URL
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, str(error)


def cheapest_model(body: str) -> str | None:
    """A Haiku model from a models listing (the cheapest for one token), else the first model listed."""
    try:
        ids = [str(m["id"]) for m in json.loads(body)["data"]]
    except (ValueError, KeyError, TypeError):
        return None
    return next((i for i in ids if "haiku" in i), ids[0] if ids else None)


def probe(env: dict[str, str]) -> tuple[bool, str]:
    """(ready, reason) for the credentials the review would use."""
    key = env.get("ANTHROPIC_API_KEY", "")
    if not key:
        if env.get("CLAUDE_CODE_OAUTH_TOKEN"):
            return True, "subscription token, not probed; the review reports its own error"
        return False, "no ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN secret is set"
    status, body = request("GET", "/models?limit=100", key)
    if status == 0:
        return True, f"the API could not be reached ({one_line(body)}); the review runs and reports its own error"
    model = cheapest_model(body) if status in OK else None
    if model is None:
        return verdict(status, body)
    message = {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ok"}]}
    status, body = request("POST", "/messages", key, message)
    if status == 0:
        return True, f"the API could not be reached ({one_line(body)}); the review runs and reports its own error"
    return verdict(status, body)


def result_message(execution_file: Path) -> dict[str, object] | None:
    """The last result message of the run, or None if the file is missing, unreadable or has none."""
    try:
        messages = json.loads(execution_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(messages, list):
        return None
    results = [m for m in messages if isinstance(m, dict) and m.get("type") == "result"]
    return results[-1] if results else None


def could_not_start(result: dict[str, object] | None) -> bool:
    """Whether the run failed on an external limit before doing any work."""
    if result is None or not result.get("is_error") or result.get("total_cost_usd") != 0:
        return False
    return bool(EXTERNAL_LIMIT.search(str(result.get("result", ""))))


def shown(result: dict[str, object] | None) -> str:
    """The error text of a result message on one line."""
    if result is None:
        return "no result message"
    return one_line(result.get("result") or result.get("errors") or result.get("subtype") or "no error text")


def summarize(line: str) -> None:
    """Add a line to the step summary, when there is one."""
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as out:
            out.write(f"{line}\n")


def main(argv: list[str]) -> int:
    """Run `probe` or `judge`; the exit code is the step's."""
    command = argv[1] if len(argv) > 1 else ""
    if command == "probe":
        ready, reason = probe(dict(os.environ))
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with Path(output).open("a", encoding="utf-8") as out:
                out.write(f"ready={str(ready).lower()}\nreason={one_line(reason)}\n")
        line = f"Claude review {'can run' if ready else 'skipped'}: {one_line(reason)}"
        print(line if ready else f"::notice::{line}")
        summarize(line)
        return 0
    if command == "judge" and len(argv) == JUDGE_ARGS:
        result = result_message(Path(argv[2]))
        kind = "skipped, it could not start" if could_not_start(result) else "failed"
        line = f"Claude review {kind}, nothing was reviewed: {shown(result)}"
        print(f"::error::{line}")
        summarize(line)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
