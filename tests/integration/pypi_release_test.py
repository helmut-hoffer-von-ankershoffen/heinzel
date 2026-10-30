"""The release verifier binds actual Git tags, main ancestry and published release identity."""

import importlib.util
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("pypi_release", ROOT / "scripts/pypi_release.py")
assert SPEC is not None
assert SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def test_current_built_wheel_passes_release_payload_validation(tmp_path):
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)], cwd=ROOT, check=True, capture_output=True, timeout=300
    )
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    release._wheel(tmp_path / f"heinzel-{version}-py3-none-any.whl", version)


@pytest.fixture
def candidate(tmp_path):
    git = shutil.which("git")
    assert git is not None

    def run(*arguments):
        return subprocess.run(
            [git, *arguments], cwd=tmp_path, check=True, capture_output=True, text=True, timeout=15
        ).stdout.strip()

    run("init", "--initial-branch=main")
    run("config", "user.name", "Fixture")
    run("config", "user.email", "fixture@example.invalid")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="heinzel"\nversion="1.2.3"\n')
    run("add", "pyproject.toml")
    run("commit", "-m", "chore(release): v1.2.3 (#10)")
    run("tag", "-a", "v1.2.3", "-m", "v1.2.3")
    run("update-ref", "refs/remotes/origin/main", "HEAD")
    published = {
        "tag_name": "v1.2.3",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-10-25T12:00:00Z",
        "html_url": f"https://github.com/{release.REPOSITORY}/releases/tag/v1.2.3",
    }
    return tmp_path, run, published


def test_exact_annotated_tag_on_main_binds_to_the_published_release(candidate):
    root, git, published = candidate
    assert release.verify_source(root, "v1.2.3", published) == git("rev-parse", "HEAD")


def test_same_named_branch_cannot_substitute_for_the_actual_tag(candidate):
    root, git, published = candidate
    git("switch", "-c", "v1.2.3")
    (root / "unreviewed.txt").write_text("different tree")
    git("add", "unreviewed.txt")
    git("commit", "-m", "feat: unrelated branch")
    with pytest.raises(release.ReleaseError, match="not the exact peeled"):
        release.verify_source(root, "v1.2.3", published)


def test_release_tag_outside_main_is_refused(candidate):
    root, git, published = candidate
    git("switch", "-c", "unmerged-release")
    (root / "pyproject.toml").write_text('[project]\nname="heinzel"\nversion="1.2.4"\n')
    git("add", "pyproject.toml")
    git("commit", "-m", "chore(release): v1.2.4")
    git("tag", "-a", "v1.2.4", "-m", "v1.2.4")
    with pytest.raises(release.ReleaseError, match="ancestry"):
        release.verify_source(root, "v1.2.4", {**published, "tag_name": "v1.2.4"})


@pytest.mark.parametrize(
    "change",
    [
        {"draft": True},
        {"prerelease": True},
        {"tag_name": "v1.2.4"},
        {"published_at": None},
        {"html_url": "https://example.invalid/release"},
    ],
)
def test_unpublished_or_different_release_metadata_is_refused(candidate, change):
    root, _, published = candidate
    with pytest.raises(release.ReleaseError, match="genuine GitHub release"):
        release.verify_source(root, "v1.2.3", {**published, **change})


def test_project_identity_must_match_the_tag(candidate):
    root, _, published = candidate
    (root / "pyproject.toml").write_text('[project]\nname="heinzel-other"\nversion="1.2.3"\n')
    with pytest.raises(release.ReleaseError, match="project name/version"):
        release.verify_source(root, "v1.2.3", published)
