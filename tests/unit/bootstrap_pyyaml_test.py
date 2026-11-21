"""The reviewed PyYAML build: one owner of the pins, refusing every lock or source that differs from them."""

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from heinzel import bootstrap_pyyaml as bootstrap

pytestmark = pytest.mark.unit

MODULE = Path(bootstrap.__file__)
UV_LOCK = (
    '[[package]]\nname = "other"\nversion = "1"\n\n[[package]]\nname = "pyyaml"\nversion = "{version}"\n'
    'sdist = {{ url = "{url}", hash = "sha256:{sha}" }}\n'
)


def uv_lock(tmp_path, **changes):
    values = {"version": bootstrap.VERSION, "url": bootstrap.SOURCE_URL, "sha": bootstrap.SOURCE_SHA256} | changes
    path = tmp_path / "uv.lock"
    path.write_text(UV_LOCK.format(**values))
    return path


def requirements(tmp_path, text):
    path = tmp_path / "requirements.lock"
    path.write_text(text)
    return path


@pytest.fixture
def commands(monkeypatch):
    """Record every external command; curl writes the given bytes, uv build leaves the given wheels."""
    state = SimpleNamespace(calls=[], source=b"reviewed", wheels=["pyyaml-6.0.3-py3-none-any.whl"])

    def run(command, **options):
        state.calls.append((command, options))
        if command[0] == "curl":
            Path(command[-1]).write_bytes(state.source)
        if command[:2] == ["uv", "build"]:
            out = Path(command[command.index("--out-dir") + 1])
            out.mkdir(parents=True)
            for name in state.wheels:
                (out / name).write_bytes(b"wheel")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    monkeypatch.setattr(bootstrap, "SOURCE_SHA256", hashlib.sha256(state.source).hexdigest())
    return state


def test_it_runs_as_a_file_with_only_the_standard_library():
    """The toolkit and the script run it with `-I -S` before any dependency exists: it may import nothing else."""
    tree = ast.parse(MODULE.read_text())
    imported = {
        (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert imported <= set(sys.stdlib_module_names)
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(MODULE), "pins"], capture_output=True, text=True, check=True, timeout=30
    )
    assert json.loads(result.stdout) == bootstrap.pins()


def test_the_pins_name_one_source_and_only_hash_pinned_backend_wheels():
    pins = bootstrap.pins()
    assert pins["source_url"].startswith("https://files.pythonhosted.org/")
    assert pins["source_url"].endswith(f"/pyyaml-{pins['version']}.tar.gz")
    assert len(pins["source_sha256"]) == 64
    assert pins["backend"]
    assert all("==" in line and "--hash=sha256:" in line for line in pins["backend"])


def test_only_a_python_without_an_upstream_wheel_needs_the_build(monkeypatch):
    assert bootstrap.needed((3, 15, 0))
    assert bootstrap.needed((3, 16))
    assert not bootstrap.needed((3, 14, 9))
    monkeypatch.setattr(bootstrap.sys, "version_info", (3, 14, 0))
    assert bootstrap.main(["needed"]) == 1
    monkeypatch.setattr(bootstrap.sys, "version_info", (3, 15, 0))
    assert bootstrap.main(["needed"]) == 0
    # a lock is resolved for a target Python that is not the one running the tooling
    assert bootstrap.main(["needed", "3.14"]) == 1
    monkeypatch.setattr(bootstrap.sys, "version_info", (3, 14, 0))
    assert bootstrap.main(["needed", "3.15"]) == 0


def test_the_reviewed_uv_lock_passes(tmp_path):
    bootstrap.check_uv_lock(uv_lock(tmp_path))


@pytest.mark.parametrize(
    "change", [{"version": "999.0.0"}, {"url": "https://example.test/unreviewed.tar.gz"}, {"sha": "0" * 64}]
)
def test_a_uv_lock_that_selects_another_pyyaml_is_refused(tmp_path, change):
    with pytest.raises(SystemExit, match="lock differs from the reviewed PyYAML bootstrap"):
        bootstrap.check_uv_lock(uv_lock(tmp_path, **change))


def test_a_uv_lock_without_pyyaml_is_refused(tmp_path):
    path = tmp_path / "uv.lock"
    path.write_text('[[package]]\nname = "other"\nversion = "1"\n')
    with pytest.raises(SystemExit, match="lock differs"):
        bootstrap.check_uv_lock(path)


def test_hash_pinned_requirements_that_accept_the_reviewed_source_pass(tmp_path):
    text = f"other==1 --hash=sha256:aa\nPyYAML=={bootstrap.VERSION} \\\n    --hash=sha256:bb \\\n"
    text += f"    --hash=sha256:{bootstrap.SOURCE_SHA256}\nzeta==2 --hash=sha256:cc\n"
    bootstrap.check_requirements(requirements(tmp_path, text))


@pytest.mark.parametrize(
    "text",
    [
        "other==1 --hash=sha256:aa\n",  # no PyYAML at all
        "pyyaml==999.0.0 --hash=sha256:{sha}\n",  # another version
        "pyyaml=={version} --hash=sha256:" + "0" * 64 + "\n",  # the reviewed source is not accepted
        "pyyaml=={version} --hash=sha256:{sha}\npyyaml=={version} --hash=sha256:{sha}\n",  # ambiguous
        "pyyaml=={version}\n",  # not hash-pinned
    ],
)
def test_requirements_that_differ_from_the_reviewed_source_are_refused(tmp_path, text):
    path = requirements(tmp_path, text.format(version=bootstrap.VERSION, sha=bootstrap.SOURCE_SHA256))
    with pytest.raises(SystemExit, match="lock differs"):
        bootstrap.check_requirements(path)


def test_build_only_copies_the_wheel_and_installs_nothing(tmp_path, commands, capsys):
    out = tmp_path / "wheels"
    assert bootstrap.main(["build", "--lock", str(uv_lock(tmp_path)), "--wheel-dir", str(out)]) == 0
    assert [path.name for path in out.iterdir()] == ["pyyaml-6.0.3-py3-none-any.whl"]
    assert capsys.readouterr().out.strip() == str(out / "pyyaml-6.0.3-py3-none-any.whl")
    curl, venv, backend, build = (command for command, _ in commands.calls)
    assert curl[0] == "curl"
    assert bootstrap.SOURCE_URL in curl
    assert curl[curl.index("--proto") + 1] == "=https"
    assert curl[curl.index("--proto-redir") + 1] == "=https"
    assert venv[:4] == ["uv", "venv", "--python", "3.15"]
    assert backend[:3] == ["uv", "pip", "install"]
    assert "--require-hashes" in backend
    assert "--no-build" in backend
    assert build[:3] == ["uv", "build", "--wheel"]
    assert "--no-build-isolation" in build


def test_the_backend_is_installed_from_the_pinned_lines_and_the_build_stays_pure_python(
    tmp_path, commands, monkeypatch
):
    monkeypatch.setenv("PYYAML_FORCE_CYTHON", "1")
    seen = {}
    record = bootstrap.subprocess.run

    def run(command, **options):
        if "--requirements" in command:
            seen["backend"] = Path(command[command.index("--requirements") + 1]).read_text()
        return record(command, **options)

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    bootstrap.build(tmp_path, "3.15")
    assert seen["backend"].splitlines() == list(bootstrap.BACKEND)
    environment = commands.calls[-1][1]["env"]
    assert environment["PYYAML_FORCE_LIBYAML"] == "0"
    assert "PYYAML_FORCE_CYTHON" not in environment


def test_the_caller_selects_the_python_the_wheel_is_built_for(tmp_path, commands, monkeypatch):
    monkeypatch.setenv("UV_PYTHON", "3.16")
    bootstrap.main(["build", "--wheel-dir", str(tmp_path / "a")])
    assert commands.calls[1][0][3] == "3.16"
    bootstrap.main(["build", "--wheel-dir", str(tmp_path / "b"), "--python", "/opt/venv/bin/python"])
    assert commands.calls[5][0][3] == "/opt/venv/bin/python"


def test_a_tampered_source_never_reaches_a_backend(tmp_path, commands):
    commands.source = b"tampered"
    with pytest.raises(SystemExit, match="differs from the reviewed hash"):
        bootstrap.build(tmp_path, "3.15")
    assert [command[0] for command, _ in commands.calls] == ["curl"]


def test_a_lock_that_differs_stops_before_any_download(tmp_path, commands):
    lock = uv_lock(tmp_path, version="999.0.0")
    with pytest.raises(SystemExit, match="lock differs"):
        bootstrap.main(["build", "--lock", str(lock), "--wheel-dir", str(tmp_path / "wheels")])
    pinned = requirements(tmp_path, "pyyaml==999.0.0 --hash=sha256:00\n")
    with pytest.raises(SystemExit, match="lock differs"):
        bootstrap.main(["install", "--requirements", str(pinned), "--python", "python"])
    assert commands.calls == []


def test_a_failing_command_stops_the_build_with_its_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda command, **options: SimpleNamespace(returncode=22))
    with pytest.raises(SystemExit) as stopped:
        bootstrap.build(tmp_path, "3.15")
    assert stopped.value.code == 22


def test_a_build_that_leaves_no_single_wheel_is_refused(tmp_path, commands):
    commands.wheels = []
    with pytest.raises(SystemExit, match="exactly one wheel"):
        bootstrap.build(tmp_path, "3.15")


def test_install_records_the_wheel_as_the_registry_package(tmp_path, commands, monkeypatch):
    """Without the direct-URL record a later `uv sync` keeps it instead of rebuilding from source."""
    site = tmp_path / "site"
    info = site / f"pyyaml-{bootstrap.VERSION}.dist-info"
    info.mkdir(parents=True)
    (info / "direct_url.json").write_text("{}")
    record = bootstrap.subprocess.run

    def run(command, **options):
        result = record(command, **options)
        if command[1:2] == ["-c"]:
            result.stdout = f"{site}\n"
        return result

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    interpreter = str(tmp_path / "venv/bin/python")
    assert bootstrap.main(["install", "--lock", str(uv_lock(tmp_path)), "--python", interpreter]) == 0
    install = commands.calls[4][0]
    assert install[:5] == ["uv", "pip", "install", "--python", interpreter]
    assert "--no-build" in install
    assert "--no-deps" in install
    assert install[-1].endswith(".whl")
    assert not (info / "direct_url.json").exists()
    assert commands.calls[1][0][3] == interpreter  # the backend runs on the Python the wheel is installed into


WHEELS_LOCK = (
    '[[package]]\nname = "pyyaml"\nversion = "6.0.4"\nsdist = {{ url = "https://example/pyyaml-6.0.4.tar.gz", '
    'hash = "sha256:00" }}\nwheels = [\n    {{ url = "https://example/pyyaml-6.0.4-cp315-cp315-manylinux.whl" }},\n]\n'
)


def test_a_lock_on_6_0_3_builds_the_reviewed_source(commands, tmp_path):
    out = tmp_path / "out"
    assert bootstrap.locked_wheel(uv_lock(tmp_path)) is None
    assert bootstrap.main(["build", "--lock", str(tmp_path / "uv.lock"), "--wheel-dir", str(out)]) == 0
    assert any(command[:2] == ["uv", "build"] for command, _ in commands.calls)
    assert [path.name for path in out.iterdir()] == ["pyyaml-6.0.3-py3-none-any.whl"]


def test_a_lock_on_6_0_4_builds_nothing_and_installs_the_upstream_wheel(commands, tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(WHEELS_LOCK.format())
    assert bootstrap.locked_wheel(lock) == "6.0.4"
    out = tmp_path / "out"
    assert bootstrap.main(["build", "--lock", str(lock), "--wheel-dir", str(out)]) == 0
    assert list(out.iterdir()) == []
    assert bootstrap.main(["install", "--lock", str(lock), "--python", "/venv/bin/python"]) == 0
    names = [command[0] for command, _ in commands.calls]
    assert "curl" not in names
    assert not any(command[:2] == ["uv", "build"] for command, _ in commands.calls)
    install = [command for command, _ in commands.calls if command[:3] == ["uv", "pip", "install"]]
    assert install == [
        ["uv", "pip", "install", "--python", "/venv/bin/python", "--no-build", "--no-deps", "pyyaml==6.0.4"]
    ]
