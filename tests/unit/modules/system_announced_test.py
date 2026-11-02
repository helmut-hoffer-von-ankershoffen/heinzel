"""Installing exactly the announced versions, the trial start and putting everything back (ADR 0116).

Nothing here runs uv or an installed hz: the uv run is a function that changes files in a temporary tool folder, and
continuity is the real fingerprint of that folder (ADR 0108).
"""

import errno
import json
import os
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import DaemonHandoverPlan, DaemonHandoverRequest, Installation
from heinzel.modules.system import _announced as announced
from heinzel.modules.system._announced import ToolSnapshot
from heinzel.modules.system._update import UpdateService

pytestmark = pytest.mark.unit


# The tool folder ------------------------------------------------------------------------------------------------------


def tool_folder(tmp_path):
    """A uv tool folder with hz 1.0.0 and one plugin, its receipt and entry point, as the continuity tests build it."""
    prefix = tmp_path / "tools" / "heinzel"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "hz").write_text("entrypoint")
    bin_dir = tmp_path / "installed-bin"
    bin_dir.mkdir()
    (bin_dir / "hz").symlink_to(prefix / "bin" / "hz")
    (prefix / "uv-receipt.toml").write_text(
        '[tool]\nrequirements = [{ name = "heinzel" }, { name = "heinzel-clock" }]\nentrypoints = [{ name = "hz", '
        "install-path = " + repr(str(bin_dir / "hz")) + " }]\n"
    )
    package = prefix / "lib" / "site-packages" / "heinzel"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("hz 1.0.0")
    return prefix


def contents(folder: Path) -> dict[str, str]:
    return {
        str(path.relative_to(folder)): path.read_text() if path.is_file() and not path.is_symlink() else ""
        for path in sorted(folder.rglob("*"))
        if not path.name.startswith(".heinzel-startup")
    }


def test_putting_back_keeps_the_tool_folder_and_its_content_byte_for_byte(tmp_path):
    prefix = tool_folder(tmp_path)
    before, inode = contents(prefix), prefix.stat().st_ino
    snapshot = ToolSnapshot.take(prefix, tmp_path / "state")
    (prefix / "lib" / "site-packages" / "heinzel" / "__init__.py").write_text("hz 9.9.9")  # the update
    (prefix / "lib" / "new.txt").write_text("new")
    snapshot.restore()
    assert contents(prefix) == before
    assert prefix.stat().st_ino == inode  # the folder itself was never replaced
    assert sorted(p.name for p in (tmp_path / "state").iterdir()) == []


@pytest.mark.parametrize("stop_after", [1, 3])
def test_a_put_back_that_stopped_half_way_is_finished_by_recover(tmp_path, monkeypatch, stop_after):
    prefix = tool_folder(tmp_path)
    before = contents(prefix)
    snapshot = ToolSnapshot.take(prefix, tmp_path / "state")
    (prefix / "lib" / "site-packages" / "heinzel" / "__init__.py").write_text("hz 9.9.9")
    renamed, real = [], Path.rename

    def rename(self, target):
        if len(renamed) == stop_after:
            raise OSError(errno.EIO, "power cut")
        renamed.append(self)
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError, match="power cut"):
        snapshot.restore()
    monkeypatch.setattr(Path, "rename", real)
    snapshot.recover()  # as the next start does
    assert contents(prefix) == before


def test_the_copy_must_be_on_the_tool_folders_disk(tmp_path, monkeypatch):
    prefix = tool_folder(tmp_path)
    real = Path.stat
    monkeypatch.setattr(
        Path,
        "stat",
        lambda self, **kw: (
            os.stat_result((*real(self, **kw)[:2], 999, *real(self, **kw)[3:]))
            if self.name == "state"
            else real(self, **kw)
        ),
    )
    with pytest.raises(HeinzelError, match="another disk"):
        ToolSnapshot.take(prefix, tmp_path / "state")


# Installing exactly the announced versions ----------------------------------------------------------------------------


class Lifecycle:
    """A daemon mutation as `system.daemon` runs it: complete only once verified and handed over."""

    def __init__(self, checkpoint_matched=True):
        self.checkpoint_matched, self.pending, self.verified, self.requested = checkpoint_matched, False, None, 0

    def mutation(self, checkpoint, rollback_identity):
        @contextmanager
        def scope():
            self.pending = True
            yield self
            assert self.verified is not None
            assert self.requested == 1
            self.pending = False

        return scope()

    def capture(self):
        return DaemonHandoverPlan(active=True, platform="darwin", unit_signature="unit")

    def verified_complete(self, expected):
        self.verified = expected

    def abort_unchanged(self):
        self.pending = False

    def request(self, plan):
        self.requested += 1
        return DaemonHandoverRequest(requested=True, reason="fake manager")


class Releases:
    def latest_version(self, timeout):
        return "9.9.9"


def service(tmp_path, monkeypatch, *, installs=None):
    """An update service on the tool folder whose uv run writes `installs` (distribution to version) into it."""
    prefix = tool_folder(tmp_path)
    tool = Installation(Progress(), prefix=prefix, uv="unused")
    tool.record_startup_state(expected=tool.startup_fingerprint())
    runs = []
    versions = {"heinzel": "1.0.0", "heinzel-clock": "1.0.0"}

    def run(command, failure, activity, environment=None, *, additional_constraints=()):
        runs.append((command, additional_constraints))
        versions.update(installs if installs is not None else dict(c.split("==") for c in additional_constraints))
        (prefix / "lib" / "site-packages" / "heinzel" / "__init__.py").write_text(f"hz {versions['heinzel']}")

    def never(*args, **kwargs):
        pytest.fail("an installed hz ran")

    monkeypatch.setattr(tool, "tool_transaction", nullcontext)  # its interpreter checks have their own tests
    monkeypatch.setattr(tool, "run", run)
    monkeypatch.setattr(tool, "hz", never)
    monkeypatch.setattr(tool, "installed_core_version", lambda: "1.0.0")
    monkeypatch.setattr(tool, "installed_versions", lambda names: {n: versions[n] for n in names if n in versions})
    monkeypatch.setattr(
        tool, "tool_install_command", lambda core, plugins, refresh=False: ["uv", "tool", "install", core, *plugins]
    )
    monkeypatch.setattr(tool, "validate_install_constraints", lambda selected, constraints: None)
    monkeypatch.setattr(tool, "verify_installed_requirements", lambda *a, **k: dict(versions))
    monkeypatch.setattr(tool, "remove_transient_tool_constraint", lambda *, expected, original, added: expected)
    lifecycle = Lifecycle()
    return UpdateService(tool, Releases(), lifecycle=lifecycle), tool, lifecycle, runs


def test_exactly_the_announced_versions_go_in_as_constraints_and_nothing_installed_runs(tmp_path, monkeypatch):
    update, _, lifecycle, runs = service(tmp_path, monkeypatch)
    started = []
    result = update.apply_announced(
        "9.9.9", {}, current="1.0.0", folder=tmp_path / "state", trial=lambda v: started.append(v) or True
    )
    assert (result.updated, result.after, started) == (True, "9.9.9", ["9.9.9"])
    command, constraints = runs[0]
    assert command[3:] == ["heinzel", "heinzel-clock"]  # the receipt's requirements, no versions in them
    assert constraints == ("heinzel==9.9.9", "heinzel-clock==1.0.0")  # every plugin held where it is
    assert lifecycle.requested == 1
    assert not lifecycle.pending


def test_other_versions_than_announced_are_put_back_and_continuity_holds(tmp_path, monkeypatch):
    update, tool, lifecycle, _ = service(tmp_path, monkeypatch, installs={"heinzel": "9.9.10"})
    result = update.apply_announced("9.9.9", {}, current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert (result.updated, result.restored, result.refused) == (False, True, False)
    assert "heinzel 9.9.10 instead of 9.9.9" in result.message
    assert tool.startup_state_matches()  # the standard app starts as before
    assert not lifecycle.pending  # the mutation completed: no handover is left pending


def test_an_update_that_does_not_start_is_put_back_and_refused(tmp_path, monkeypatch):
    update, tool, lifecycle, _ = service(tmp_path, monkeypatch)
    before = contents(tool.prefix)
    result = update.apply_announced(
        "9.9.9", {"heinzel-clock": "2.0.0"}, current="1.0.0", folder=tmp_path / "state", trial=lambda v: False
    )
    assert (result.updated, result.restored, result.refused) == (False, True, True)
    assert contents(tool.prefix) == before
    assert tool.startup_state_matches()
    assert not lifecycle.pending


def test_an_announced_plugin_that_is_not_installed_installs_nothing(tmp_path, monkeypatch):
    update, _, _, runs = service(tmp_path, monkeypatch)
    with pytest.raises(HeinzelError, match="aren't installed: heinzel-other"):
        update.apply_announced("9.9.9", {"heinzel-other": "1.0"}, folder=tmp_path / "state", trial=lambda v: True)
    assert runs == []


# The trial start ------------------------------------------------------------------------------------------------------

TRIAL_SERVER = """
import http.server, json, os, pathlib, sys
for variable in ("HZ_STATE_DIR", "HZ_DATA_DIR", "HZ_CACHE_DIR", "HZ_LOG_DIR"):
    pathlib.Path(os.environ[variable], "written-by-the-trial").write_text("x")
config = pathlib.Path(os.environ["HZ_CONFIG_HOME"], "hz.toml")
config.write_text(config.read_text() + "written = true\\n")
pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(os.environ)))

class Lifecycle(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"version": "9.9.9"}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

server = http.server.HTTPServer(("127.0.0.1", 0), Lifecycle)
print(f"http://127.0.0.1:{server.server_port}/#token=t", flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
"""


def test_a_trial_start_runs_on_throwaway_folders_and_leaves_the_live_ones_untouched(tmp_path, monkeypatch):
    live = {name: tmp_path / "live" / name for name in ("config", "state", "data", "cache", "log")}
    for folder in live.values():
        folder.mkdir(parents=True)
    (live["config"] / "hz.toml").write_text("[hz]\nui_port = 8737\n")
    for name, variable in [("state", "HZ_STATE_DIR"), ("data", "HZ_DATA_DIR"), ("cache", "HZ_CACHE_DIR")]:
        monkeypatch.setenv(variable, str(live[name]))
    monkeypatch.setenv("HZ_LOG_DIR", str(live["log"]))
    monkeypatch.setenv("HZ_CONFIG_HOME", str(live["config"]))
    seen = tmp_path / "seen.json"
    script = tmp_path / "trial.py"
    script.write_text(TRIAL_SERVER)

    assert announced.trial_start("9.9.9", timeout=30, command=[sys.executable, str(script), str(seen)]) is True

    assert (live["config"] / "hz.toml").read_text() == "[hz]\nui_port = 8737\n"
    assert [p.name for f in live.values() for p in f.iterdir()] == ["hz.toml"]  # nothing written anywhere live
    env = json.loads(seen.read_text())
    assert (env["HZ_AUTO_UPDATE"], env["HZ_UPDATE_CHECK"]) == ("false", "false")
    scratch = Path(env["HZ_STATE_DIR"]).parent
    assert scratch.name.startswith("hz-trial-")
    assert not scratch.exists()  # deleted with everything the trial wrote


def test_a_trial_start_that_answers_another_version_fails(tmp_path):
    seen, script = tmp_path / "seen.json", tmp_path / "trial.py"
    script.write_text(TRIAL_SERVER)
    assert announced.trial_start("1.0.0", timeout=30, command=[sys.executable, str(script), str(seen)]) is False
