"""`McpConnector` over stdio with a real launcher subprocess: start, call, stop, and every way starting fails."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import psutil
import pytest

from heinzel.connectors.mcp import INLINE_UNTRUSTED, McpConnector, McpConnectorError, McpToolsMissingError

pytestmark = pytest.mark.integration

SERVER = textwrap.dedent(
    """
    import os, sys
    from mcp.server import MCPServer

    mcp = MCPServer("launcher")

    @mcp.tool()
    def search_mail(query: str) -> str:
        return "«untrusted-data»" + query + " from " + os.environ.get("ACME_REGION", "?") + "«/untrusted-data»"

    @mcp.tool()
    def pid() -> str:
        return str(os.getpid())

    if __name__ == "__main__":
        mcp.run("stdio")
    """
)


@pytest.fixture
def launcher(tmp_path: Path) -> list[str]:
    script = tmp_path / "launcher.py"
    script.write_text(SERVER, encoding="utf-8")
    return [sys.executable, str(script)]


def test_starts_a_launcher_calls_it_and_stops_it(launcher: list[str]):
    connector = McpConnector(
        launcher, tools=["search_mail", "pid"], markers=[INLINE_UNTRUSTED], env={"ACME_REGION": "eu"}, timeout=10
    )
    with connector:
        result = connector.call("search_mail", {"query": "plan"})
        process = psutil.Process(int(connector.call("pid").text))
    assert result.text == "plan from eu"
    assert result.untrusted == ("plan from eu",)
    process.wait(timeout=5)  # gone once stop() returned, or a moment later
    assert not process.is_running()


def test_a_missing_tool_stops_the_launcher_and_says_what_it_offers(launcher: list[str]):
    with pytest.raises(McpToolsMissingError, match=r"lacks the tools calendar_events .* it offers pid, search_mail"):
        McpConnector(launcher, tools=["calendar_events"]).start()


def test_a_launcher_that_fails_to_start_reports_its_last_stderr_line(tmp_path: Path):
    script = tmp_path / "expired.py"
    script.write_text("import sys\nprint('sign in again: acme-sso login', file=sys.stderr)\nsys.exit(3)\n")
    connector = McpConnector([sys.executable, str(script)], tools=["x"], start_timeout=10)
    with pytest.raises(McpConnectorError, match=r"didn't start .*It said: sign in again: acme-sso login"):
        connector.start()
    assert not connector.started


def test_a_missing_program_is_a_clear_error():
    with pytest.raises(McpConnectorError, match="The MCP server no-such-launcher-hz didn't start"):
        McpConnector(["no-such-launcher-hz"], tools=["x"], start_timeout=5).start()


def test_a_launcher_that_never_answers_times_out_and_is_stopped(tmp_path: Path):
    script = tmp_path / "silent.py"
    script.write_text("import time\nwhile True:\n    time.sleep(1)\n")
    connector = McpConnector([sys.executable, str(script)], tools=["x"], start_timeout=1, stop_timeout=2)
    with pytest.raises(McpConnectorError, match="didn't start within 1s"):
        connector.start()
    children = [p for p in psutil.Process(os.getpid()).children(recursive=True) if str(script) in " ".join(p.cmdline())]
    assert children == []


GRANDCHILD = textwrap.dedent(
    """
    import os, subprocess, time
    from mcp.server import MCPServer

    mcp = MCPServer("launcher")

    @mcp.tool()
    def hang() -> str:
        child = subprocess.Popen(["sleep", "300"])
        with open(os.environ["PID_FILE"], "w") as file:
            file.write(str(child.pid))
        time.sleep(300)
        return ""

    if __name__ == "__main__":
        mcp.run("stdio")
    """
)


@pytest.mark.timeout(60)
@pytest.mark.parametrize(("attempt", "shebang"), [(0, False), (1, True), (2, True)], ids=["python", "shebang", "again"])
def test_stop_kills_the_whole_launcher_group_even_with_a_short_stop_timeout(
    tmp_path: Path, attempt: int, *, shebang: bool
):
    """Also a launcher run by name through its `#!` line, whose process shows its interpreter, not its name."""
    script, pid_file = tmp_path / "acme-mcp", tmp_path / f"grandchild-{attempt}"
    script.write_text(f"#!{sys.executable}\n{GRANDCHILD}", encoding="utf-8")
    script.chmod(0o755)
    command = [str(script), "launch", "mail"] if shebang else [sys.executable, str(script)]
    connector = McpConnector(command, tools=["hang"], env={"PID_FILE": str(pid_file)}, timeout=1, stop_timeout=1)
    with connector, pytest.raises(McpConnectorError, match="didn't answer"):
        connector.call("hang")
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    assert not _alive(grandchild), "the launcher's grandchild outlived stop()"
    leftovers = [p for p in psutil.Process().children(recursive=True) if str(script) in " ".join(p.cmdline())]
    assert leftovers == [], "the launcher outlived stop()"


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


HANGING_KIT = textwrap.dedent(
    """
    import threading
    from heinzel.conformance import check_adapter

    class Hanging:
        def models(self):
            threading.Event().wait()

        def model(self, model_id):
            raise AssertionError

    import heinzel.kernel._ai_models as ai_models
    ai_models.LIST_TIMEOUT = 0.2
    print(check_adapter(Hanging(), "ai-models", name="acme").failures[0])
    """
)


def test_the_kit_finishes_and_the_process_exits_after_a_call_that_never_returns(tmp_path: Path):
    script = tmp_path / "hanging_kit.py"
    script.write_text(HANGING_KIT, encoding="utf-8")
    finished = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=30, check=False)
    assert finished.returncode == 0, finished.stderr
    assert "models() took longer than 0.2s" in finished.stdout
