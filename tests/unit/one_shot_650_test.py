"""Offline tests of the unactivated updater."""

import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

source = Path(__file__).resolve().parents[2] / "scripts/one_shot_650.py"
spec = importlib.util.spec_from_file_location("updater", source)
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class TestUpdateRef:
    def exercise(self, drift=False):
        state = {"head": updater.OLD, "writes": []}

        def api(path, *args):
            if path == "git/ref/heads/main":
                return {"object": {"sha": "drift" if drift else updater.BASE}}
            if path == f"git/ref/heads/{updater.BRANCH}":
                return {"object": {"sha": state["head"]}}
            if path == "pulls/650":
                return {
                    "state": "open",
                    "draft": False,
                    "base": {"ref": "main"},
                    "head": {"sha": state["head"], "ref": updater.BRANCH},
                }
            if path.startswith("compare/"):
                return {"status": "ahead", "base_commit": {"sha": updater.OLD}}
            if path == f"git/refs/heads/{updater.BRANCH}":
                state["writes"].append(args)
                state["head"] = updater.NEW
                return {"object": {"sha": updater.NEW}}
            raise AssertionError(path)

        env = {
            "GITHUB_REPOSITORY": updater.REPO,
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_REF": "refs/heads/bootstrap/one-shot-650-update",
            "GITHUB_ACTOR": "helmut-hoffer-von-ankershoffen",
            "GITHUB_SHA": "reviewed",
            "REVIEWED_HELPER_SHA": "reviewed",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(updater, "api", api):
            if drift:
                with pytest.raises(ValueError, match="held until writer serialization"):
                    updater.main()
            else:
                updater.main()
        return state["writes"]

    def test_update_is_held_until_serialization_is_enforced(self):
        with pytest.raises(ValueError, match="held until writer serialization"):
            self.exercise()

    def test_hold_prevents_write_when_base_drifts(self):
        assert self.exercise(drift=True) == []


def test_snapshot_rejects_test_merge_with_moved_base():
    def api(path, *_args):
        if path == "git/ref/heads/main":
            return {"object": {"sha": updater.BASE}}
        if path == f"git/ref/heads/{updater.BRANCH}":
            return {"object": {"sha": updater.OLD}}
        if path == "pulls/650":
            return {
                "state": "open",
                "draft": False,
                "merge_commit_sha": "merge",
                "base": {"ref": "main", "repo": {"full_name": updater.REPO}},
                "head": {"sha": updater.OLD, "ref": updater.BRANCH, "repo": {"full_name": updater.REPO}},
            }
        if path == f"git/commits/{updater.NEW}":
            return {"tree": {"sha": updater.TREE}}
        if path == "git/commits/merge":
            return {"parents": [{"sha": "moved"}, {"sha": updater.OLD}], "tree": {"sha": "unused"}}
        raise AssertionError(path)

    with patch.object(updater, "api", side_effect=api), pytest.raises(ValueError, match="test merge parent drift"):
        updater.snapshot(updater.OLD)
