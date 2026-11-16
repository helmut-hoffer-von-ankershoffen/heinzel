"""An automatic update against real uv, offline, in temporary tool folders (ADR 0116).

Two tiny local releases of a stand-in `heinzel` (with an `hz` script), a plugin and a dependency are built as wheels.
uv installs the first into a temporary uv tool folder; `system.apply-announced`'s install path then moves to the second,
or puts the first back. Nothing reaches an index or the real tool folders: uv runs with `--no-index` and the wheels'
folder, its tool, executable and cache folders are temporary, and no uv configuration file is read.
"""

import base64
import fcntl
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation, ReleaseSource, _installation_continuity
from heinzel.kernel._installation import _run
from heinzel.modules.system._announced import Announcement, environment_versions
from heinzel.modules.system._update import UpdateService

pytestmark = pytest.mark.integration


def unavailable(reason: str, environ: Mapping[str, str] | None = None) -> NoReturn:
    """Skip locally, but fail in CI: this test guards the real-uv update path, so it must never skip unseen there."""
    env = os.environ if environ is None else environ
    if env.get("CI"):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(autouse=True)
def needs_uv():
    if shutil.which("uv") is None:
        unavailable("uv isn't installed, so there is no real uv to test with")


def wheel(  # noqa: PLR0913 - the fields of a wheel a test varies
    folder: Path,
    name: str,
    version: str,
    requires: tuple[str, ...] = (),
    script: str | None = None,
    *,
    python: str | None = None,
) -> None:
    """A minimal pure-Python wheel of `name` at `version`, needing Python `python` if given."""
    module, dist = name.replace("-", "_"), f"{name.replace('-', '_')}-{version}"
    metadata = (
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        + (f"Requires-Python: {python}\n" if python else "")
        + "".join(f"Requires-Dist: {item}\n" for item in requires)
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


def private_python(shared: Path) -> str:
    """Reuse an accepted runner interpreter or provision one old Python for this isolated pytest run."""
    runner = Path(sys.executable).resolve()
    try:
        _installation_continuity.external_python(runner)
    except HeinzelError:
        pass
    else:
        return str(runner)

    # Hosted images can have a controller Python outside the ownership policy. The old interpreter is immutable
    # input to these tests; cache only that interpreter rather than spending each case's 15 seconds provisioning it.
    folder = shared / "rollback-source-python"
    with (shared / "rollback-source-python.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (folder / "complete").is_file():
            env = {**os.environ, "UV_NO_CONFIG": "1", "UV_CACHE_DIR": str(folder / "cache")}
            found = subprocess.run(
                [shutil.which("uv") or "uv", "python", "install", "--no-bin", "3.13", "--install-dir", str(folder)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            if found.returncode != 0:
                unavailable(f"no private Python for a uv tool, and uv couldn't install one: {found.stderr}")
        candidates = sorted(folder.glob("cpython-3.13.*/bin/python3.13"))
        if len(candidates) != 1:
            unavailable("uv did not provide one unambiguous private Python 3.13")
        python = candidates[0].resolve(strict=True)
        if not python.is_relative_to(folder.resolve(strict=True)):
            pytest.fail("uv selected an interpreter outside the private rollback fixture")
        _installation_continuity.external_python(python)
        (folder / "complete").touch()
        return str(python)


@pytest.fixture(scope="session")
def original_python(tmp_path_factory) -> str:
    base = tmp_path_factory.getbasetemp()
    shared = base.parent if "PYTEST_XDIST_WORKER" in os.environ else base
    return private_python(shared)


@pytest.fixture
def tool(tmp_path, monkeypatch, original_python):
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

    first = runner(["uv", "tool", "install", "--python", original_python, "heinzel", "--with", "heinzel-clock"])
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


def apply(installation, tmp_path, *, starts, releases=None):
    now = datetime.now(UTC)
    announced = Announcement(
        "2.0.0",
        {"heinzel-clock": "2.0.0"},
        exclude_newer=now + timedelta(days=1),  # local wheels have no upload time; the flag must still go through uv
        exclude_newer_core=now + timedelta(days=1),
    )
    service = UpdateService(installation, releases)  # type: ignore[arg-type] - None: no release source is asked
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


# A release that needs a newer Python than the tool runs on ------------------------------------------------------------

NEWER = f"{sys.version_info.major}.{sys.version_info.minor + 1}"


class NeedsNewerPython(ReleaseSource):
    """Release metadata as PyPI publishes it: 2.0.0 needs the next Python minor version."""

    def requires_python(self, version, timeout):
        del timeout
        return f">={NEWER}" if version == "2.0.0" else None


@pytest.fixture
def newer_release(tool, tmp_path, monkeypatch):
    """2.0.0 of hz needs the next Python; uv manages Pythons only in a temporary folder, with no executables linked."""
    wheel(tmp_path / "wheels", "heinzel", "2.0.0", requires=("fakedep>=1",), script="hz", python=f">={NEWER}")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(tmp_path / "managed"))
    monkeypatch.setenv("UV_PYTHON_BIN_DIR", str(tmp_path / "python-bin"))
    monkeypatch.delenv("UV_PYTHON_DOWNLOADS", raising=False)
    return tool


def test_real_uv_moves_to_a_managed_newer_python_the_release_needs(newer_release, tmp_path):
    managed_python_or_skip()
    result = apply(newer_release, tmp_path, starts=True, releases=NeedsNewerPython())
    assert result.updated, result.message
    versions = environment_versions(newer_release.prefix)
    assert (versions["heinzel"], versions["heinzel-clock"], versions["fakedep"]) == ("2.0.0", "2.0.0", "1.0.0")
    python = (newer_release.prefix / "bin" / "python").resolve()
    assert python.is_relative_to((tmp_path / "managed").resolve())
    assert python.name.startswith(f"python{NEWER}")
    assert not (tmp_path / "python-bin").exists() or not any((tmp_path / "python-bin").iterdir())


def managed_python_or_skip() -> None:
    """A uv-managed Python of the next minor version in the temporary folder, downloaded once if uv has one."""
    found = subprocess.run(
        [shutil.which("uv") or "uv", "python", "install", "--managed-python", "--no-bin", NEWER],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,  # one download of a Python build, into the temporary folder
    )
    if found.returncode != 0:
        if "no download" in found.stderr.lower():
            pytest.skip(f"uv has no Python {NEWER} to download yet")
        unavailable(f"uv couldn't install a managed Python {NEWER} here: {found.stderr}")


def test_real_uv_puts_back_the_old_python_and_its_continuity_when_the_move_does_not_start(newer_release, tmp_path):
    managed_python_or_skip()
    receipt = (newer_release.prefix / "uv-receipt.toml").read_bytes()
    python = (newer_release.prefix / "bin" / "python").resolve()
    result = apply(newer_release, tmp_path, starts=False, releases=NeedsNewerPython())
    assert (result.updated, result.restored) == (False, True), result.message
    versions = environment_versions(newer_release.prefix)
    assert (versions["heinzel"], versions["heinzel-clock"], versions["fakedep"]) == ("1.0.0", "1.0.0", "1.0.0")
    assert (newer_release.prefix / "uv-receipt.toml").read_bytes() == receipt
    assert (newer_release.prefix / "bin" / "python").resolve() == python
    assert newer_release.startup_state_matches()  # uv recreated the folder; the record survived it


def test_real_uv_keeps_the_continuity_record_across_a_successful_move(newer_release, tmp_path):
    managed_python_or_skip()
    record = (newer_release.prefix / ".heinzel-startup-state").read_bytes()
    result = apply(newer_release, tmp_path, starts=True, releases=NeedsNewerPython())
    assert result.updated, result.message
    assert (newer_release.prefix / ".heinzel-startup-state").read_bytes() == record  # as after a same-Python update
    assert not (newer_release.prefix / ".heinzel-startup-pending").exists()


def test_real_uv_without_the_newer_python_stops_before_installing(newer_release, tmp_path, monkeypatch):
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    receipt = (newer_release.prefix / "uv-receipt.toml").read_bytes()
    python = (newer_release.prefix / "bin" / "python").resolve()
    with pytest.raises(HeinzelError, match=rf"could not provide a uv-managed Python {re.escape(NEWER)}") as caught:
        apply(newer_release, tmp_path, starts=True, releases=NeedsNewerPython())
    assert "keeps working" in str(caught.value)
    versions = environment_versions(newer_release.prefix)
    assert (versions["heinzel"], versions["heinzel-clock"], versions["fakedep"]) == ("1.0.0", "1.0.0", "1.0.0")
    assert (newer_release.prefix / "uv-receipt.toml").read_bytes() == receipt
    assert (newer_release.prefix / "bin" / "python").resolve() == python
    assert newer_release.startup_state_matches()


@pytest.mark.parametrize(("environ", "outcome"), [({"CI": "true"}, pytest.fail.Exception), ({}, pytest.skip.Exception)])
def test_without_uv_or_a_python_the_test_fails_in_ci_and_skips_elsewhere(environ, outcome):
    with pytest.raises(outcome, match="no real uv"):
        unavailable("uv isn't installed, so there is no real uv to test with", environ)


def test_unsafe_runner_provisions_one_private_old_interpreter_for_repeated_installs(tmp_path, monkeypatch):
    unsafe = tmp_path / "unsafe-runner"
    unsafe.mkdir()
    executable = unsafe / "python"
    executable.touch(mode=0o700)
    unsafe.chmod(0o777)
    monkeypatch.setattr(sys, "executable", str(executable))
    shared = tmp_path / "pytest-run"
    shared.mkdir()
    folder = shared / "rollback-source-python"
    chosen = folder / "cpython-3.13.0-fixture/bin/python3.13"
    calls = []

    def install(command, *, env, **options):
        calls.append(command)
        assert command[1:] == ["python", "install", "--no-bin", "3.13", "--install-dir", str(folder)]
        assert options["timeout"] == 60
        assert env["UV_NO_CONFIG"] == "1"
        assert env["UV_CACHE_DIR"] == str(folder / "cache")
        chosen.parent.mkdir(parents=True)
        chosen.touch(mode=0o700)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", install)
    assert private_python(shared) == str(chosen.resolve())
    assert private_python(shared) == str(chosen.resolve())
    assert len(calls) == 1
    assert not (shared / "managed").exists()


# A plugin pinned to a tagged git commit (ADR 0137) --------------------------------------------------------------------

BACKEND = '''
"""A build backend with no dependencies, so uv builds the plugin offline: a wheel of the package folder."""
import base64, hashlib, pathlib, tomllib, zipfile


def _project():
    return tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    project = _project()
    name, version = project["name"], project["version"]
    dist = f"{name.replace('-', '_')}-{version}"
    files = {
        f"{name.replace('-', '_')}/__init__.py": pathlib.Path(name.replace("-", "_"), "__init__.py").read_text(),
        f"{dist}.dist-info/METADATA": f"Metadata-Version: 2.1\\nName: {name}\\nVersion: {version}\\n"
        + "".join(f"Requires-Dist: {item}\\n" for item in project.get("dependencies", [])),
        f"{dist}.dist-info/WHEEL": "Wheel-Version: 1.0\\nGenerator: test\\nRoot-Is-Purelib: true\\n"
        "Tag: py3-none-any\\n",
    }
    record = []
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(text.encode())}")
    files[f"{dist}.dist-info/RECORD"] = "\\n".join([*record, f"{dist}.dist-info/RECORD,,"]) + "\\n"
    wheel = f"{dist}-py3-none-any.whl"
    with zipfile.ZipFile(pathlib.Path(wheel_directory, wheel), "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return wheel
'''


def git(repository: Path, *arguments: str) -> str:
    environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    done = subprocess.run(
        [shutil.which("git") or "git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
        timeout=60,
    )
    return done.stdout.strip()


def plugin_commit(repository: Path, version: str) -> str:
    """Commit heinzel-maps at `version` in the local repository, tag it `heinzel-maps/v<version>`; the commit."""
    (repository / "heinzel_maps").mkdir(parents=True, exist_ok=True)
    (repository / "heinzel_maps" / "__init__.py").write_text(f'VERSION = "{version}"\n')
    (repository / "backend.py").write_text(BACKEND)
    (repository / "pyproject.toml").write_text(
        f'[project]\nname = "heinzel-maps"\nversion = "{version}"\ndependencies = ["heinzel>=1"]\n\n'
        '[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["."]\n'
    )
    git(repository, "add", "-A")
    git(repository, "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-q", "-m", version)
    git(repository, "tag", f"heinzel-maps/v{version}")
    return git(repository, "rev-parse", "HEAD")


def test_real_uv_moves_a_tagged_git_plugin_to_exactly_the_announced_commit(tool, tmp_path):
    if shutil.which("git") is None:
        unavailable("git isn't installed, so there is no git plugin to test with")
    repository = tmp_path / "heinzel-maps"
    repository.mkdir()
    git(repository, "init", "-q", "-b", "main")
    first = plugin_commit(repository, "0.2.0")
    tool.run(
        tool.tool_install_command(
            "heinzel", ["heinzel-clock", f"heinzel-maps @ git+{repository.as_uri()}@{first}"], reinstall=False
        ),
        "uv couldn't add the git plugin",
        "Adding heinzel-maps",
    )
    tool.record_startup_state(expected=tool.startup_fingerprint())
    second = plugin_commit(repository, "0.3.0")
    before = environment_versions(tool.prefix)
    now = datetime.now(UTC)
    announced = Announcement(
        None,
        {"heinzel-maps": "0.3.0"},
        plugin_commits={"heinzel-maps": second},
        exclude_newer=now + timedelta(days=1),
    )
    service = UpdateService(tool, None)  # type: ignore[arg-type] - no release source is asked
    result = service.apply_announced(announced, current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert result.updated, result.message
    versions = environment_versions(tool.prefix)
    assert versions["heinzel-maps"] == "0.3.0"
    assert {k: v for k, v in versions.items() if k != "heinzel-maps"} == {
        k: v for k, v in before.items() if k != "heinzel-maps"
    }  # hz, heinzel-clock and fakedep stay, though 2.0.0 of each is there
    assert second in (tool.prefix / "uv-receipt.toml").read_text()  # still pinned, now to the new commit
