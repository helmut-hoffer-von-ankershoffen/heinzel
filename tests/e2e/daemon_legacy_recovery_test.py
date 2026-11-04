"""Genuine published015 -> explicit candidate recovery -> actual015 updater -> new ordinary install.

Only the OS-manager protocol is an immutable installed double. This is not native launchd acceptance.
"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from urllib.request import urlopen

import pytest

from heinzel import Config, Settings
from heinzel.heads import install
from heinzel.kernel import ProcessOutcome
from heinzel.modules.system.daemon import _service as daemon
from tests.e2e.tool_install_test import REPO, Sandbox

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(sys.platform != "darwin", reason="Initial legacy recovery protocol is macOS only"),
]
FIXTURE = REPO / "tests/fixtures/legacy_recovery_manager.py"


def manager_wheel(directory):
    name = "heinzel_legacy_manager_fixture"
    files = {
        "legacy_recovery_fixture.py": FIXTURE.read_bytes(),
        "legacy_manager_plugin/__init__.py": (
            b'from heinzel import Module\nmodule = Module("legacy-manager", "Isolated manager fixture.")\n'
        ),
        f"{name}-1.0.0.dist-info/METADATA": (
            b"Metadata-Version: 2.1\nName: heinzel-legacy-manager-fixture\nVersion: 1.0.0\n"
        ),
        f"{name}-1.0.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{name}-1.0.0.dist-info/entry_points.txt": b"[heinzel.modules]\nlegacy-manager = legacy_manager_plugin\n",
    }
    record = f"{name}-1.0.0.dist-info/RECORD"
    lines = [
        f"{path},sha256={base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip('=')},{len(content)}"
        for path, content in files.items()
    ]
    files[record] = ("\n".join([*lines, f"{record},,"]) + "\n").encode()
    target = directory / f"{name}-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return target


@pytest.fixture
def sandbox(tmp_path, fixture_managed_python):
    """This proof owns a cold cache; it never adopts another test run's tool links."""
    return Sandbox(tmp_path, *fixture_managed_python)


@pytest.fixture(scope="module")
def candidate_016(tmp_path_factory):
    """Version a copied source fixture only; do not alter or claim a released016 artifact."""
    root = tmp_path_factory.mktemp("legacy-candidate-016")
    for name in (
        "pyproject.toml",
        "uv.lock",
        "hatch_build.py",
        "README.md",
        "PYPI.md",
        "ATTRIBUTIONS.md",
        "LICENSE",
        "CHANGELOG.md",
        ".gitignore",
    ):
        shutil.copy2(REPO / name, root / name)
    for name in ("src", "scripts", "web", "agent-skills"):
        shutil.copytree(
            REPO / name,
            root / name,
            ignore=shutil.ignore_patterns("node_modules", "__pycache__", "test-results", "work", ".vite"),
        )
    subprocess.run(["git", "init", "--quiet", root], check=True, timeout=15)
    for name in ("pyproject.toml", "uv.lock"):
        path = root / name
        text = path.read_text()
        old = 'version = "0.9.15"' if name == "pyproject.toml" else 'name = "heinzel"\nversion = "0.9.15"'
        path.write_text(text.replace(old, old.replace("0.9.15", "0.9.16"), 1))
    subprocess.run(
        [sys.executable, root / "scripts/build.py", "--wheel", "--out-dir", root / "dist"], check=True, timeout=300
    )
    return root / "dist/heinzel-0.9.16-py3-none-any.whl"


@pytest.fixture(scope="module")
def published_015(tmp_path_factory):
    path = tmp_path_factory.mktemp("legacy-published-015") / "heinzel-0.9.15-py3-none-any.whl"
    url = "https://files.pythonhosted.org/packages/ed/31/331f330e2afeb9d1d0138f38f3156742ad9852957c0bb5eb08695f330622/heinzel-0.9.15-py3-none-any.whl"
    with urlopen(url, timeout=60) as response:
        content = response.read(8 * 1024 * 1024)
    assert hashlib.sha256(content).hexdigest() == "bbcaaf457bd9adacf469293881d9edb2c539c40d3cc3191148714238e8afd435"
    path.write_bytes(content)
    return path


@pytest.fixture(scope="module")
def published_014(tmp_path_factory):
    path = tmp_path_factory.mktemp("legacy-published-014") / "heinzel-0.9.14-py3-none-any.whl"
    with urlopen("https://pypi.org/pypi/heinzel/0.9.14/json", timeout=60) as response:
        metadata = json.load(response)
    artifact = next(item for item in metadata["urls"] if item["filename"] == path.name)
    expected = "9c3a0a94ebce1be6f420a79892dbfb5fe4fa225999883eb9bb4379000c2154de"
    assert artifact["digests"]["sha256"] == expected
    assert artifact["url"].startswith("https://files.pythonhosted.org/")
    with urlopen(artifact["url"], timeout=60) as response:  # noqa: S310 - fixed primary publisher authority and independent SHA256 below
        content = response.read(8 * 1024 * 1024)
    assert hashlib.sha256(content).hexdigest() == expected
    path.write_bytes(content)
    return path


def absent_manager(command):
    if command[1] == "print":
        return ProcessOutcome(113, "", "")
    return ProcessOutcome(
        0, {"managername": "Aqua\n", "manageruid": f"{os.getuid()}\n", "list": "PID\tStatus\tLabel\n"}[command[1]], ""
    )


def install_legacy(selected, fixture, core):
    artifacts = [
        install.Artifact(
            name=name,
            version=version,
            filename=path.name,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            url=str(path),
        )
        for name, version, path in (
            ("heinzel", "0.9.14", core),
            ("heinzel-legacy-manager-fixture", "1.0.0", fixture),
        )
    ]
    profile = install.AppProfile(format=2, core_version="0.9.14", sdk=1, artifacts=artifacts)
    settings = Config.load(selected.home).settings(Settings, "hz")
    paths = install.prepare(profile, settings.data_dir / "app-artifacts", set())
    assert install._managed_install(profile, artifacts, paths, settings, set()) == 0
    return settings


def retain_fixture_source(selected, run):
    source = selected.root / "manager-source"
    source.mkdir()
    shutil.copy2(FIXTURE, source / "legacy_recovery_fixture.py")
    (source / "legacy_manager_plugin").mkdir()
    (source / "legacy_manager_plugin/__init__.py").write_bytes(
        b'from heinzel import Module\nmodule = Module("legacy-manager", "Isolated manager fixture.")\n'
    )
    (source / "pyproject.toml").write_text(
        '[project]\nname="heinzel-legacy-manager-fixture"\nversion="1.0.0"\n[project.entry-points."heinzel.modules"]\nlegacy-manager="legacy_manager_plugin"\n[build-system]\nrequires=["hatchling>=1.32.4"]\nbuild-backend="hatchling.build"\n[tool.hatch.build.targets.wheel]\nonly-include=["legacy_recovery_fixture.py","legacy_manager_plugin"]\n'
    )
    run("system", "plugin", "develop", str(source))


def wait_for_genuine_handover(settings, root):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        marker = settings.state_dir / "daemon/install.json"
        observation = settings.state_dir / "daemon/handover-observation.json"
        heartbeat = settings.state_dir / "daemon/heartbeat.json"
        if (
            marker.exists()
            and "handover_id" in json.loads(marker.read_text())
            and observation.exists()
            and heartbeat.exists()
            and not json.loads((root / "manager.json").read_text()).get("pid")
        ):
            return
        time.sleep(0.05)
    message = (
        "The real published SDK child tick did not complete its handover: " + (root / "tick-stderr.txt").read_text()
    )
    raise AssertionError(message)


def create_completed_history(run, settings, selected, published_015):
    run("automatic-015")
    wait_for_genuine_handover(settings, selected.root)
    assert "0.9.15" in run("--version")
    site = next((selected.root / "tools/heinzel/lib").glob("python*/site-packages"))
    with zipfile.ZipFile(published_015) as archive:
        for name in archive.namelist():
            if name.endswith(".py") and (name.startswith("heinzel/") or name == "_heinzel_launcher.py"):
                assert (site / name).read_bytes() == archive.read(name)
    run("system", "daemon", "pause", "--json")


@pytest.mark.long_running
@pytest.mark.timeout(300)
def test_published014_auto015_handover_then_explicit_retirement_and_real015_updater_to016(
    sandbox, candidate_016, published_014, published_015, monkeypatch
):
    selected = Sandbox(sandbox.root, sandbox.python_directory, sandbox.python, own_home=True)
    selected.home.mkdir(mode=0o700)
    environment = {key: value for key, value in selected.env.items() if not key.startswith(("HZ_", "XDG_"))}
    environment["HZ_LEGACY_MANAGER_FIXTURE"] = str(selected.root)
    for key in list(os.environ):
        if key.startswith(("HZ_", "XDG_")):
            monkeypatch.delenv(key)
    for key, value in environment.items():
        if key in {
            "HOME",
            "UV_TOOL_DIR",
            "UV_TOOL_BIN_DIR",
            "UV_CACHE_DIR",
            "UV_PYTHON_INSTALL_DIR",
            "UV_PYTHON",
            "HZ_LEGACY_MANAGER_FIXTURE",
        }:
            monkeypatch.setenv(key, value)
    monkeypatch.setattr(daemon, "_quiescence_command", absent_manager)
    fixture = manager_wheel(selected.root)
    settings = install_legacy(selected, fixture, published_014)
    python = selected.root / "tools/heinzel/bin/python"

    def run(*args, candidate=False, success=True):
        command = (
            [
                "uv",
                "run",
                "--no-project",
                "--with",
                str(candidate_016),
                "--with",
                str(fixture),
                "python",
                "-I",
                "-m",
                "legacy_recovery_fixture",
                "recovery",
            ]
            if candidate
            else [str(python), "-I", "-m", "legacy_recovery_fixture"]
        )
        result = subprocess.run(
            [*command, *args],
            env=environment,
            cwd=selected.home,
            capture_output=True,
            text=True,
            check=False,
            timeout=240,
        )
        (selected.root / f"proof-{len(list(selected.root.glob('proof-*')))}.json").write_text(
            json.dumps(
                {"arguments": args, "status": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
            )
        )
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        return result.stdout

    # Retain the unpublished fixture through the genuine local-source plugin operation, before creating legacy units.
    retain_fixture_source(selected, run)
    run("system", "daemon", "install", "--json")
    create_completed_history(run, settings, selected, published_015)
    marker = settings.state_dir / "daemon/install.json"
    original = marker.read_bytes()
    assert "profile" not in json.loads(original)
    assert "handover_id" in json.loads(original)
    plan = json.loads(run("plan", candidate=True))
    assert plan["historical_profile"] == "unknown"
    assert marker.read_bytes() == original
    assert not (settings.state_dir / "daemon/legacy-retirement").exists()
    # Only the exact reviewed digest can authorize this explicit recovery.
    run("retire", "--confirm", "0" * 64, candidate=True, success=False)
    result = json.loads(run("retire", "--confirm", plan["digest"], candidate=True))
    assert result["status"] == "retired"
    assert not marker.exists()
    assert not (settings.state_dir / "daemon/standard-install-pending.json").exists()
    run("system", "update", "--source", str(candidate_016))  # Actual published015 updater, not direct uv swapping.
    assert "0.9.16" in run("--version")
    run("system", "daemon", "install", "--json")
    status = json.loads(run("system", "daemon", "status", "--json"))
    assert status["installed"]
    assert status["loaded"]
    assert status["owned"]
    assert status["paused"]
    new_marker = marker.read_bytes()
    replay = json.loads(run("retire", "--confirm", plan["digest"], candidate=True))
    assert replay["status"] == "retired"
    assert marker.read_bytes() == new_marker
    with zipfile.ZipFile(candidate_016) as archive:
        installed = next(
            (selected.root / "tools/heinzel/lib").glob(
                "python*/site-packages/heinzel/modules/system/daemon/_legacy_retirement.py"
            )
        )
        assert installed.read_bytes() == archive.read("heinzel/modules/system/daemon/_legacy_retirement.py")
