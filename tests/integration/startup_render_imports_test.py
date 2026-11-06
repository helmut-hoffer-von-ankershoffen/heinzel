"""Full discovery and authenticated API construction do not need CLI presentation implementations."""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


def test_complete_web_does_not_import_unused_cli_renderers():
    code = r"""
import importlib.abc
import sys
blocked = {
    'heinzel.modules.org._render', 'heinzel.modules.heinzel._render',
    'heinzel.modules.graph._render', 'heinzel.modules.system._render',
    'heinzel.modules.team._render', 'heinzel.modules.telemetry._render',
    'heinzel.modules.system.notify._render', 'heinzel.modules.system.config._render',
    'heinzel.modules.system.claude._render', 'heinzel.modules.system.capability._render',
    'heinzel.modules.system.plugin._render', 'heinzel.modules.system.state._render',
    'heinzel.modules.system.layer._render', 'heinzel.modules.system.daemon._render',
    'heinzel.modules.system.connector._render', 'heinzel.modules.system.plugin.docs._render',
    'heinzel.modules.system.prerequisite._render',
}
class NoCliRenderers(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError('Unused CLI renderer imported: ' + fullname)
guard = NoCliRenderers()
sys.meta_path.insert(0, guard)
from heinzel import Settings
from heinzel.kernel import boot, DaemonOverview
from heinzel.heads import cli
import typer
kernel = boot(Settings(plugins_enabled=False, update_check=False, auto_update=False))
assert len(kernel.modules) == 32
assert any(record.qualname == 'system.prerequisite' for record in kernel.modules)
assert 'ui' in typer.main.get_command(cli.build(kernel, ui_only=True)).commands
from heinzel.heads.web import build
from fastapi.testclient import TestClient
app = build(kernel, port=8765, token='fixture-session', static=None)
client = TestClient(app, base_url='http://localhost:8765')
assert client.get('/api/lifecycle').status_code == 401
response = client.get('/api/lifecycle', headers={'Authorization': 'Bearer fixture-session'})
assert response.status_code == 200, response.text
assert response.json()['instance'] == app.state.lifecycle.instance
assert not blocked.intersection(sys.modules)
# The exact registered built-in class gate still accepts the real observer.
from heinzel.modules.system.daemon._overview import DaemonOverviewService
observer = kernel.resolve_builtin_service(DaemonOverview, 'system.daemon')
assert type(observer) is DaemonOverviewService
assert not blocked.intersection(sys.modules)
sys.meta_path.remove(guard)
from heinzel.modules.system.daemon import render_jobs
from rich.console import Console
console = Console()
with console.capture() as output:
    console.print(render_jobs([]))
assert 'No module advertises a job' in output.get()
assert 'heinzel.modules.system.daemon._render' in sys.modules
"""
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr
