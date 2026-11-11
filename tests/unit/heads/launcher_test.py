"""The package entry shim isolates Python before importing any application namespace."""

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("entry", "module"),
    [
        ("main", "heinzel.heads.bootstrap"),
        ("cli", "heinzel"),
        ("install", "heinzel.heads.install"),
    ],
)
def test_namespace_independent_entry_preserves_arguments_and_discards_stale_python_paths(monkeypatch, entry, module):
    main = runpy.run_path(str(Path(__file__).resolve().parents[3] / "src/_heinzel_launcher.py"))[entry]
    calls = []
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    monkeypatch.setenv("PYTHONPATH", "/a/stale/checkout")
    monkeypatch.setenv("PYTHONHOME", "/another/python")
    monkeypatch.setenv("HZ_LOG_LEVEL", "INFO")
    monkeypatch.setattr(sys, "argv", ["heinzel", "system", "info", "--json"])
    monkeypatch.setattr(os, "execve", lambda *arguments: calls.append(arguments))
    main()
    executable, arguments, environment = calls[0]
    assert executable == sys.executable
    assert arguments == [sys.executable, "-I", "-m", module, "system", "info", "--json"]
    assert "PYTHONPATH" not in environment
    assert "PYTHONHOME" not in environment
    assert environment["HZ_LOG_LEVEL"] == "INFO"


@pytest.mark.parametrize("entry", ["main", "cli", "install"])
@pytest.mark.parametrize("no_bytecode", [False, True])
def test_real_launcher_child_preserves_only_explicit_no_bytecode_mode(tmp_path, entry, no_bytecode):
    source = Path(__file__).resolve().parents[3] / "src/_heinzel_launcher.py"
    (tmp_path / "fictional_probe.py").write_text("VALUE = 1\n")
    # Retain the launcher's actual exec flags/environment/arguments; substitute only the application import.
    probe = (
        "import json,sys; "
        f"sys.path.insert(0,{str(tmp_path)!r}); import fictional_probe; "
        "print(json.dumps({'isolated':bool(sys.flags.isolated),"
        "'no_bytecode':sys.dont_write_bytecode,'arguments':sys.argv[1:]}))"
    )
    driver = (
        "import os,runpy,sys\n"
        "execute=os.execve\n"
        "def observe(executable,arguments,environment):\n"
        " position=arguments.index('-m')\n"
        f" execute(executable,[*arguments[:position],'-c',{probe!r},*arguments[position+2:]],environment)\n"
        "os.execve=observe\n"
        "sys.argv=['hz','fictional','--json']\n"
        f"runpy.run_path({str(source)!r})[{entry!r}]()\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", *(["-B"] if no_bytecode else []), "-c", driver],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == {
        "isolated": True,
        "no_bytecode": no_bytecode,
        "arguments": ["fictional", "--json"],
    }
    assert bool(list(tmp_path.rglob("*.pyc"))) is not no_bytecode
