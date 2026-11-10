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
            "GITHUB_ACTOR": "helmut-hoffer-von-ankershoffen",
            "GITHUB_SHA": "reviewed",
            "REVIEWED_HELPER_SHA": "reviewed",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(updater, "api", api):
            if drift:
                with pytest.raises(ValueError, match="drift"):
                    updater.main()
            else:
                updater.main()
        return state["writes"]

    def test_fast_forward(self):
        assert self.exercise() == [("--method", "PATCH", "-f", f"sha={updater.NEW}", "-F", "force=false")]

    def test_drift_never_writes(self):
        assert self.exercise(drift=True) == []
