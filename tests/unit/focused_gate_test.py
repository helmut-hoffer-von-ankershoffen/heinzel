"""The protected judge binds coverage evidence to an immutable original upload."""

import hashlib
import io
import zipfile

import pytest

from scripts import focused_gate
from scripts.release_candidate import CandidateError

pytestmark = pytest.mark.unit


def _archive(*names):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in names:
            archive.writestr(name, "{}")
    return output.getvalue()


@pytest.mark.parametrize(
    "defect", ["none", "wrong-id", "wrong-hash", "expired", "skipped", "wrong-name", "extra-entry"]
)
def test_focused_artifact_is_bound_to_its_successful_upload_and_digest(defect):
    run, job_id, artifact_id = 123, 456, 789
    name = f"focused-pr-evidence-python-{run}-1"
    steps = [{"name": focused_gate.UPLOAD_NAME, "number": 6, "status": "completed", "conclusion": "success"}]
    job = {"id": job_id, "steps": steps}
    entries = ["evidence.json", "junit.xml", "coverage.json"]
    if defect == "extra-entry":
        entries.append("coverage-total.txt")
    content = _archive(*entries)
    artifact = {
        "id": artifact_id,
        "size_in_bytes": len(content),
        "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
        "expired": defect == "expired",
    }
    if defect == "skipped":
        steps[0]["conclusion"] = "skipped"
    if defect == "wrong-hash":
        artifact["digest"] = "sha256:" + "0" * 64
    observed_id = artifact_id + 1 if defect == "wrong-id" else artifact_id
    observed_name = f"{name}-other" if defect == "wrong-name" else name
    log = (
        f"2026-11-10T08:00:00.000Z Artifact {observed_name} has been successfully uploaded! "
        f"Final size is {len(content)} bytes. Artifact ID is {observed_id}\n"
    ).encode()

    def read(path):
        return {
            f"actions/jobs/{job_id}/steps/6/logs": log,
            f"actions/artifacts/{artifact_id}/zip": content,
        }[path]

    if defect == "none":
        assert set(focused_gate._artifact(read, job, artifact, name)) == set(entries)
    else:
        with pytest.raises(CandidateError, match=r".+"):
            focused_gate._artifact(read, job, artifact, name)


def test_candidate_composite_still_binds_original_attempt_without_allowing_a_focused_rerun(monkeypatch):
    from scripts import merge_gate
    from scripts.release_candidate import REPOSITORY, WORKFLOW

    head = "a" * 40
    live = {
        "id": 123,
        "run_attempt": 2,
        "head_sha": head,
        "workflow_id": focused_gate.CI_WORKFLOW_ID,
        "path": WORKFLOW,
        "repository": {"full_name": REPOSITORY},
        "status": "completed",
        "conclusion": "success",
        "event": "pull_request",
        "check_suite_id": 333,
    }
    original = live | {"run_attempt": 1, "conclusion": "failure"}

    def github(_gate, path):
        if path == "actions/runs/123":
            return live
        if path == "actions/runs/123/attempts/1":
            return original
        return {"workflow_runs": [live]}

    monkeypatch.setattr(merge_gate.Gate, "github", github)
    gate = merge_gate.Gate(REPOSITORY, head=head)
    with pytest.raises(CandidateError, match="successful original attempt"):
        focused_gate._authenticated_run(gate, 123)
    assert focused_gate._authenticated_run(gate, 123, candidate_composite=True) == live
    original["head_sha"] = "b" * 40
    with pytest.raises(CandidateError, match="canonical CI workflow"):
        focused_gate._authenticated_run(gate, 123, candidate_composite=True)
