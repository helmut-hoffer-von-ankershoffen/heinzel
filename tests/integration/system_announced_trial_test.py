"""The trial start of an announced update runs a real server process: an integration test (ADR 0116)."""

import json
import sys
from pathlib import Path

import pytest

from heinzel.modules.system import _announced as announced

pytestmark = pytest.mark.integration

# The stand-in server starts as `python -I -S`: no site-packages to scan, which on a loaded macOS runner took the child
# longer to print its link than the unit test's 5 seconds (main run 41473262545). It needs only the standard library.
TRIAL_SERVER = """
import http.server, json, os, pathlib, socketserver, sys
for variable in ("HZ_STATE_DIR", "HZ_DATA_DIR", "HZ_CACHE_DIR", "HZ_LOG_DIR"):
    pathlib.Path(os.environ[variable], "written-by-the-trial").write_text("x")
config = pathlib.Path(os.environ["HZ_CONFIG_HOME"], "hz.toml")
config.write_text(config.read_text() + "written = true\\n")
pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(os.environ)))

class Lifecycle(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"version": "9.9.9"}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

class LoopbackHTTPServer(http.server.HTTPServer):
    def server_bind(self):  # HTTPServer would ask DNS for a display name, which can stall for seconds on macOS
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

server = LoopbackHTTPServer(("127.0.0.1", 0), Lifecycle)
print(f"http://127.0.0.1:{server.server_port}/#token=t", flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
"""


def test_a_trial_start_runs_on_throwaway_folders_and_leaves_the_live_ones_untouched(tmp_path, monkeypatch):
    live = {name: tmp_path / "live" / name for name in ("config", "state", "data", "cache", "log")}
    for folder in live.values():
        folder.mkdir(parents=True)
    (live["config"] / "hz.toml").write_text("[hz]\nui_port = 8737\n")
    for name, variable in [("state", "HZ_STATE_DIR"), ("data", "HZ_DATA_DIR"), ("cache", "HZ_CACHE_DIR")]:
        monkeypatch.setenv(variable, str(live[name]))
    monkeypatch.setenv("HZ_LOG_DIR", str(live["log"]))
    monkeypatch.setenv("HZ_CONFIG_HOME", str(live["config"]))
    seen = tmp_path / "seen.json"
    script = tmp_path / "trial.py"
    script.write_text(TRIAL_SERVER)

    assert (
        announced.trial_start("9.9.9", timeout=30, command=[sys.executable, "-I", "-S", str(script), str(seen)]) is True
    )

    assert (live["config"] / "hz.toml").read_text() == "[hz]\nui_port = 8737\n"
    assert [p.name for f in live.values() for p in f.iterdir()] == ["hz.toml"]  # nothing written anywhere live
    env = json.loads(seen.read_text())
    assert (env["HZ_AUTO_UPDATE"], env["HZ_UPDATE_CHECK"]) == ("false", "false")
    scratch = Path(env["HZ_STATE_DIR"]).parent
    assert scratch.name.startswith("hz-trial-")
    assert not scratch.exists()  # deleted with everything the trial wrote


def test_a_trial_start_that_answers_another_version_fails(tmp_path):
    seen, script = tmp_path / "seen.json", tmp_path / "trial.py"
    script.write_text(TRIAL_SERVER)
    assert (
        announced.trial_start("1.0.0", timeout=30, command=[sys.executable, "-I", "-S", str(script), str(seen)])
        is False
    )
