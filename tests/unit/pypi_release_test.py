"""Only complete core distributions cross the isolated PyPI publishing boundary."""

import hashlib
import importlib.util
import io
import json
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pypi_release", ROOT / "scripts/pypi_release.py")
assert SPEC is not None
assert SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
VERSION = "1.2.3"
COMMIT = "a" * 40


@pytest.fixture
def artifacts(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dict.fromkeys(release.REQUIRED, b"payload")
    wheel[release.NOTIFIER] = b"\xca\xfe\xba\xbepayload"
    wheel[f"heinzel-{VERSION}.dist-info/METADATA"] = b"Name: heinzel\nVersion: 1.2.3\n"
    wheel[f"heinzel-{VERSION}.dist-info/entry_points.txt"] = (
        b"[console_scripts]\nheinzel = _heinzel_launcher:main\nhz = _heinzel_launcher:cli\n"
        b"hz-install = _heinzel_launcher:install\n[pytest11]\nheinzel = heinzel.testing\n"
    )
    source = {
        f"heinzel-{VERSION}/src/{name}": b"payload" for name in release.REQUIRED if name != "heinzel/CHANGELOG.md"
    }
    source[f"heinzel-{VERSION}/CHANGELOG.md"] = b"Changes"
    source[f"heinzel-{VERSION}/PKG-INFO"] = b"Name: heinzel\nVersion: 1.2.3\n"
    source[f"heinzel-{VERSION}/pyproject.toml"] = b'[project]\nname="heinzel"\nversion="1.2.3"\n'

    def write(
        *,
        wheel_changes=None,
        source_changes=None,
        notifier_mode=stat.S_IFREG | 0o755,
        launcher_mode=stat.S_IFREG | 0o644,
        source_launcher_symlink=False,
    ):
        for names, changes in ((wheel, wheel_changes), (source, source_changes)):
            for name, value in (changes or {}).items():
                if value is None:
                    names.pop(name, None)
                else:
                    names[name] = value
        with zipfile.ZipFile(dist / f"heinzel-{VERSION}-py3-none-any.whl", "w") as archive:
            for name, value in wheel.items():
                info = zipfile.ZipInfo(name)
                mode = (
                    notifier_mode
                    if name == release.NOTIFIER
                    else launcher_mode
                    if name == "_heinzel_launcher.py"
                    else stat.S_IFREG | 0o644
                )
                info.external_attr = mode << 16
                archive.writestr(info, value)
        with tarfile.open(dist / f"heinzel-{VERSION}.tar.gz", "w:gz") as archive:
            for name, value in source.items():
                info = tarfile.TarInfo(name)
                if source_launcher_symlink and name == f"heinzel-{VERSION}/src/_heinzel_launcher.py":
                    info.type = tarfile.SYMTYPE
                    info.linkname = "other.py"
                    archive.addfile(info)
                    continue
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))
        return dist

    return write


def test_complete_core_artifacts_have_exact_identity_and_sha256_manifest(artifacts):
    dist = artifacts()
    manifest = release.verify_artifacts(dist, "v1.2.3", COMMIT)
    assert manifest == {
        "schema": 1,
        "project": "heinzel",
        "version": VERSION,
        "commit": COMMIT,
        "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(dist.iterdir())},
    }


@pytest.mark.parametrize(
    "entries",
    [
        b"[console_scripts]\nhz = heinzel.heads.cli:main\n",
        b"[console_scripts]\nhz = _heinzel_launcher:cli\nheinzel = _heinzel_launcher:main\n",
        b"[console_scripts]\nhz = _heinzel_launcher:cli\nheinzel = _heinzel_launcher:main\nhz-install = wrong:target\n",
        (
            b"[console_scripts]\nhz = _heinzel_launcher:cli\nhz = _heinzel_launcher:cli\n"
            b"heinzel = _heinzel_launcher:main\nhz-install = _heinzel_launcher:install\n"
        ),
        (
            b"[pytest11]\nhz = _heinzel_launcher:cli\nheinzel = _heinzel_launcher:main\n"
            b"hz-install = _heinzel_launcher:install\n"
        ),
        (
            b"[DEFAULT]\nhz = _heinzel_launcher:cli\nheinzel = _heinzel_launcher:main\n"
            b"hz-install = _heinzel_launcher:install\n[console_scripts]\n"
        ),
    ],
)
def test_wrong_missing_duplicate_or_misplaced_console_scripts_are_refused(artifacts, entries):
    dist = artifacts(wheel_changes={f"heinzel-{VERSION}.dist-info/entry_points.txt": entries})
    with pytest.raises(release.ReleaseError, match="console scripts"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_empty_launcher_payload_is_refused(artifacts):
    dist = artifacts(wheel_changes={"_heinzel_launcher.py": b""})
    with pytest.raises(release.ReleaseError, match="empty"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_symlink_launcher_payload_is_refused(artifacts):
    dist = artifacts(launcher_mode=stat.S_IFLNK | 0o777)
    with pytest.raises(release.ReleaseError, match="regular"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_empty_source_launcher_is_refused(artifacts):
    dist = artifacts(source_changes={f"heinzel-{VERSION}/src/_heinzel_launcher.py": b""})
    with pytest.raises(release.ReleaseError, match="nonempty"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_symlink_source_launcher_is_refused(artifacts):
    dist = artifacts(source_launcher_symlink=True)
    with pytest.raises(release.ReleaseError, match="regular"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_missing_source_launcher_is_refused(artifacts):
    dist = artifacts(source_changes={f"heinzel-{VERSION}/src/_heinzel_launcher.py": None})
    with pytest.raises(release.ReleaseError, match="incomplete"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


@pytest.mark.parametrize("missing", release.REQUIRED)
def test_missing_wheel_payload_is_refused(artifacts, missing):
    dist = artifacts(wheel_changes={missing: None})
    with pytest.raises(release.ReleaseError, match="lacks"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


@pytest.mark.parametrize(
    "metadata",
    [
        b"Name: other\nVersion: 1.2.3\n",
        b"Name: heinzel\nVersion: 1.2.4\n",
        b"Name: heinzel\nName: other\nVersion: 1.2.3\n",
    ],
)
def test_mismatched_or_ambiguous_metadata_is_refused(artifacts, metadata):
    dist = artifacts(wheel_changes={f"heinzel-{VERSION}.dist-info/METADATA": metadata})
    with pytest.raises(release.ReleaseError, match="metadata"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


@pytest.mark.parametrize("mode", [stat.S_IFREG | 0o644, stat.S_IFLNK | 0o755])
def test_notifier_must_be_a_real_executable_payload(artifacts, mode):
    dist = artifacts(notifier_mode=mode)
    with pytest.raises(release.ReleaseError, match=r"regular|executable"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_notifier_placeholder_is_refused(artifacts):
    dist = artifacts(wheel_changes={release.NOTIFIER: b"not a binary"})
    with pytest.raises(release.ReleaseError, match="universal"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_incomplete_sdist_is_refused(artifacts):
    dist = artifacts(source_changes={f"heinzel-{VERSION}/src/heinzel/heads/web/static/index.html": None})
    with pytest.raises(release.ReleaseError, match="incomplete"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


def test_unpublished_plugin_or_extra_file_cannot_enter_the_upload(artifacts):
    dist = artifacts()
    (dist / "heinzel-harbor-1.2.3.whl").write_bytes(b"plugin")
    with pytest.raises(release.ReleaseError, match="only one core"):
        release.verify_artifacts(dist, "v1.2.3", COMMIT)


@pytest.mark.parametrize("tag", ["main", "v1.2.3rc1", "v01.2.3", "v1.2.3;echo unsafe", "refs/tags/v1.2.3"])
def test_only_final_exact_version_tags_are_allowed(tag):
    with pytest.raises(release.ReleaseError, match="genuine final release tag"):
        release.version_for(tag)


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/pypi.yml").read_text())


def test_uv_build_helper_is_removed_before_strict_artifact_verification(artifacts):
    dist = artifacts()
    (dist / ".gitignore").write_text("*", encoding="utf-8")
    steps = workflow()["jobs"]["build"]["steps"]
    build = next(
        index for index, step in enumerate(steps) if step.get("run") == "uv build --package heinzel --out-dir dist"
    )
    cleanup = next(index for index, step in enumerate(steps) if step.get("run") == "make pypi-clean-build-marker")
    verify = next(index for index, step in enumerate(steps) if "pypi_release.py artifacts" in step.get("run", ""))

    assert build < cleanup < verify
    subprocess.run(
        ["make", "pypi-clean-build-marker", f"PYPI_DIST={dist}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert not (dist / ".gitignore").exists()
    manifest = release.verify_artifacts(dist, "v1.2.3", COMMIT)
    assert set(manifest["files"]) == {f"heinzel-{VERSION}-py3-none-any.whl", f"heinzel-{VERSION}.tar.gz"}


def test_oidc_authority_is_isolated_from_all_build_and_checkout_steps():
    spec = workflow()
    assert spec["permissions"] == {}
    assert spec[True] == {"workflow_dispatch": None}  # YAML1.1 represents GitHub's 'on' as True.
    assert spec["concurrency"]["cancel-in-progress"] is False
    jobs = spec["jobs"]
    for name in ("notifier", "build"):
        assert jobs[name]["permissions"] == {"contents": "read"}
        checkout = next(step for step in jobs[name]["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"]["ref"] == "refs/tags/${{ github.ref_name }}"
        assert checkout["with"]["persist-credentials"] is False
    publisher = jobs["publish"]
    assert publisher["permissions"] == {"id-token": "write"}
    assert publisher["environment"]["name"] == "pypi"
    assert publisher["needs"] == "build"
    assert all("refs/tags/v" in job["if"] and release.REPOSITORY in job["if"] for job in jobs.values())
    assert not any(step.get("uses", "").startswith("actions/checkout") for step in publisher["steps"])
    upload = publisher["steps"][-1]
    assert upload["uses"] == "pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086"
    assert upload["with"] == {
        "packages-dir": "release-artifacts/dist",
        "attestations": True,
        "skip-existing": False,
        "verify-metadata": True,
    }


@pytest.mark.parametrize("change", [None, "bytes", "commit", "extra", "symlink"])
def test_publish_job_rechecks_identity_and_bytes_without_project_code(artifacts, tmp_path, monkeypatch, change):
    dist = artifacts()
    manifest = release.verify_artifacts(dist, "v1.2.3", COMMIT)
    root = tmp_path / "release-artifacts"
    root.mkdir()
    dist.rename(root / "dist")
    if change == "bytes":
        next((root / "dist").glob("*.whl")).write_bytes(b"modified")
    elif change == "commit":
        manifest["commit"] = "b" * 40
    elif change == "extra":
        (root / "dist/other.whl").write_bytes(b"extra")
    elif change == "symlink":
        wheel = next((root / "dist").glob("*.whl"))
        outside = tmp_path / "outside.whl"
        wheel.rename(outside)
        wheel.symlink_to(outside)
    (root / "artifact-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setenv("RELEASE_TAG", "v1.2.3")
    monkeypatch.setenv("SOURCE_COMMIT", COMMIT)
    steps = workflow()["jobs"]["publish"]["steps"]
    script = next(step["run"] for step in steps if "run" in step).split("\n", 1)[1].removesuffix("PY\n")
    # Run the exact standard-library verification used in the credentialed job, not a matching reimplementation.
    monkeypatch.chdir(tmp_path)
    if change is None:
        exec(compile(script, "<publish-manifest-verifier>", "exec"), {})  # noqa: S102 - exercise the exact trusted workflow verifier
    else:
        with pytest.raises(SystemExit):
            exec(compile(script, "<publish-manifest-verifier>", "exec"), {})  # noqa: S102 - exercise the exact trusted workflow verifier
