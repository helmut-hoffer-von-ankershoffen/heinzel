"""The Codex CLI as AI access option and agent runner, through the CLI head, with a fake `codex` on PATH (ADR 0057).

The fake records its arguments and standard input and answers like `codex login status` and `codex exec --json`.
No test runs the real Codex.
"""

import json
import os
import sys

import pytest
from typer.testing import CliRunner

from heinzel import Config, Kernel
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

FAKE = """
import json, pathlib, sys
log = pathlib.Path(sys.argv[0]).with_name("calls.jsonl")
args = sys.argv[1:]
if args[:2] == ["login", "status"]:
    log.open("a").write(json.dumps({"args": args}) + "\\n")
    print("Logged in using ChatGPT")
    sys.exit(0)
prompt = sys.stdin.read()
log.open("a").write(json.dumps({"args": args, "stdin": prompt}) + "\\n")
for event in (
    {"type": "thread.started", "thread_id": "t-1"},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "OK from codex"}},
    {"type": "turn.completed", "usage": {"input_tokens": 50, "cached_input_tokens": 10, "output_tokens": 3}},
):
    print(json.dumps(event))
"""


@pytest.fixture
def codex(tmp_path, monkeypatch):
    """A `codex` on PATH that records each call in calls.jsonl next to it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "codex"
    script.write_text(f"#!{sys.executable}\n{FAKE}", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir / "calls.jsonl"


def hz(kernel: Kernel, *args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    assert result.output.startswith("{"), result.output
    return result.exit_code, json.loads(result.output)


def calls(log) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_codex_is_an_access_option_that_is_signed_in_and_tests_read_only(make_kernel, codex):
    kernel = make_kernel(builtins=True)
    _, listing = hz(kernel, "ai", "access", "list")
    [option] = [o for o in listing["options"] if o["name"] == "codex"]
    assert (option["status"], option["runner"]) == ("signed in", "codex")
    code, report = hz(kernel, "ai", "access", "test", "--name", "codex")
    assert (code, report["ok"], report["detail"]) == (0, True, "OK from codex")
    [*_, run] = calls(codex)
    assert run["args"][:6] == ["exec", "--json", "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check"]
    assert run["stdin"] == "Reply with the single word OK."


def test_the_chat_and_questions_can_run_on_codex(make_kernel, codex):
    config = Config(files=(), data={"capabilities": {"agent_runner": "codex"}})
    kernel = make_kernel(builtins=True, config=config)
    code, answer = hz(kernel, "system", "runner", "ask", "What is hz?")
    assert (code, answer["status"], answer["runner"], answer["draft"]) == (0, "completed", "codex", "OK from codex")
    assert answer["usage"]["cache_read_tokens"] == 10
    assert any("prevents writes, not reads" in warning for warning in answer["warnings"])
    [run] = [call for call in calls(codex) if call["args"][0] == "exec"]
    assert "What is hz?" in run["stdin"]


def test_doctor_checks_the_bound_codex_login(make_kernel, codex):
    config = Config(files=(), data={"capabilities": {"agent_runner": "codex"}})
    _, report = hz(make_kernel(builtins=True, config=config), "system", "doctor")
    [check] = report["checks"]["system.runner"]
    assert (check["name"], check["status"]) == ("codex", "up")
