# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Preview a standalone plugin with its installed core artifact and isolated application state."""

import os
import shutil
import subprocess
from pathlib import Path

root = Path.cwd()
environment = {key: value for key, value in os.environ.items() if not key.startswith("HZ_")}
for name in ("CONFIG_HOME", "DATA_DIR", "CACHE_DIR", "STATE_DIR", "LOG_DIR"):
    environment[f"HZ_{name}"] = str(root / ".preview" / name.lower())
environment["HZ_PLUGINS_ENABLED"] = "true"
print("Preview uses isolated state. Restart after rebuilding view assets; Ctrl-C stops it.", flush=True)
executable = shutil.which("uv")
if executable is None:
    msg = "Install uv before previewing this plugin."
    raise SystemExit(msg)
subprocess.run(  # noqa: S603 - interactive: no timeout; preview stays open until Ctrl-C
    [executable, "run", "hz", "ui", "--no-browser", "--port", "0"], env=environment, cwd=root, check=True
)
