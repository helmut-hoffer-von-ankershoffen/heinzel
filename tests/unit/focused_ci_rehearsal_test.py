"""The rehearsal admits just one reviewed test-file diff from an unchanged protected base."""

import hashlib
import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BASE, HEAD, MERGE = (char * 40 for char in "abc")
RAW_DIFF = b"reviewed raw diff"
LS_REFS = ("ls-remote", "--exit-code", "origin", "refs/heads/main", "refs/pull/7/head", "refs/pull/7/merge")


@pytest.fixture
def scope(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    module = importlib.import_module("focused_ci_rehearsal")
    replies = {
        ("rev-parse", "HEAD"): BASE.encode(),
        LS_REFS: f"{BASE}\trefs/heads/main\n{HEAD}\trefs/pull/7/head\n{MERGE}\trefs/pull/7/merge\n".encode(),
        ("fetch", "--no-tags", "--no-recurse-submodules", "origin", "refs/pull/7/head", "refs/pull/7/merge"): b"",
        ("merge-base", "--is-ancestor", BASE, HEAD): b"",
        ("rev-list", "--parents", "-n", "1", MERGE): f"{MERGE} {BASE} {HEAD}".encode(),
        ("rev-parse", f"{MERGE}^{{tree}}"): b"tree",
        ("rev-parse", f"{HEAD}^{{tree}}"): b"tree",
        ("diff-tree", "--no-commit-id", "-r", "--name-status", "--no-renames", "-z", BASE, HEAD): (
            f"M\0{module.TEST}\0".encode()
        ),
        ("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", BASE, HEAD): RAW_DIFF,
    }
    monkeypatch.setattr(module, "git_bytes", lambda *args: replies[args])
    return module, replies


def test_only_the_exact_pinned_scope_passes(scope):
    scope[0].check_scope("7", BASE, HEAD, MERGE, hashlib.sha256(RAW_DIFF).hexdigest())


@pytest.mark.parametrize(
    ("key", "reply", "message"),
    [
        (("rev-parse", "HEAD"), HEAD.encode(), "from the approved base"),
        (LS_REFS, b"moved", "Protected main moved"),
        (LS_REFS, f"{BASE}\trefs/heads/main\n".encode(), "head or merge ref moved"),
        (("rev-list", "--parents", "-n", "1", MERGE), b"wrong parents", "Unexpected rehearsal merge"),
        (("rev-parse", f"{MERGE}^{{tree}}"), b"wrong tree", "Unexpected rehearsal merge"),
        (
            ("diff-tree", "--no-commit-id", "-r", "--name-status", "--no-renames", "-z", BASE, HEAD),
            b"M\0tests/unit/focused_ci_receipt_test.py\0M\0pyproject.toml\0",
            "modify only the receipt test file",
        ),
        (
            ("diff-tree", "--no-commit-id", "-r", "--raw", "--no-abbrev", "--no-renames", "-z", BASE, HEAD),
            b"same paths, different blobs",
            "diff does not match approval",
        ),
    ],
)
def test_rejects_untrusted_checkout_moved_base_or_changed_scope(scope, key, reply, message):
    module, replies = scope
    replies[key] = reply
    with pytest.raises(ValueError, match=message):
        module.check_scope("7", BASE, HEAD, MERGE, hashlib.sha256(RAW_DIFF).hexdigest())


def test_unset_pin_cannot_admit_rehearsal(scope):
    with pytest.raises(ValueError, match="Missing approved rehearsal diff digest"):
        scope[0].check_scope("7", BASE, HEAD, MERGE, "")
