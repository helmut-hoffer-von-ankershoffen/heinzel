"""Installing exactly the announced versions, the trial start and putting everything back (ADR 0116).

Nothing here runs uv or an installed hz: the uv run is a function that changes the metadata in a temporary tool
folder, and continuity is the real fingerprint of that folder (ADR 0108).
"""

import errno
import json
import os
import sys
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import DaemonHandoverPlan, DaemonHandoverRequest, Installation
from heinzel.kernel._installation import _core_by_tag
from heinzel.modules.system import _announced as announced
from heinzel.modules.system._announced import Announcement, ToolSnapshot, environment_versions
from heinzel.modules.system._update import UpdateService

pytestmark = pytest.mark.unit
SITE = Path("lib", "python3.13", "site-packages")
REPO = "https://github.com/org/heinzel"
TAG_COMMIT = "c" * 40


# The tool folder ------------------------------------------------------------------------------------------------------


CRM_COMMIT = "a" * 40
CRM = {
    "METADATA": "Metadata-Version: 2.1\nName: heinzel-crm\nVersion: 0.3.0\nRequires-Dist: heinzel>=1\n",
    "RECORD": "heinzel_crm/__init__.py,sha256=abc,10\n",
    "direct_url.json": json.dumps(
        {"url": "https://example.test/crm", "vcs_info": {"vcs": "git", "commit_id": CRM_COMMIT}}
    ),
}
"""A plugin from git as uv installs it: its metadata, the hashes of its files and the commit it came from."""


def install(prefix: Path, versions: dict[str, str], files: dict[str, dict[str, str]] | None = None) -> None:
    """What uv leaves: a dist-info folder per distribution (with `files` for some), and hz's code with its version."""
    site = prefix / SITE
    for info in site.glob("*.dist-info"):
        for file in info.iterdir():
            file.unlink()
        info.rmdir()
    for name, version in versions.items():
        info = site / f"{name.replace('-', '_')}-{version}.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        for file, text in (files or {}).get(name, {}).items():
            (info / file).write_text(text)
    (site / "heinzel").mkdir(exist_ok=True)
    (site / "heinzel" / "__init__.py").write_text(f"hz {versions['heinzel']}")


def tool_folder(tmp_path, plugin="", core='{ name = "heinzel" }'):
    """A uv tool folder with hz 1.0.0, a plugin and a dependency, its receipt and entry point."""
    prefix = tmp_path / "tools" / "heinzel"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "hz").write_text("entrypoint")
    bin_dir = tmp_path / "installed-bin"
    bin_dir.mkdir()
    (bin_dir / "hz").symlink_to(prefix / "bin" / "hz")
    (prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{core}, {{ name = "heinzel-clock" }}{", " + plugin if plugin else ""}]'
        '\nentrypoints = [{ name = "hz", '
        "install-path = " + repr(str(bin_dir / "hz")) + " }]\n"
    )
    install(prefix, {"heinzel": "1.0.0", "heinzel-clock": "1.0.0", "urllib3": "2.0.0"})
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
    install(prefix, {"heinzel": "9.9.9", "heinzel-clock": "1.0.0", "urllib3": "2.8.0"})  # the update
    snapshot.restore()
    assert contents(prefix) == before
    assert prefix.stat().st_ino == inode  # the folder itself was never replaced
    assert sorted(p.name for p in (tmp_path / "state").iterdir()) == []


@pytest.mark.parametrize("stop_after", [1, 3])
def test_a_put_back_that_stopped_half_way_is_finished_by_recover(tmp_path, monkeypatch, stop_after):
    prefix = tool_folder(tmp_path)
    before = contents(prefix)
    snapshot = ToolSnapshot.take(prefix, tmp_path / "state")
    install(prefix, {"heinzel": "9.9.9", "heinzel-clock": "1.0.0", "urllib3": "2.0.0"})
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


@pytest.mark.parametrize("killed", ["right after the copy", "right after the install"])
def test_a_process_killed_during_its_update_is_put_back_by_the_next_start(tmp_path, killed):
    prefix = tool_folder(tmp_path)
    tool = Installation(Progress(), prefix=prefix, uv="unused")
    tool.record_startup_state(expected=tool.startup_fingerprint())
    before = contents(prefix)
    ToolSnapshot.take(prefix, tmp_path / "state")
    if killed == "right after the install":
        install(prefix, {"heinzel": "9.9.9", "heinzel-clock": "1.0.0", "urllib3": "2.0.0"})
    # The process is gone. The next `hz ui` finds the copy and nothing else (system.recover-announced):
    ToolSnapshot(prefix, tmp_path / "state" / "before-update", tmp_path / "state" / "after-update").recover()
    assert contents(prefix) == before
    assert tool.startup_state_matches()
    assert list((tmp_path / "state").iterdir()) == []


def test_a_copy_cut_short_is_never_put_back(tmp_path, monkeypatch):
    prefix = tool_folder(tmp_path)
    real = announced.shutil.copytree

    def cut(source, target, **kwargs):
        raise OSError(errno.ENOSPC, "no space left")

    monkeypatch.setattr(announced.shutil, "copytree", cut)
    with pytest.raises(OSError, match="no space"):
        ToolSnapshot.take(prefix, tmp_path / "state")
    monkeypatch.setattr(announced.shutil, "copytree", real)
    before = contents(prefix)
    ToolSnapshot(prefix, tmp_path / "state" / "before-update", tmp_path / "state" / "after-update").recover()
    assert contents(prefix) == before  # only a complete copy is the truth


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


CUTOFF = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
CORE_CUTOFF = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)


def service(tmp_path, monkeypatch, *, moves=None, crm=None, **folder):
    """An update service on the tool folder; its uv obeys the exact constraints, then moves what `moves` names.

    With `crm` (its dist-info files), the plugin heinzel-crm from git is installed too, and uv leaves it as it was
    unless `moves` names it.
    """
    prefix = tool_folder(tmp_path, **folder)
    files = {"heinzel-crm": crm} if crm else {}
    if crm:
        install(prefix, {**environment_versions(prefix), "heinzel-crm": "0.3.0"}, files)
    tool = Installation(Progress(), prefix=prefix, uv="unused")
    tool.record_startup_state(expected=tool.startup_fingerprint())
    runs, retagged = [], []

    def run(command, failure, activity, environment=None, *, additional_constraints=()):
        runs.append((command, additional_constraints))
        held = dict(c.split("==") for c in additional_constraints)
        changed = (
            {"heinzel-crm": {**CRM, "RECORD": "heinzel_crm/__init__.py,sha256=new,12\n"}}
            if moves and "heinzel-crm" in moves
            else files
        )
        install(prefix, {**held, **{k: v for k, v in (moves or {}).items() if k != "heinzel-crm"}}, changed)

    def never(*args, **kwargs):
        pytest.fail("an installed hz ran")

    monkeypatch.setattr(tool, "tool_transaction", nullcontext)  # its interpreter checks have their own tests
    monkeypatch.setattr(tool, "run", run)
    monkeypatch.setattr(tool, "hz", never)
    monkeypatch.setattr(tool, "installed_core_version", lambda: "1.0.0")
    monkeypatch.setattr(tool, "installed_versions", lambda names: environment_versions(prefix))
    monkeypatch.setattr(
        tool,
        "tool_install_command",
        lambda core, plugins, refresh=False, reinstall=True: [
            "uv",
            "tool",
            "install",
            *(["--reinstall"] if reinstall else []),
            core,
            *plugins,
        ],
    )
    monkeypatch.setattr(tool, "validate_install_constraints", lambda selected, constraints: None)
    monkeypatch.setattr(tool, "verify_installed_requirements", lambda *a, **k: {})
    monkeypatch.setattr(
        tool, "remove_transient_tool_constraint", lambda *, expected, original, added, options: expected
    )
    monkeypatch.setattr(
        tool, "name_core_by_tag", lambda *, expected, commit, tag: retagged.append((commit, tag)) or expected
    )
    monkeypatch.setattr(
        tool,
        "restore_receipt_requirements",
        lambda *, expected, original, names: retagged.append(("receipt", tuple(names))) or expected,
    )
    monkeypatch.setattr(announced, "installed_commit", lambda prefix: TAG_COMMIT)
    monkeypatch.setattr("heinzel.modules.system._update.installed_commit", lambda prefix: TAG_COMMIT)
    lifecycle = Lifecycle()
    update = UpdateService(tool, Releases(), lifecycle=lifecycle)
    update.retagged = retagged
    return update, tool, lifecycle, runs


def news(core="9.9.9", plugins=None, commit=None):
    return Announcement(core, plugins or {}, commit, exclude_newer=CUTOFF, exclude_newer_core=CORE_CUTOFF)


def test_everything_is_held_but_the_announced_versions_and_nothing_installed_runs(tmp_path, monkeypatch):
    update, _, lifecycle, runs = service(tmp_path, monkeypatch)
    started = []
    result = update.apply_announced(
        news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: started.append(v) or True
    )
    assert (result.updated, result.after, started) == (True, "9.9.9", ["9.9.9"])
    command, constraints = runs[0]
    assert command[3:5] == ["heinzel", "heinzel-clock"]  # the receipt's requirements, no versions in them
    assert set(constraints) == {"heinzel==9.9.9", "heinzel-clock==1.0.0", "urllib3==2.0.0"}  # dependencies too
    assert "--refresh" not in command
    assert "--reinstall" not in command
    assert command[5:] == [
        "--refresh-package",
        "heinzel",  # only what was announced is asked again and reinstalled
        "--reinstall-package",
        "heinzel",
        "--exclude-newer",
        "2026-11-01T09:00:00Z",  # anything new resolves only among uploads as old as the minimum age
        "--exclude-newer-package",
        "heinzel=2026-11-02T03:00:00Z",
    ]
    assert (lifecycle.requested, lifecycle.pending) == (1, False)


def test_a_dependency_that_moves_is_put_back_and_continuity_holds(tmp_path, monkeypatch):
    update, tool, lifecycle, _ = service(tmp_path, monkeypatch, moves={"urllib3": "2.8.0"})
    result = update.apply_announced(news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert (result.updated, result.restored, result.refused) == (False, True, False)
    assert "urllib3 2.8.0 instead of 2.0.0" in result.message
    assert tool.startup_state_matches()  # the standard app starts as before
    assert not lifecycle.pending  # the mutation completed: no handover is left pending


def test_an_update_that_does_not_start_is_put_back_and_refused(tmp_path, monkeypatch):
    update, tool, lifecycle, _ = service(tmp_path, monkeypatch)
    before = contents(tool.prefix)
    result = update.apply_announced(
        news(plugins={"heinzel-clock": "2.0.0"}), current="1.0.0", folder=tmp_path / "state", trial=lambda v: False
    )
    assert (result.updated, result.restored, result.refused) == (False, True, True)
    assert contents(tool.prefix) == before
    assert tool.startup_state_matches()
    assert not lifecycle.pending


GIT_CRM = '{ name = "heinzel-crm", git = "https://example.test/crm?rev=main" }'


def crm_files(prefix: Path) -> dict[str, str]:
    info = next((prefix / SITE).glob("heinzel_crm-*.dist-info"))
    return {file.name: file.read_text() for file in sorted(info.iterdir())}


@pytest.mark.parametrize("starts", [True, False])
def test_a_plugin_from_git_stays_exactly_as_installed_while_hz_and_pypi_plugins_update(tmp_path, monkeypatch, starts):
    update, tool, _, runs = service(tmp_path, monkeypatch, crm=CRM, plugin=GIT_CRM)
    before = crm_files(tool.prefix)
    result = update.apply_announced(
        news(plugins={"heinzel-clock": "2.0.0"}), current="1.0.0", folder=tmp_path / "state", trial=lambda v: starts
    )
    assert (result.updated, result.restored) == (starts, not starts)
    command, constraints = runs[0]
    assert f"heinzel-crm @ git+https://example.test/crm@{CRM_COMMIT}" in command  # its installed commit, not main
    assert "--reinstall" not in command
    assert "--refresh" not in command
    moving = [command[i + 1] for i, part in enumerate(command) if part in {"--refresh-package", "--reinstall-package"}]
    assert sorted(set(moving)) == ["heinzel", "heinzel-clock"]  # never heinzel-crm: no new build of it
    assert "heinzel-crm==0.3.0" in constraints
    assert crm_files(tool.prefix) == before  # not one of its files changed, after the update or the put-back
    if starts:
        assert ("receipt", ("heinzel-crm",)) in update.retagged  # the receipt follows main again


def test_an_update_that_would_change_a_plugin_from_git_is_put_back(tmp_path, monkeypatch):
    update, tool, lifecycle, _ = service(tmp_path, monkeypatch, crm=CRM, plugin=GIT_CRM, moves={"heinzel-crm": "0.3.0"})
    before = crm_files(tool.prefix)
    result = update.apply_announced(news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert (result.updated, result.restored) == (False, True)
    assert "heinzel-crm" in result.message
    assert crm_files(tool.prefix) == before
    assert not lifecycle.pending


def test_a_plugin_from_git_that_does_not_allow_the_new_hz_stops_the_update_in_one_line(tmp_path, monkeypatch):
    capped = {**CRM, "METADATA": CRM["METADATA"].replace("heinzel>=1", "heinzel<2,>=1")}
    update, tool, _, runs = service(tmp_path, monkeypatch, crm=capped, plugin=GIT_CRM)
    before = crm_files(tool.prefix)
    result = update.apply_announced(news(), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert (result.updated, runs) == (False, [])
    assert result.message == (
        "heinzel-crm, a plugin updated by hand, doesn't allow hz 9.9.9. Update it first; hz isn't updated by itself."
    )
    assert crm_files(tool.prefix) == before


def test_a_release_tag_installs_the_commit_it_named_and_the_receipt_names_the_tag(tmp_path, monkeypatch):
    core = f'{{ name = "heinzel", git = "{REPO}?rev=v1.0.0" }}'
    update, _, _, runs = service(tmp_path, monkeypatch, core=core)
    result = update.apply_announced(
        news(commit=TAG_COMMIT), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True
    )
    assert result.updated
    assert runs[0][0][3] == f"heinzel @ git+{REPO}@{TAG_COMMIT}"  # the commit, not the tag that could move
    assert update.retagged == [(TAG_COMMIT, "v9.9.9")]


def test_a_release_tag_with_no_recorded_commit_is_updated_by_hand(tmp_path, monkeypatch):
    core = f'{{ name = "heinzel", git = "{REPO}?rev=v1.0.0" }}'
    update, _, _, runs = service(tmp_path, monkeypatch, core=core)
    result = update.apply_announced(news(commit=None), current="1.0.0", folder=tmp_path / "state", trial=lambda v: True)
    assert (result.updated, result.refused, runs) == (False, True, [])
    assert result.message.endswith("hz isn't updated by itself.")


def test_the_receipt_names_the_tag_again_and_nothing_else_changes():
    receipt = f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev={TAG_COMMIT}" }}]\n'.encode()
    assert _core_by_tag(receipt, TAG_COMMIT, "v9.9.9").decode() == receipt.decode().replace(TAG_COMMIT, "v9.9.9")
    with pytest.raises(HeinzelError):
        _core_by_tag(receipt, "d" * 40, "v9.9.9")


def test_an_announced_plugin_that_is_not_installed_installs_nothing(tmp_path, monkeypatch):
    update, _, _, runs = service(tmp_path, monkeypatch)
    with pytest.raises(HeinzelError, match="aren't installed from PyPI: heinzel-other"):
        update.apply_announced(news(plugins={"heinzel-other": "1.0"}), folder=tmp_path / "state", trial=lambda v: True)
    assert runs == []


# The trial start ------------------------------------------------------------------------------------------------------

# The stand-in server starts as `python -I -S`: no site-packages to scan, which on a loaded macOS runner took the child
# longer to print its link than the unit test's 5 seconds (main run 41473262545). It needs only the standard library.
TRIAL_SERVER = """
import http.server, json, os, pathlib, socketserver, sys
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

class LoopbackHTTPServer(http.server.HTTPServer):
    def server_bind(self):  # HTTPServer would ask DNS for a display name, which can stall for seconds on macOS
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

server = LoopbackHTTPServer(("127.0.0.1", 0), Lifecycle)
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

    assert (
        announced.trial_start("9.9.9", timeout=30, command=[sys.executable, "-I", "-S", str(script), str(seen)]) is True
    )

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
    assert (
        announced.trial_start("1.0.0", timeout=30, command=[sys.executable, "-I", "-S", str(script), str(seen)])
        is False
    )


def test_the_receipt_follows_a_git_plugins_branch_again_and_nothing_else_changes():
    from heinzel.kernel._installation import _requirements_from

    original = (
        '[tool]\nrequirements = [{ name = "heinzel" }, '
        '{ name = "heinzel-crm", git = "https://example.test/crm?rev=main" }]\n'
    )
    pinned = original.replace("rev=main", f"rev={CRM_COMMIT}").encode()
    assert _requirements_from(pinned, original.encode(), ["heinzel-crm"]).decode() == original
