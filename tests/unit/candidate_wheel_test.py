"""Lifecycle source fixtures retain exact release identities as the candidate version advances."""

import zipfile

import pytest

from tests.e2e import _candidate_wheel as candidate

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("version", ["0.9.17", "0.9.18", "0.9.19"])
@pytest.mark.parametrize("defect", [None, "project", "lock", "filename", "name", "version", "duplicate-version"])
def test_candidate_requires_independent_source_lock_filename_and_wheel_identity(tmp_path, monkeypatch, version, defect):
    monkeypatch.setattr(candidate, "CANDIDATE_VERSION", version)
    other = "0.9.99"
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "heinzel"\nversion = "{other if defect == "project" else version}"\n'
    )
    (tmp_path / "uv.lock").write_text(
        f'[[package]]\nname = "heinzel"\nversion = "{other if defect == "lock" else version}"\n'
    )
    (tmp_path / "dist").mkdir()
    target = tmp_path / f"dist/heinzel-{other if defect == 'filename' else version}-py3-none-any.whl"
    with zipfile.ZipFile(target, "w") as archive:
        metadata = (
            f"Name: {'foreign' if defect == 'name' else 'heinzel'}\n"
            f"Version: {other if defect == 'version' else version}\n"
        )
        if defect == "duplicate-version":
            metadata += f"Version: {version}\n"
        archive.writestr(f"heinzel-{version}.dist-info/METADATA", metadata)
    if defect:
        with pytest.raises(AssertionError):
            candidate.candidate_wheel(tmp_path)
    else:
        assert candidate.candidate_wheel(tmp_path) == target
