"""A reviewed Makefile exception never grants authority over other execution inputs."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def focused_gate(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from scripts import focused_gate

    return focused_gate


@pytest.fixture
def authorization(monkeypatch, focused_gate):
    base, head, merge, tree, old, new = (letter * 40 for letter in "abcdef")
    source = SimpleNamespace(commit=merge, source=SimpleNamespace(head=head))
    inputs = dict.fromkeys(focused_gate.FULL_EXECUTION_INPUTS, old)
    inputs["Makefile"] = new
    changed = {"Makefile": "M", "apple/App/HeinzelApp.swift": "M"}
    raw = b"".join(f":100644 100644 {old} {new} {status}\0{path}\0".encode() for path, status in changed.items())
    monkeypatch.setattr(focused_gate, "raw_diff", lambda *_: raw)
    monkeypatch.setattr(focused_gate, "object_id", lambda _: tree)
    monkeypatch.setattr(focused_gate, "git_blob", lambda commit, path: old if commit == base else inputs[path])
    entry = {
        "schema": 1,
        "kind": "full-pr-reviewed",
        "repository": focused_gate.REPOSITORY,
        "head": head,
        "base": base,
        "tree": tree,
        "changed": changed,
        "execution": inputs.copy(),
    }
    return source, base, entry, inputs


def test_reviewed_full_pr_binds_exact_execution_and_source(authorization, focused_gate):
    source, base, entry, _inputs = authorization
    focused_gate.full_pr_inputs(source, base, entry)
    with pytest.raises(focused_gate.CandidateError, match="execution inputs differ"):
        focused_gate.full_pr_inputs(source, base, None)


@pytest.mark.parametrize("field", ["schema", "kind", "repository", "head", "base", "tree", "changed", "execution"])
@pytest.mark.parametrize("defect", ["missing", "wrong", "null", "list"])
def test_reviewed_full_pr_rejects_incomplete_or_stale_authorization(authorization, focused_gate, field, defect):
    source, base, entry, _inputs = authorization
    if defect == "missing":
        del entry[field]
    else:
        entry[field] = {"wrong": "wrong", "null": None, "list": []}[defect]
    with pytest.raises(focused_gate.CandidateError):
        focused_gate.full_pr_inputs(source, base, entry)


@pytest.mark.parametrize("field", ["approved", "selection", "mode", "config_blob", "commit"])
def test_reviewed_full_pr_rejects_extra_authority_fields(authorization, focused_gate, field):
    source, base, entry, _inputs = authorization
    entry[field] = True
    with pytest.raises(focused_gate.CandidateError):
        focused_gate.full_pr_inputs(source, base, entry)


def test_reviewed_full_pr_rejects_changed_execution_after_review(authorization, focused_gate):
    source, base, entry, inputs = authorization
    for path in focused_gate.FULL_EXECUTION_INPUTS:
        old = inputs[path]
        inputs[path] = "0" * 40
        with pytest.raises(focused_gate.CandidateError):
            focused_gate.full_pr_inputs(source, base, entry)
        inputs[path] = old


def test_review_cannot_authorize_other_execution_changes(authorization, focused_gate):
    source, base, entry, inputs = authorization
    for path in focused_gate.FULL_EXECUTION_INPUTS:
        if path == "Makefile":
            continue
        old = inputs[path]
        inputs[path] = entry["execution"][path] = "0" * 40
        with pytest.raises(focused_gate.CandidateError):
            focused_gate.full_pr_inputs(source, base, entry)
        inputs[path] = entry["execution"][path] = old


@pytest.mark.parametrize("defect", ["bool-schema", "missing-path", "extra-path", "status", "extra-input", "unchanged"])
def test_reviewed_full_pr_rejects_ambiguous_or_inexact_manifest(authorization, focused_gate, defect):
    source, base, original, inputs = authorization
    entry = deepcopy(original)
    if defect == "bool-schema":
        entry["schema"] = True
    elif defect == "missing-path":
        del entry["changed"]["apple/App/HeinzelApp.swift"]
    elif defect == "extra-path":
        entry["changed"]["unreviewed.py"] = "M"
    elif defect == "status":
        entry["changed"]["Makefile"] = "A"
    elif defect == "extra-input":
        entry["execution"]["unreviewed.py"] = "0" * 40
    else:
        inputs["Makefile"] = entry["execution"]["Makefile"] = inputs["pyproject.toml"]
    with pytest.raises(focused_gate.CandidateError):
        focused_gate.full_pr_inputs(source, base, entry)


def test_review_cannot_authorize_mode_only_protected_input_changes(authorization, focused_gate, monkeypatch):
    source, base, entry, inputs = authorization
    original = focused_gate.raw_diff(base, source.commit)
    for path in focused_gate.FULL_EXECUTION_INPUTS:
        if path == "Makefile":
            continue
        blob = inputs[path]
        raw = original + f":100644 100755 {blob} {blob} M\0{path}\0".encode()
        monkeypatch.setattr(focused_gate, "raw_diff", lambda *_, value=raw: value)
        reviewed = deepcopy(entry)
        reviewed["changed"][path] = "M"
        with pytest.raises(focused_gate.CandidateError, match="only a reviewed Makefile"):
            focused_gate.full_pr_inputs(source, base, reviewed)


@pytest.mark.parametrize("transport", ["legacy-slot", "two-slots"])
@pytest.mark.parametrize("selected", [True, False])
def test_duplicate_full_pr_authorizations_refuse_even_when_identical(focused_gate, transport, selected):
    from scripts.focused_plan import encode_slot

    key = f"pull_request:{'a' * 40}"
    registry = {key: {"kind": focused_gate.FULL_PR_KIND}}
    encoded = encode_slot(registry)
    manifest, slots = (json.dumps(registry), (encoded,)) if transport == "legacy-slot" else ("{}", (encoded, encoded))
    with pytest.raises(focused_gate.CandidateError, match="duplicate full PR authorization"):
        focused_gate.selected_pr_registry(manifest, slots, key if selected else f"pull_request:{'b' * 40}")


def test_identical_legacy_focused_entries_remain_compatible(focused_gate):
    from scripts.focused_plan import encode_slot

    key = f"pull_request:{'a' * 40}"
    registry = {key: {"schema": 1, "mode": "pr-scoped"}}
    selected = focused_gate.selected_pr_registry(json.dumps(registry), (encode_slot(registry),) * 2, key)
    assert json.loads(selected) == registry
