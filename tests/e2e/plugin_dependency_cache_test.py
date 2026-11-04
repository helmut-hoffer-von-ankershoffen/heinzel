"""A new plugin uses fresh dependency artifacts without forcing unrelated HTTP refreshes."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from heinzel import Progress
from heinzel.kernel import Installation
from heinzel.modules.system.plugin._models import PluginSettings
from heinzel.modules.system.plugin._service import PluginService
from tests.e2e.tool_install_test import Sandbox
from tests.e2e.uv_tool_collision_test import _fixture_wheel

pytestmark = pytest.mark.e2e


def test_a_new_plugin_keeps_fresh_dependency_cache_when_its_server_is_unavailable(
    tmp_path, fixture_managed_python, make_kernel
):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    dependency = _fixture_wheel(wheels, "1.0.0", [], name="fixture_dependency")
    requests = []
    available = True

    class Wheels(BaseHTTPRequestHandler):
        def respond(self, *, body):
            requests.append(self.path)
            if not available:
                self.send_error(503)
                return
            assert self.path == f"/{dependency.name}"
            data = dependency.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            if body:
                self.wfile.write(data)

        def do_GET(self):
            self.respond(body=True)

        def do_HEAD(self):
            self.respond(body=False)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Wheels) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/{dependency.name}"
            core = _fixture_wheel(wheels, "1.0.0", ["hz"], name="heinzel", requirements=[f"fixture-dependency @ {url}"])
            plugin = _fixture_wheel(wheels, "2.0.0", [], name="fixture_plugin", requirements=["heinzel"])
            sandbox = Sandbox(tmp_path / "sandbox", *fixture_managed_python)
            sandbox.run("uv", "tool", "install", core)
            assert requests
            requests.clear()
            available = False
            installation = Installation(Progress(), prefix=sandbox.root / "tools/heinzel", uv="uv")
            service = PluginService(make_kernel(), installation, PluginSettings())
            command = service.add_command(f"fixture-plugin @ {plugin.as_uri()}")
            sandbox.run(*command, UV_HTTP_RETRIES="0")
            assert requests == []
            python = sandbox.root / "tools/heinzel/bin/python"
            installed = sandbox.run(
                python,
                "-c",
                "import importlib.metadata as m, json; "
                "print(json.dumps({d.metadata['Name']: d.version for d in m.distributions()}))",
            )
            assert json.loads(installed) == {
                "heinzel": "1.0.0",
                "fixture-plugin": "2.0.0",
                "fixture-dependency": "1.0.0",
            }
            assert installation.requirements() == [f"heinzel @ {core.as_uri()}", f"fixture-plugin @ {plugin.as_uri()}"]
        finally:
            server.shutdown()
            thread.join(5)
            assert not thread.is_alive()
