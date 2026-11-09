"""Focused verdicts bind the entire diff, trusted source, run and selected job results."""

import hashlib
import runpy
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SELECTOR = SCRIPTS / "focused_ci_receipt.py"
POLICY = runpy.run_path(str(SELECTOR))
Change, Run, Receipt, verify = (POLICY[name] for name in ("Change", "Run", "Receipt", "verify"))

RAW = b":100644 100644 " + b"1" * 40 + b" " + b"2" * 40 + b" M\0tests/unit/example_test.py\0"
RUN = Run(
    repository_id=123,
    pr=7,
    base="1" * 40,
    head="2" * 40,
    merge="3" * 40,
    tree="4" * 40,
    workflow_id=456,
    workflow_sha256="5" * 64,
    selector_sha256="6" * 64,
    run_id=789,
    attempt=1,
    event="pull_request",
)
CHANGE = Change(RUN.base, RUN.head, hashlib.sha256(RAW).hexdigest(), RUN.selector_sha256, ("Linux", "ARM macOS"))
JOBS = (("ARM macOS", "success"), ("Linux", "success"))
RECEIPT = Receipt(RUN, JOBS)


def test_exact_success_is_accepted_regardless_of_job_order():
    verify(CHANGE, current=RUN, raw_diff=RAW, receipt=RECEIPT)


@pytest.mark.parametrize(
    "changed",
    [
        b"",
        RAW + b"extra\0",
        RAW.replace(b"100644", b"100755", 1),
        RAW.replace(b"1" * 40, b"3" * 40),
        RAW.replace(b"example_test", b"renamed_test"),
    ],
)
def test_only_the_exact_git_diff_is_admitted(changed):
    with pytest.raises(ValueError, match="reviewed change"):
        verify(CHANGE, current=RUN, raw_diff=changed, receipt=RECEIPT)


@pytest.mark.parametrize("field", ["base", "head"])
def test_even_a_matching_receipt_cannot_admit_a_moved_base_or_head(field):
    current = replace(RUN, **{field: "9" * 40})
    with pytest.raises(ValueError, match="No reviewed change"):
        verify(CHANGE, current=current, raw_diff=RAW, receipt=Receipt(current, JOBS))


def test_even_a_matching_receipt_cannot_admit_a_different_selector():
    current = replace(RUN, selector_sha256="9" * 64)
    with pytest.raises(ValueError, match="reviewed selector"):
        verify(CHANGE, current=current, raw_diff=RAW, receipt=Receipt(current, JOBS))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository_id", 1),
        ("pr", 1),
        ("base", "9" * 40),
        ("head", "9" * 40),
        ("merge", "9" * 40),
        ("tree", "9" * 40),
        ("workflow_id", 1),
        ("workflow_sha256", "9" * 64),
        ("selector_sha256", "9" * 64),
        ("run_id", 1),
        ("attempt", 2),
        ("event", "workflow_dispatch"),
    ],
)
def test_every_receipt_identity_field_must_match_the_current_run(field, value):
    old = replace(RUN, **{field: value})
    with pytest.raises(ValueError, match="receipt is stale"):
        verify(CHANGE, current=RUN, raw_diff=RAW, receipt=Receipt(old, JOBS))


@pytest.mark.parametrize("event", ["workflow_dispatch", "push", "schedule", "merge_group"])
def test_other_events_cannot_issue_a_focused_pr_verdict(event):
    current = replace(RUN, event=event)
    with pytest.raises(ValueError, match="Invalid focused PR run identity"):
        verify(CHANGE, current=current, raw_diff=RAW, receipt=Receipt(current, JOBS))


@pytest.mark.parametrize("jobs", [(), JOBS[:1], JOBS + JOBS[:1], (*JOBS, ("Extra", "success"))])
def test_job_list_must_be_exact_and_complete(jobs):
    with pytest.raises(ValueError, match="missing, duplicate, or unexpected"):
        verify(CHANGE, current=RUN, raw_diff=RAW, receipt=Receipt(RUN, jobs))


@pytest.mark.parametrize("conclusion", ["skipped", "failure", "cancelled", "neutral", "", None])
def test_only_success_satisfies_a_selected_job(conclusion):
    jobs = (("ARM macOS", conclusion), JOBS[1])
    with pytest.raises(ValueError, match="did not succeed"):
        verify(CHANGE, current=RUN, raw_diff=RAW, receipt=Receipt(RUN, jobs))


def test_transition_manifest_pins_the_exact_offline_selector():
    manifest = tomllib.loads((SCRIPTS / "focused_ci_transition.toml").read_text())
    selector_sha256 = manifest.pop("source")["selector_sha256"]
    assert selector_sha256 == hashlib.sha256(SELECTOR.read_bytes()).hexdigest()
    assert {"step_signal", "desktop_download_recovery"} <= manifest.keys()
    for entry in manifest.values():
        change = Change(**{**entry, "selector_sha256": selector_sha256})
        assert len(change.base) == len(change.head) == 40
        assert len(change.diff_sha256) == 64
        assert change.jobs
        assert len(change.jobs) == len(set(change.jobs))
