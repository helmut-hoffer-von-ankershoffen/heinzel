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
    mirror.main(path, tmp_path / "mirrors.txt")
    manifest = tmp_path / "mirrors.txt"
    assert manifest.read_text().splitlines() == [
        "https://archive.ubuntu.com/ubuntu/\tpriority:1",
        "https://mirrors.edge.kernel.org/ubuntu/\tpriority:2",
    ]
    assert manifest.stat().st_mode & 0o777 == 0o644
    expected = SOURCE.replace(mirror.ORIGINAL, mirror.MIRROR)
    assert path.read_text() == expected
    assert path.stat().st_mode & 0o777 == 0o644
    inode = manifest.stat().st_ino
    mirror.main(path, manifest)
    assert path.read_text() == expected
    assert manifest.stat().st_ino == inode


@pytest.mark.parametrize(
    ("main_uri", "security_uri"),
    [
        ("http://archive.ubuntu.com/ubuntu", "http://security.ubuntu.com/ubuntu"),
        ("http://archive.ubuntu.com/ubuntu/", "http://security.ubuntu.com/ubuntu/"),
        ("https://archive.ubuntu.com/ubuntu/", "https://archive.ubuntu.com/ubuntu/"),
        ("https://archive.ubuntu.com/ubuntu", "http://security.ubuntu.com/ubuntu"),
        ("http://azure.archive.ubuntu.com/ubuntu/", "http://azure.archive.ubuntu.com/ubuntu/"),
        (mirror.KERNEL_MIRROR, mirror.KERNEL_MIRROR),
    ],
)
def test_rewrites_official_cloud_init_uris_only(tmp_path, main_uri, security_uri):
    head, tail = SOURCE.split("## Security")
    path = tmp_path / "ubuntu.sources"
    path.write_text(
        head.replace(mirror.ORIGINAL, main_uri) + "## Security" + tail.replace(mirror.ORIGINAL, security_uri)
    )
    mirror.main(path, tmp_path / "mirrors.txt")
    assert path.read_text() == SOURCE.replace(mirror.ORIGINAL, mirror.MIRROR)


@pytest.mark.parametrize(
    "uri",
    [
        "https://security.ubuntu.com/ubuntu",
        "http://archive.ubuntu.com/ubuntu//",
        "http://archive.ubuntu.com.example/ubuntu",
        "http://de.archive.ubuntu.com/ubuntu",
        "mirror+file:/tmp/apt-mirrors.txt",
    ],
)
def test_unreviewed_uri_refuses_and_names_it(tmp_path, uri):
    path = tmp_path / "ubuntu.sources"
    source = SOURCE.replace(mirror.ORIGINAL, uri)
    path.write_text(source)
    with pytest.raises(ValueError, match="differs from reviewed runner: ") as refused:
        mirror.main(path, tmp_path / "mirrors.txt")
    assert repr(uri) in str(refused.value)
    assert path.read_text() == source
    assert not (tmp_path / "mirrors.txt").exists()


@pytest.mark.parametrize(
    "source",
    [
        SOURCE.replace(mirror.ORIGINAL, "https://unexpected.example/ubuntu"),
        SOURCE.replace(mirror.ORIGINAL, mirror.MIRROR, 1),
        SOURCE.replace(mirror.ORIGINAL, mirror.MIRROR, 1).replace(mirror.ORIGINAL, "http://archive.ubuntu.com/ubuntu"),
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
        mirror.main(path, tmp_path / "mirrors.txt")
    assert path.read_text() == source
    assert not (tmp_path / "mirrors.txt").exists()


@pytest.mark.parametrize("kind", ["wrong-content", "world-writable", "symlink"])
def test_rejects_existing_mirror_list_drift_before_rewriting_sources(tmp_path, kind):
    source = tmp_path / "ubuntu.sources"
    source.write_text(SOURCE)
    target = tmp_path / "other"
    target.write_text(mirror.MIRROR_CONTENT)
    manifest = tmp_path / "mirrors.txt"
    if kind == "symlink":
        manifest.symlink_to(target)
    else:
        manifest.write_text(
            "https://unexpected.example/ubuntu/\n" if kind == "wrong-content" else mirror.MIRROR_CONTENT
        )
        manifest.chmod(0o666 if kind == "world-writable" else 0o644)
    before = manifest.read_bytes()
    with pytest.raises(ValueError, match="unexpected browser mirror list"):
        mirror.main(source, manifest)
    assert source.read_text() == SOURCE
    assert manifest.read_bytes() == before
    assert manifest.is_symlink() == (kind == "symlink")


def test_refuses_nonregular_sources_and_preserves_targets(tmp_path):
    target = tmp_path / "target"
    target.write_text(SOURCE)
    link = tmp_path / "link"
    link.symlink_to(target)
    for path in (link, tmp_path):
        with pytest.raises(ValueError, match="regular file"):
            mirror.main(path, tmp_path / "mirrors.txt")
    assert target.read_text() == SOURCE
    assert link.is_symlink()


@pytest.mark.parametrize("failed_file", ["mirrors.txt", "ubuntu.sources"])
def test_replacement_failure_preserves_original_and_removes_temporary_file(tmp_path, monkeypatch, failed_file):
    path = tmp_path / "ubuntu.sources"
    path.write_text(SOURCE)
    manifest = tmp_path / "mirrors.txt"
    replace = Path.replace

    def fail_replace(source, target):
        if target.name == failed_file:
            message = "synthetic replacement failure"
            raise OSError(message)
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_replace)
        with pytest.raises(OSError, match="synthetic replacement failure"):
            mirror.main(path, manifest)
    assert path.read_text() == SOURCE
    expected = {path} if failed_file == "mirrors.txt" else {path, manifest}
    assert set(tmp_path.iterdir()) == expected
    mirror.main(path, manifest)
    assert manifest.read_text() == mirror.MIRROR_CONTENT
    assert path.read_text() == SOURCE.replace(mirror.ORIGINAL, mirror.MIRROR)


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
        if value == "/etc/apt/heinzel-browser-mirrors.txt":
            return tmp_path / "mirrors.txt"
        return real_path(value)

    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    monkeypatch.setattr(pathlib, "Path", source_path)
    runpy.run_path(str(SCRIPT), run_name="__main__")
    assert path.read_text() == SOURCE.replace(mirror.ORIGINAL, f"mirror+file:{tmp_path / 'mirrors.txt'}")
    assert (tmp_path / "mirrors.txt").read_text() == mirror.MIRROR_CONTENT


def test_host_installs_apt_source_scope_after_validating_archive():
    import shlex
    import subprocess

    root = SCRIPT.parent.parent
    plan = subprocess.check_output(
        ["make", "--no-print-directory", "-n", "-f", "scripts/ubuntu_browser_mirror.mk"],
        cwd=root,
        text=True,
        timeout=10,
    ).splitlines()
    validate, install = map(shlex.split, plan)
    assert validate == ["sudo", "python3", "-I", "scripts/ubuntu_browser_mirror.py"]
    assert install == [
        "sudo",
        "install",
        "-o",
        "root",
        "-g",
        "root",
        "-m",
        "0644",
        "scripts/ubuntu_browser_mirror.conf",
        "/etc/apt/apt.conf.d/99-heinzel-browser-sources",
    ]
    config = (root / install[-2]).read_text()
    assert [line for line in config.splitlines() if not line.startswith("//")] == [
        'Dir::Etc::sourcelist "sources.list.d/ubuntu.sources";',
        'Dir::Etc::sourceparts "-";',
        'APT::Update::Error-Mode "any";',
    ]


def test_new_apt_process_uses_only_ubuntu_including_security(tmp_path):
    import os
    import shutil
    import subprocess

    apt = shutil.which("apt-get")
    if apt is None:
        pytest.skip("APT source resolution runs on the Linux lane")
    parts = tmp_path / "sources.list.d"
    parts.mkdir()
    (parts / "ubuntu.sources").write_text(SOURCE.replace(mirror.ORIGINAL, "https://archive.ubuntu.com/ubuntu"))
    (parts / "vendor.list").write_text("deb https://unrelated.invalid/ubuntu noble main\n")
    (tmp_path / "sources.list").write_text("deb https://legacy.invalid/ubuntu noble main\n")
    config_parts = tmp_path / "apt.conf.d"
    config_parts.mkdir()
    # Relocate the system config for this fixture. The clean child receives no browser-specific options.
    bootstrap = tmp_path / "bootstrap.conf"
    bootstrap.write_text(f'Dir::Etc "{tmp_path}";\n')
    env = {"APT_CONFIG": str(bootstrap), "PATH": os.defpath, "LC_ALL": "C"}

    def fetch_plan():
        return subprocess.check_output([apt, "--print-uris", "update"], env=env, text=True, timeout=10)

    before = fetch_plan()
    assert "unrelated.invalid" in before
    assert "legacy.invalid" in before
    shutil.copyfile(SCRIPT.with_name("ubuntu_browser_mirror.conf"), config_parts / "99-heinzel-browser-sources")
    after = fetch_plan()
    assert "unrelated.invalid" not in after
    assert "legacy.invalid" not in after
    for suite in ("noble", "noble-updates", "noble-backports", "noble-security"):
        assert f"https://archive.ubuntu.com/ubuntu/dists/{suite}/InRelease" in after
