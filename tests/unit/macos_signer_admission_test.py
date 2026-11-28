"""Exercise the protected publish admission steps with isolated Git objects and synthetic event metadata."""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from heinzel.testing import scaled

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
REPO = "helmut-hoffer-von-ankershoffen/heinzel"
TAG = "v1.2.3"
MAIN = "refs/heads/main"
TAG_REF = f"refs/tags/{TAG}"


@pytest.mark.parametrize(
    ("event", "ref", "workflow", "defect"),
    [
        ("push", MAIN, "release-tag", ""),
        ("workflow_dispatch", MAIN, "release-tag", ""),
        ("push", TAG_REF, "publish", "direct-tag"),
        ("push", MAIN, "publish", "caller"),
        ("pull_request", MAIN, "release-tag", "event"),
        ("workflow_dispatch", TAG_REF, "publish", "event"),
        ("push", "refs/tags/v1.2.4", "publish", "tag"),
        ("push", MAIN, "release-tag", "unprotected"),
        ("push", MAIN, "release-tag", "sha"),
        ("push", MAIN, "release-tag", "off-main"),
        ("push", MAIN, "release-tag", "lightweight"),
        ("push", MAIN, "release-tag", "title"),
        ("push", MAIN, "release-tag", "version"),
    ],
)
def test_signing_admission_requires_the_official_event_and_reviewed_tag(tmp_path, event, ref, workflow, defect):
    jobs = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text())["jobs"]
    promote = jobs["promote"]["steps"]
    guard = next(
        step["run"] for step in promote if step.get("name") == "Bind the exact official core source before tooling"
    )
    step = next(step for step in promote if step.get("id") == "signing-source")
    assert step["run"] == "make desktop-swift-admit-release"
    admission = (ROOT / "apple/scripts/admit-macos-release").read_text()
    assert 'scripts/release_latest.py "$TAG" "$GITHUB_SHA" --signing' in admission
    admission = admission.splitlines()[:-1]  # The authenticated candidate verifier has its own API fixtures.
    admission = "\n".join(admission)
    env = {
        "HOME": str(tmp_path),
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GITHUB_REF": ref,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF_PROTECTED": "false" if defect == "unprotected" else "true",
        "GITHUB_WORKFLOW_REF": f"{REPO}/.github/workflows/{workflow}.yml@{ref}",
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "TAG": TAG,
    }

    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
            cwd=tmp_path,
            env=env,
            text=True,
            timeout=scaled(5),
        ).strip()

    git("init", "-q")
    git("commit", "--allow-empty", "-qm", "initial")
    base = git("rev-parse", "HEAD")
    version = "1.2.4" if defect == "version" else "1.2.3"
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname="heinzel"\nversion="{version}"\n')
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/pypi_release.py", tmp_path / "scripts/pypi_release.py")
    git("add", ".")
    git("commit", "-qm", "ordinary change" if defect == "title" else f"chore(release): {TAG}")
    head = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", base if defect == "off-main" else head)
    if defect == "lightweight":
        git("tag", TAG)
    else:
        git("tag", "-am", TAG, TAG)
    env["GITHUB_SHA"] = base if defect == "sha" else head
    # The test interpreter supplies the same stdlib-only entry point without provisioning another Python.
    admission = admission.replace(
        "uv run --no-project --frozen --no-build --python 3.15 python", shlex.quote(sys.executable)
    )
    run = subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", guard + "\n" + admission],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=scaled(10),
    )
    if defect:
        assert run.returncode != 0
        assert not (tmp_path / "output").exists() or not (tmp_path / "output").read_text()
    else:
        assert run.returncode == 0, run.stderr
        assert (tmp_path / "output").read_text().strip() == f"commit={head}"


def test_core_caller_passes_only_named_signing_inputs():
    caller = yaml.safe_load((ROOT / ".github/workflows/release-tag.yml").read_text())["jobs"]["publish"]
    assert caller["secrets"] == {
        name: "${{ secrets." + name + " }}"
        for name in (
            "CONFIDENTIAL_TERMS",
            "HEINZEL_MAC_CERTIFICATE_B64",
            "HEINZEL_MAC_CERTIFICATE_PASSWORD",
            "HEINZEL_MAC_NOTARY_KEY_B64",
        )
    }
