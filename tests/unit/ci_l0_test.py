"""Level L0 of the test selection, run for real: the `changes` job's path step on real path sets.

The step's script is taken from ci.yml and run with a stub `git` that reports the changed paths, so what the workflow
decides is what is tested: a pull request of Apple files or prose alone skips the Python jobs; anything mixed, unknown,
shared, large, a release branch, a candidate, a batch or a push keeps the complete run (fail closed).
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
STEP = next(s for s in WORKFLOW["jobs"]["changes"]["steps"] if s.get("id") == "paths")
OUTPUTS = WORKFLOW["jobs"]["changes"]["outputs"]

pytestmark = [pytest.mark.unit, pytest.mark.timeout(60)]


def decide(tmp_path, changed, **options):
    event, release_full = options.get("event", "pull_request"), options.get("release_full", "false")
    head_ref, batch = options.get("head_ref", "feature/x"), options.get("batch", "")
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    listing = tmp_path / "changed.txt"
    listing.write_text("\n".join(changed) + "\n", encoding="utf-8")
    (stub / "git").write_text(f'#!/bin/sh\nif [ "$1" = diff ]; then cat "{listing}"; else exec /usr/bin/git "$@"; fi\n')
    (stub / "git").chmod(0o755)
    out = tmp_path / "out.txt"
    out.write_text("")
    env = {
        **os.environ,
        "PATH": f"{stub}:{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(out),
        "EVENT": event,
        "FULL_RELEASE": "true",  # what the workflow sets today: the real flag is RELEASE_FULL
        "RELEASE_FULL": release_full,
        "BASE": "base",
        "BATCH_BASE": batch,
        "HEAD_REF": head_ref,
    }
    subprocess.run(["bash", "-e", "-c", STEP["run"]], cwd=ROOT, env=env, check=True, capture_output=True, timeout=60)
    return dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line)


def test_an_apple_only_pull_request_selects_the_apple_job_and_no_python_job(tmp_path):
    got = decide(tmp_path, ["apple/App/HeinzelApp.swift", "apple/Widgets/W.swift"])
    assert (got["surface_only"], got["core"]) == ("true", "false")


def test_a_prose_only_pull_request_skips_the_python_jobs(tmp_path):
    got = decide(tmp_path, ["docs/guide/start.md"])
    assert got["core"] == "false"


@pytest.mark.parametrize(
    "changed",
    [
        ["apple/App/HeinzelApp.swift", "src/heinzel/ports.py"],
        ["apple/App/HeinzelApp.swift", "web/src/App.tsx"],
        ["web/src/App.tsx"],
        ["contexts.toml"],
        ["apple/App/HeinzelApp.swift", "unknown/path.bin"],
        ["CHANGELOG.md"],
        [".github/workflows/ci.yml", "apple/x.swift"],
    ],
)
def test_mixed_shared_or_unknown_paths_keep_the_complete_run(tmp_path, changed):
    got = decide(tmp_path, changed)
    assert (got["surface_only"], got["core"]) == ("false", "true")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"event": "push"},
        {"event": "schedule"},
        {"release_full": "true"},
        {"head_ref": "release/0.9.54"},
        {"batch": "abc"},
    ],
)
def test_release_main_nightly_and_batches_stay_complete_even_for_apple_files(tmp_path, kwargs):
    got = decide(tmp_path, ["apple/App/HeinzelApp.swift"], **kwargs)
    assert (got["surface_only"], got["core"]) == ("false", "true")


def test_a_refactor_sized_change_stays_complete(tmp_path):
    got = decide(tmp_path, [f"apple/App/File{i}.swift" for i in range(250)])
    assert got["surface_only"] == "false"


def test_the_apple_job_runs_for_an_apple_only_change_and_ci_ok_asks_for_it():
    jobs = WORKFLOW["jobs"]
    assert "surface_only == 'true'" in jobs["macos-specific"]["if"]
    assert "surface_only" in OUTPUTS
    [complete] = [s for s in jobs["ci-ok"]["steps"] if s["name"].startswith("Require the complete direct CI workload")]
    assert "macos-specific" in complete["run"].split("SURFACE_ONLY")[-1]
    assert complete["env"]["SURFACE_ONLY"] == "${{ needs.changes.outputs.surface_only }}"
