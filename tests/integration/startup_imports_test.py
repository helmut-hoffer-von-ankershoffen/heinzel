"""Discovery and web construction retain operations without loading their unused terminal/cron dependencies."""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


def test_boot_and_web_keep_services_without_importing_unused_execution_dependencies():
    code = r"""
import sys
from heinzel import Settings
from heinzel.kernel import boot
from heinzel.heads.web import build
kernel = boot(Settings(plugins_enabled=False, update_check=False, auto_update=False))
app = build(kernel, port=8765, token='fixture-session', static=None)
assert app.state.lifecycle.instance
names = {record.qualname for record in kernel.modules}
assert {'graph', 'heinzel', 'system.daemon', 'system.skill', 'system.changelog'} <= names
unwanted = {'psutil', 'croniter', 'dateutil', 'markdown_it', 'pygments', 'rich.markdown', 'rich.syntax'}
assert not unwanted.intersection(sys.modules), sorted(unwanted.intersection(sys.modules))
from heinzel.modules.heinzel._schedule_suggestions import SuggestedTrigger
from pydantic import ValidationError
assert SuggestedTrigger(cron="0 9 * * *", timezone="UTC").cron == "0 9 * * *"
try:
    SuggestedTrigger(cron="bad cron", timezone="UTC")
except ValidationError:
    pass
else:
    raise AssertionError("Deferred cron parser did not validate the suggestion")
from rich.console import Console
from pathlib import Path
from heinzel.modules.system.skill import render_show
from heinzel.modules.system.skill._models import SkillDetail, SkillView
from heinzel.modules.system.daemon._render import render_installed
from heinzel.modules.system.daemon._models import Installed
from heinzel.modules.system.changelog import as_markdown
from typing import get_type_hints
from rich.console import RenderableType
assert get_type_hints(as_markdown)["return"] == RenderableType
console = Console(record=True, width=100)
with console.capture() as captured:
    console.print(as_markdown("# Release notes\n\nLiteral <plugin>"))
    skill = SkillView(name="example", description="Fixture", source="core", fingerprint="0"*64)
    console.print(render_show(SkillDetail(skill=skill, body="# Skill body")))
    installed = Installed(
        platform="linux", executable=Path("/fixture/hz"), interval_minutes=5,
        files={Path("fixture.service"): "[Service]\nExecStart=/fixture/hz"}, commands=[], dry_run=True
    )
    console.print(render_installed(installed))
output = captured.get()
expected = ("Release notes", "Literal <plugin>", "Skill body", "ExecStart=/fixture/hz")
assert all(text in output for text in expected), output
from heinzel.heads.web._agent_titles import claude_title
assert claude_title(-1) is None
assert {"psutil", "croniter", "rich.markdown", "rich.syntax"} <= sys.modules.keys()
"""
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr


def test_direct_ui_preserves_complete_web_without_importing_other_cli_workflows():
    code = r"""
import importlib.abc
import sys
blocked = {
    'heinzel.heads.cli._setup', 'heinzel.heads.cli._join', 'heinzel.heads.cli._chat',
    'heinzel.heads.cli._discovery', 'heinzel.heads.cli._overview',
}
class NoUnusedCli(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise AssertionError('Unrelated CLI workflow imported: ' + fullname)
guard = NoUnusedCli()
sys.meta_path.insert(0, guard)
from heinzel.heads import cli
from heinzel import Settings
from heinzel.kernel import boot
import typer
kernel = boot(Settings(plugins_enabled=False, update_check=False, auto_update=False))
command = typer.main.get_command(cli.build(kernel, ui_only=True))
assert 'ui' in command.commands
from heinzel.heads.web import build
from fastapi.testclient import TestClient
app = build(kernel, port=8765, token='fixture-session', static=None)
assert len(kernel.modules) >= 31
client = TestClient(app, base_url='http://localhost:8765')
assert client.get('/api/lifecycle').status_code == 401
response = client.get('/api/lifecycle', headers={'Authorization': 'Bearer fixture-session'})
assert response.status_code == 200, response.text
assert response.json()['instance'] == app.state.lifecycle.instance
assert not blocked.intersection(sys.modules)
sys.meta_path.remove(guard)
full = typer.main.get_command(cli.build(kernel))
assert {'setup', 'join', 'chat', 'ui'} <= full.commands.keys()
assert {'heinzel.heads.cli._setup', 'heinzel.heads.cli._join', 'heinzel.heads.cli._chat'} <= sys.modules.keys()
"""
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr
