"""The isolated runner must not start pytest when its boundaries fail."""

import errno
import importlib.util
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("mcp_fixture_runner", ROOT / "scripts/mcp_isolated_fixture.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class IsolatedRunnerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "fixture").write_bytes(b"fictional")
        self.env = {
            "CONTROL_HOST": "192.0.2.2",
            "CONTROL_PORT": "9999",
            "HOME": "/test",
            "TMPDIR": str(self.root),
            "XDG_CONFIG_HOME": "/test/config",
            "XDG_CACHE_HOME": "/test/cache",
            "PLAYWRIGHT_BROWSERS_PATH": "/browser",
        }
        self.connect = socket.create_connection

    def execute(self, remote):
        def connect(address, timeout):
            if address[0] == "127.0.0.1":
                return self.connect(address, timeout=timeout)
            return remote(address, timeout)

        with (
            patch.object(runner, "ROOT", self.root),
            patch.object(
                runner, "EXPECTED", {"fixture": "a524644266885e16a3bb16166ae38bdbfb36af08fc51036f4dbb91423c7cbcc1"}
            ),
            patch.dict(os.environ, self.env, clear=True),
            patch.object(runner.importlib.metadata, "entry_points", return_value=[]),
            patch.object(runner.socket, "create_connection", side_effect=connect),
            patch.object(runner.subprocess, "run") as process,
        ):
            process.return_value.returncode = 0
            result = runner.run()
            return result, process.call_count

    def test_offline_canaries_permit_selected_test(self):
        def denied(*_):
            raise OSError(errno.ENETUNREACH, "network unreachable")

        assert self.execute(denied) == (0, 1)

    def test_unexpected_reachable_host_stops_before_pytest(self):
        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with pytest.raises(runner.IsolationError, match="unexpectedly reachable"):
            self.execute(lambda *_: Connection())

    def test_refused_connection_does_not_prove_network_isolation(self):
        def refused(*_):
            raise OSError(errno.ECONNREFUSED, "refused")

        with pytest.raises(runner.IsolationError, match="unexpected network error"):
            self.execute(refused)


if __name__ == "__main__":
    unittest.main()


def test_pinned_playwright_seccomp_and_container_boundaries():
    """Do not silently lose the sandbox or substitute a different syscall profile."""
    import hashlib
    import json

    profile = ROOT / ".github/mcp-fixture/seccomp.json"
    assert (
        hashlib.sha256(profile.read_bytes()).hexdigest()
        == "3ee57896ff2f6ae4be729dc13fdc18623f842a0f37c30c52c696e21e8c9c65e7"
    )
    data = json.loads(profile.read_bytes())
    assert data["defaultAction"] == "SCMP_ACT_ERRNO"
    assert any(
        set(rule["names"]) == {"clone", "setns", "unshare"} and rule["action"] == "SCMP_ACT_ALLOW"
        for rule in data["syscalls"]
    )
    # Docker drops rules whose included capabilities are absent. With ALL dropped,
    # chroot must still be permitted by seccomp for Chromium's user namespace.
    active = [
        rule
        for rule in data["syscalls"]
        if not rule.get("includes", {}).get("caps") and rule["action"] == "SCMP_ACT_ALLOW"
    ]
    assert any("chroot" in rule["names"] and not rule.get("args") and not rule.get("excludes") for rule in active)
    host = (ROOT / ".github/mcp-fixture/host.sh").read_text()
    assert "--security-opt seccomp=.github/mcp-fixture/seccomp.json" in host
    assert "--network none --ipc private --cap-drop ALL" in host
    assert "--security-opt no-new-privileges --user 10001:10001" in host
    assert "--no-sandbox" not in host


def test_one_shot_push_guard_rejects_unreviewed_inputs():
    """The actual pre-checkout shell guard accepts only the reviewed initial push."""
    import os
    import subprocess
    import textwrap

    workflow = (ROOT / ".github/workflows/mcp-isolated.yml").read_text()
    guard = workflow.split("      - name: Require exact reviewed first-push source\n        run: |\n", 1)[1].split(
        "      - uses:", 1
    )[0]
    guard = "set -e\n" + textwrap.dedent(guard)
    sha = "a" * 40
    valid = {
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REPOSITORY": "helmut-hoffer-von-ankershoffen/heinzel",
        "GITHUB_ACTOR": "helmut-hoffer-von-ankershoffen",
        "GITHUB_REF": "refs/heads/bootstrap/mcp-settings-fixture-4pih",
        "PUSH_CREATED": "true",
        "GITHUB_RUN_ATTEMPT": "1",
        "REVIEWED_SHA": sha,
        "GITHUB_SHA": sha,
        "WORKFLOW_SHA": sha,
    }

    def check(env):
        return subprocess.run(["bash", "-c", guard], env=os.environ | env, check=False, capture_output=True).returncode

    assert check(valid) == 0
    for key, wrong in [
        ("GITHUB_EVENT_NAME", "workflow_dispatch"),
        ("GITHUB_REPOSITORY", "other/repo"),
        ("GITHUB_ACTOR", "someone-else"),
        ("GITHUB_REF", "refs/heads/main"),
        ("PUSH_CREATED", "false"),
        ("GITHUB_RUN_ATTEMPT", "2"),
        ("REVIEWED_SHA", ""),
        ("REVIEWED_SHA", "b" * 40),
        ("REVIEWED_SHA", "not-a-sha"),
        ("WORKFLOW_SHA", "b" * 40),
        ("GITHUB_SHA", "b" * 40),
    ]:
        assert check(valid | {key: wrong}) != 0, key
    assert "github.actor == github.repository_owner" in workflow
    assert "github.run_attempt == '1'" in workflow
    assert "github.event.created == true" in workflow
    assert "vars.MCP_SETTINGS_FIXTURE_REVIEWED_SHA" in workflow


def test_helper_pins_product_and_overlays_exact_fixtures():
    """The isolated image uses one reviewed product tree and byte-exact synthetic fixture."""
    import hashlib

    fixtures = {
        ".github/mcp-fixture/mcp-settings-py.fixture": (
            "9c778734947a47334514a547c6c7b44e0fd5fe500f953183bfe45f45059989c5"
        ),
        ".github/mcp-fixture/mcp-settings.mjs": "0f0b6d04396800d0de7f40c016d63102e801f80cad87a0c3515b313b5ad77781",
    }
    for path, digest in fixtures.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
    workflow = (ROOT / ".github/workflows/mcp-isolated.yml").read_text()
    host = (ROOT / ".github/mcp-fixture/host.sh").read_text()
    sha = "ff85f84818fca13645789602fd2337af1ca27d49"
    assert f"ref: {sha}" in workflow
    assert workflow.count("persist-credentials: false") == 2
    assert f"source_sha={sha}" in host
    assert 'git -C mcp-source archive "$source_sha"' in host
    assert 'mcp-settings-py.fixture "$context/tests/e2e/mcp_settings_test.py"' in host
    assert 'mcp-settings.mjs "$context/web/e2e/mcp-settings.mjs"' in host
