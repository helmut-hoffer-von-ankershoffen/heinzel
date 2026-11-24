"""Level L0 of the test selection, run for real: the `changes` job's path step on real path sets.

The step's script is taken from ci.yml and run with a stub `git` that reports the changed paths, so what the workflow
decides is what is tested: a pull request of Apple files or prose alone skips the Python jobs; anything mixed, unknown,
shared, large, a release branch, a candidate, a batch or a push keeps the complete run (fail closed).
"""

import os
import re
import subprocess
from collections import defaultdict
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


def evaluate(condition: str, outputs: dict[str, str]) -> bool:
    """A job's `if` as GitHub reads it, for the few forms ci.yml uses (outputs, event name, `!cancelled()`)."""
    text = str(condition).replace("${{", "").replace("}}", "")
    text = text.split(" # ")[0]  # a trailing YAML comment
    text = re.sub(r"!\s*cancelled\(\)", "True", text)
    text = re.sub(r"always\(\)", "True", text)
    text = re.sub(r"needs\.[\w-]+\.outputs\.(\w+)", lambda m: f"out[{m.group(1)!r}]", text)
    text = re.sub(r"needs\.[\w-]+\.result", "'success'", text)
    text = text.replace("github.event_name", "'pull_request'").replace("github.ref", "'refs/pull/1/merge'")
    text = text.replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"!(?!=)", " not ", text)
    return bool(eval(text, {"__builtins__": {}}, {"out": defaultdict(str, outputs)}))  # noqa: S307 - our own workflow text


def test_every_job_the_reduced_workload_requires_runs_when_surface_only_is_set():
    """CI OK demands these jobs to be `success`; a job skipped on the same run would fail every such pull request.

    Each job's `if` is evaluated with the outputs of an Apple-only pull request: core false, surface_only true.
    """
    [complete] = [s for s in WORKFLOW["jobs"]["ci-ok"]["steps"] if s["name"].startswith("Require the complete direct")]
    listed = next(
        line
        for line in complete["run"].splitlines()
        if line.strip().startswith("required=(admission changes lint secrets commits")
    )
    required = listed.split("required=(")[1].split(")")[0].split()
    outputs = {
        "core": "false",
        "surface_only": "true",
        "full_ci": "true",
        "site_only": "false",
        "scheduled_maintenance": "",
        "focused_mode": "",
        "lane": "",
        "full_release": "false",
        "full": "true",
    }
    skipped = [
        job for job in required if "if" in WORKFLOW["jobs"][job] and not evaluate(WORKFLOW["jobs"][job]["if"], outputs)
    ]
    assert skipped == [], (
        f"CI OK requires these jobs but their `if` skips them for a surface-only pull request: {skipped}"
    )


def test_the_evaluator_sees_a_job_that_core_gates():
    assert (
        evaluate("needs.changes.outputs.core == 'true' && needs.changes.outputs.lane != 'macos'", {"core": "false"})
        is False
    )
    assert evaluate(
        "needs.changes.outputs.core == 'true' || needs.changes.outputs.surface_only == 'true'", {"surface_only": "true"}
    )
