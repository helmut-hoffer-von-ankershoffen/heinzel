"""An automatic update against real uv, offline, in temporary tool folders (ADR 0116).

Two tiny local releases of a stand-in `heinzel` (with an `hz` script), a plugin and a dependency are built as wheels.
uv installs the first into a temporary uv tool folder; `system.apply-announced`'s install path then moves to the second,
or puts the first back. Nothing reaches an index or the real tool folders: uv runs with `--no-index` and the wheels'
folder, its tool, executable and cache folders are temporary, and no uv configuration file is read.
"""

import base64
import hashlib
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation, _installation_continuity
from heinzel.kernel._installation import _run
from heinzel.modules.system._announced import Announcement, environment_versions
from heinzel.modules.system._update import UpdateService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("uv") is None, reason="uv isn't installed, so there is no real uv to test with"),
]


def wheel(folder: Path, name: str, version: str, requires: tuple[str, ...] = (), script: str | None = None) -> None:
    """A minimal pure-Python wheel of `name` at `version`."""
    module, dist = name.replace("-", "_"), f"{name.replace('-', '_')}-{version}"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n" + "".join(
        f"Requires-Dist: {item}\n" for item in requires
    )
    files = {
        f"{module}/__init__.py": f'VERSION = "{version}"\n\n\ndef main():\n    print("{name} {version}")\n',
        f"{dist}.dist-info/METADATA": metadata,
        f"{dist}.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    if script:
        files[f"{dist}.dist-info/entry_points.txt"] = f"[console_scripts]\n{script} = {module}:main\n"
    record = []
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(text.encode())}")
    files[f"{dist}.dist-info/RECORD"] = "\n".join([*record, f"{dist}.dist-info/RECORD,,"]) + "\n"
    with zipfile.ZipFile(folder / f"{dist}-py3-none-any.whl", "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)


def release(folder: Path, version: str) -> None:
    """hz, its plugin and its dependency at `version` (the dependency moves on as well, but must stay)."""
    wheel(folder, "heinzel", version, requires=("fakedep>=1",), script="hz")
    wheel(folder, "heinzel-clock", version, requires=("heinzel>=1",))
    wheel(folder, "fakedep", version)


def private_python(tmp_path: Path) -> str:
    """An interpreter hz accepts for a tool: in a folder only you can write to, as hz names it to uv (no link).

    The test's own Python, if it qualifies; else (a CI image whose Python folders others may write to) a Python uv
    installs into a temporary folder. Skipped only if neither can be had.
    """
    own = str(Path(sys.executable).resolve())
    try:
        _installation_continuity.external_python(Path(own))
    except HeinzelError:
        pass
    else:
        return own
    folder = tmp_path / "python"
    found = subprocess.run(
        [shutil.which("uv") or "uv", "python", "install", "3.13", "--install-dir", str(folder)],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,  # one download of a Python build
    )
    candidates = sorted(folder.glob("*/bin/python3.13"))
    if found.returncode != 0 or not candidates:
        pytest.skip(f"no Python in a private folder for a uv tool here, and uv couldn't install one: {found.stderr}")
    python = str(candidates[0].resolve())
    _installation_continuity.external_python(Path(python))
    return python


@pytest.fixture
def tool(tmp_path, monkeypatch):
    """hz 1.0.0 with heinzel-clock 1.0.0 and fakedep 1.0.0, installed by real uv into temporary folders."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    release(wheels, "1.0.0")
    for variable, folder in [("UV_TOOL_DIR", "tools"), ("UV_TOOL_BIN_DIR", "bin"), ("UV_CACHE_DIR", "cache")]:
        monkeypatch.setenv(variable, str(tmp_path / folder))
    monkeypatch.setenv("UV_NO_CONFIG", "1")
    monkeypatch.delenv("UV_EXCLUDE_NEWER", raising=False)
    offline = ["--no-index", "--find-links", str(wheels)]

    def runner(command, environment=None):
        """Real uv, offline; the receipt then forgets the two offline flags (only those lines), as a PyPI install
        records neither. Anything else uv writes, an empty `[tool.options]` table included, stays for the test to see.
        """
        result = _run([*command, *offline] if command[1:3] == ["tool", "install"] else command, environment)
        receipt = tmp_path / "tools" / "heinzel" / "uv-receipt.toml"
        if receipt.is_file():
            text = receipt.read_text()
            for line in ("no-index = true\n", f'find-links = ["{wheels.as_uri()}"]\n'):
                text = text.replace(line, "")
            receipt.write_text(text)
        return result

    python = private_python(tmp_path)
    first = runner(["uv", "tool", "install", "--python", python, "heinzel", "--with", "heinzel-clock"])
    assert first.returncode == 0, first.stderr
    # Setup only: the first install is to look as one from PyPI does, with no `[tool.options]` table at all.
    receipt = tmp_path / "tools" / "heinzel" / "uv-receipt.toml"
    receipt.write_text(receipt.read_text().replace("\n[tool.options]\n", "\n").rstrip("\n") + "\n")
    assert "[tool.options]" not in receipt.read_text()
    prefix = (tmp_path / "tools" / "heinzel").resolve()
    installation = Installation(Progress(), prefix=prefix, uv=shutil.which("uv"), runner=runner)
    installation.record_startup_state(expected=installation.startup_fingerprint())
    release(wheels, "2.0.0")  # published only now: what the update finds
    return installation


def apply(installation, tmp_path, *, starts):
    now = datetime.now(UTC)
    announced = Announcement(
        "2.0.0",
        {"heinzel-clock": "2.0.0"},
        exclude_newer=now + timedelta(days=1),  # local wheels have no upload time; the flag must still go through uv
        exclude_newer_core=now + timedelta(days=1),
    )
    service = UpdateService(installation, None)  # type: ignore[arg-type] - no release source is asked
    return service.apply_announced(announced, current="1.0.0", folder=tmp_path / "state", trial=lambda v: starts)


def test_real_uv_installs_exactly_the_announced_versions_and_the_receipt_stays_clean(tool, tmp_path):
    before = (tool.prefix / "uv-receipt.toml").read_bytes()
    result = apply(tool, tmp_path, starts=True)
    assert result.updated, result.message
    versions = environment_versions(tool.prefix)
    assert (versions["heinzel"], versions["heinzel-clock"]) == ("2.0.0", "2.0.0")
    assert versions["fakedep"] == "1.0.0"  # a dependency is held where it was, though 2.0.0 is there
    after = (tool.prefix / "uv-receipt.toml").read_bytes()
    assert b"[tool.options]" not in after  # not even an empty table is left
    assert after == before  # no constraints, no options: byte for byte the receipt it was


def test_real_uv_puts_everything_back_when_the_new_version_does_not_start(tool, tmp_path):
    receipt = (tool.prefix / "uv-receipt.toml").read_bytes()
    result = apply(tool, tmp_path, starts=False)
    assert (result.updated, result.restored) == (False, True), result.message
    versions = environment_versions(tool.prefix)
    assert (versions["heinzel"], versions["heinzel-clock"], versions["fakedep"]) == ("1.0.0", "1.0.0", "1.0.0")
    assert (tool.prefix / "uv-receipt.toml").read_bytes() == receipt
    assert tool.startup_state_matches()
