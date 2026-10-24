import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci_logs.py"
spec = importlib.util.spec_from_file_location("ci_logs", SCRIPT)
assert spec
assert spec.loader
ci_logs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci_logs)


def thread(path: str, line: int | None, author: str | None, body: str, *, resolved: bool = False) -> dict:
    """One node of a GraphQL `reviewThreads` response."""
    comments = [{"author": {"login": author} if author else None, "body": body}]
    return {"isResolved": resolved, "path": path, "line": line, "comments": {"nodes": comments}}


def response(*threads: dict) -> dict:
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": list(threads)}}}}}


ALERT = "## CodeQL / Statement has no effect\n\nThis statement has no effect.\n\n[Show more details](https://x/12)"


def test_lists_unresolved_threads_with_path_line_author_and_first_line():
    found = ci_logs.unresolved_threads(
        response(
            thread("src/a.py", 7, "github-advanced-security", ALERT),
            thread("src/b.py", 3, "someone", "Done.", resolved=True),
            thread("README.md", None, None, "\n  Typo here?\nSecond line"),
        )
    )
    assert found == [
        {
            "job": "review thread",
            "step": "src/a.py:7 (github-advanced-security)",
            "lines": ["CodeQL / Statement has no effect"],
        },
        {"job": "review thread", "step": "README.md (ghost)", "lines": ["Typo here?"]},
    ]


def test_no_threads_when_all_are_resolved():
    assert ci_logs.unresolved_threads(response(thread("a.py", 1, "x", "ok", resolved=True))) == []


def fake_gh(monkeypatch, state: str, threads: dict) -> list[list[str]]:
    """Answer `gh pr view` with a pull request in `state` and `gh api graphql` with `threads`; record the calls."""
    calls: list[list[str]] = []

    def run(args, **_):
        calls.append(args)
        if args[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(args, 0, json.dumps({"number": 5, "mergeStateStatus": state}), "")
        return subprocess.CompletedProcess(args, 0, json.dumps(threads), "")

    monkeypatch.setattr(ci_logs.subprocess, "run", run)
    return calls


def test_blocked_pull_request_lists_its_threads(monkeypatch):
    calls = fake_gh(monkeypatch, "BLOCKED", response(thread("a.py", 2, "bot", "Fix this")))
    assert ci_logs.blocking_threads("feat/x") == [
        {"job": "review thread", "step": "a.py:2 (bot)", "lines": ["Fix this"]}
    ]
    assert "number=5" in calls[-1]


def test_unblocked_pull_request_asks_for_no_threads(monkeypatch):
    calls = fake_gh(monkeypatch, "CLEAN", response())
    assert ci_logs.blocking_threads("feat/x") == []
    assert len(calls) == 1


def test_branch_without_pull_request_has_no_threads(monkeypatch):
    monkeypatch.setattr(ci_logs.subprocess, "run", lambda args, **_: subprocess.CompletedProcess(args, 1, "", "no PR"))
    assert ci_logs.blocking_threads("feat/x") == []
