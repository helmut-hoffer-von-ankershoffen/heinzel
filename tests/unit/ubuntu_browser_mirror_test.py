"""Fail closed before changing the runner's signed Ubuntu source inventory."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/ubuntu_browser_mirror.py"
spec = importlib.util.spec_from_file_location("ubuntu_browser_mirror", SCRIPT)
assert spec
assert spec.loader
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)

SOURCE = """## Ubuntu sources
Types: deb
URIs: mirror+file:/etc/apt/apt-mirrors.txt
Suites: noble noble-updates noble-backports
Components: main universe restricted multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

## Security
Types: deb
URIs: mirror+file:/etc/apt/apt-mirrors.txt
Suites: noble-security
Components: main universe restricted multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
"""


def test_rewrites_both_uris_only_and_is_idempotent(tmp_path):
    path = tmp_path / "ubuntu.sources"
    path.write_text(SOURCE)
    path.chmod(0o644)
    mirror.main(path)
    expected = SOURCE.replace(mirror.ORIGINAL, mirror.MIRROR)
    assert path.read_text() == expected
    assert path.stat().st_mode & 0o777 == 0o644
    mirror.main(path)
    assert path.read_text() == expected


@pytest.mark.parametrize(
    "source",
    [
        SOURCE.replace(mirror.ORIGINAL, "https://unexpected.example/ubuntu"),
        SOURCE.replace(mirror.ORIGINAL, mirror.MIRROR, 1),
        SOURCE.replace("noble-security", "noble-security other"),
        SOURCE.replace("main universe restricted multiverse", "main universe", 1),
        SOURCE.replace("/usr/share/keyrings/ubuntu-archive-keyring.gpg", "/untrusted/key", 1),
        SOURCE.replace("Types: deb", "Types: deb\nEnabled: no", 1),
        SOURCE.replace("Types: deb", "Types: deb\nTypes: deb", 1),
        SOURCE + "\nTypes: deb\n",
        SOURCE.removesuffix("\n"),
    ],
)
def test_drift_refuses_before_writing(tmp_path, source):
    path = tmp_path / "ubuntu.sources"
    path.write_text(source)
    with pytest.raises(ValueError, match=r"source|URI|Ubuntu|mixed"):
        mirror.main(path)
    assert path.read_text() == source


def test_refuses_nonregular_sources_and_preserves_targets(tmp_path):
    target = tmp_path / "target"
    target.write_text(SOURCE)
    link = tmp_path / "link"
    link.symlink_to(target)
    for path in (link, tmp_path):
        with pytest.raises(ValueError, match="regular file"):
            mirror.main(path)
    assert target.read_text() == SOURCE
    assert link.is_symlink()


def test_replacement_failure_preserves_original_and_removes_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "ubuntu.sources"
    path.write_text(SOURCE)
    before = set(tmp_path.iterdir())

    def fail_replace(*_args):
        message = "synthetic replacement failure"
        raise OSError(message)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replacement failure"):
        mirror.main(path)
    assert path.read_text() == SOURCE
    assert set(tmp_path.iterdir()) == before


def test_cli_rejects_arguments_without_reading_system_source(monkeypatch):
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "unexpected"])
    with pytest.raises(SystemExit, match="fixed Ubuntu source path"):
        runpy.run_path(str(SCRIPT), run_name="__main__")


def test_cli_uses_fixed_source_path(tmp_path, monkeypatch):
    import pathlib
    import runpy
    import sys

    path = tmp_path / "ubuntu.sources"
    path.write_text(SOURCE)
    real_path = pathlib.Path

    def source_path(value):
        if value == "/etc/apt/sources.list.d/ubuntu.sources":
            return path
        return real_path(value)

    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    monkeypatch.setattr(pathlib, "Path", source_path)
    runpy.run_path(str(SCRIPT), run_name="__main__")
    assert path.read_text() == SOURCE.replace(mirror.ORIGINAL, mirror.MIRROR)
