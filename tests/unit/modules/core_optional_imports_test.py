"""Core discovery must not import optional model SDKs, even if tests installed the extras."""

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.timeout(60)
def test_core_cli_and_private_api_discovery_without_provider_sdks(tmp_path):
    # A fresh interpreter blocks optional SDK imports regardless of what the test environment installed.
    (tmp_path / "sitecustomize.py").write_text(
        "import builtins\n"
        "original = builtins.__import__\n"
        "def guarded(name, *args, **kwargs):\n"
        "    if name.split('.')[0] in {'openai', 'anthropic'}:\n"
        "        raise ModuleNotFoundError('optional SDK intentionally unavailable')\n"
        "    return original(name, *args, **kwargs)\n"
        "builtins.__import__ = guarded\n"
    )
    env = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(tmp_path),
        "HZ_PLUGINS_ENABLED": "false",
    }
    cli = subprocess.run(
        [sys.executable, "-c", "import sys; sys.argv=['hz', '--version']; from heinzel.heads.cli import main; main()"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert cli.returncode == 0, cli.stderr
    assert "hz " in cli.stdout
    source = """\
from decimal import Decimal
from heinzel import HeinzelError
from heinzel.kernel import Kernel, discover
from heinzel.kernel._exposure import exposed
from heinzel.modules.heinzel._priced_model import ReservedModel
records, _ = discover(plugins=False)
kernel = Kernel(records=records)
names = {f'{item.record.qualname}:{item.operation.name}' for item in exposed(kernel, 'api-private')}
assert {'heinzel:propose-model-policy', 'heinzel:model-policy', 'heinzel:approve-model-grant'} <= names
try:
    ReservedModel.for_openai(None, None, '', '', message_cap=Decimal(1), max_requests=1,
        max_turn_tokens=1, max_turn_output_tokens=1, session_cap=Decimal(1), owner_cap=Decimal(1), now=lambda: None)
except HeinzelError as error:
    assert 'provider is not installed' in str(error)
else:
    raise AssertionError('selected provider must fail without its optional SDK')
"""
    discovery = subprocess.run(
        [sys.executable, "-c", source],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert discovery.returncode == 0, discovery.stderr
