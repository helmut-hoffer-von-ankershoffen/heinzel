import json
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import CorePin, EdgeRelease, Installation, ReleaseSource, Settings
from heinzel.modules.system._github import asset_host
from heinzel.modules.system._service import _edge_release, _published_version
from heinzel.modules.system._update import UpdateService, blocking_plugins

pytestmark = pytest.mark.unit

REPO = "https://github.com/example-org/heinzel"
PLUGIN = "heinzel-acme @ git+https://github.com/example-org/heinzel-acme@v1.0.0"
PYTHON = ""


@pytest.fixture(autouse=True)
def isolated_python(tmp_path, monkeypatch):
    base = tmp_path / "base-python"
    assert base.parent.resolve() == tmp_path.resolve()
    assert not base.exists()
    assert not base.is_symlink()
    base.write_text("#!/bin/sh\nprintf 'hz 0.2.0\\n'\n")
    base.chmod(0o700)
    monkeypatch.setitem(globals(), "PYTHON", str(base))


BLOCKED = (
    "error: No solution found when resolving dependencies\n"
    "  cause: Because heinzel-acme==1.0.0 depends on heinzel<0.3 and you require heinzel==0.4.0, we can conclude "
    "that your requirements are unsatisfiable."
)


class FakeReleases(ReleaseSource):
    """A package source whose highest release is `highest` (raising it if it is an exception)."""

    def __init__(self, highest: object = "0.4.0") -> None:
        self.highest = highest
        self.asked: list[bool] = []

    def highest_version(self, timeout: float, *, prereleases: bool = False) -> str:
        self.asked.append(prereleases)
        if isinstance(self.highest, Exception):
            raise self.highest
        return str(self.highest)


class FakeUv:
    """uv and a fresh `hz`: records commands, "installs" the core's tag as the version, or fails as told."""

    def __init__(self, version: str) -> None:
        self.version = version
        self.next_version = None
        self.commands: list[list[str]] = []
        self.constraint_contents: list[str] = []
        self.failures: list[str] = []
        self.hz_starts = True
        self.plugins = {"heinzel-acme": "1.0.0"}  # what a fresh hz lists, by distribution
        self.available = {"heinzel-acme": "1.0.0"}  # what an install of the plugin's spec brings
        self.environments: list[dict[str, str] | None] = []

    def __call__(
        self, command: list[str], environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        recorded = list(command)
        if "--constraints" in recorded:
            from pathlib import Path

            index = recorded.index("--constraints")
            self.constraint_contents.append(Path(recorded[index + 1]).read_text())
            del recorded[index : index + 2]
        self.commands.append(recorded)
        self.environments.append(environment)
        if self.failures:
            return subprocess.CompletedProcess(command, 1, "", self.failures.pop(0))
        if command[1:3] == ["tool", "install"]:
            core = command[command.index(PYTHON) + 1]
            self.version = self.next_version or (core.rsplit("@v", 1)[-1] if "@v" in core else self.version)
            self.hz_starts = True
            plugin_specs = [command[i + 1] for i, part in enumerate(command) if part == "--with"]
            self.plugins = {
                name: version if f"{name}=={version}" in plugin_specs else self.available[name]
                for name, version in self.plugins.items()
                if any(w.startswith(name) for w in plugin_specs)
            }
        return subprocess.CompletedProcess(command, 0, "", "")

    def hz(self, *args: str) -> str:
        if not self.hz_starts:
            msg = "`hz --version` failed after the change"
            raise HeinzelError(msg)
        if args[:3] == ("system", "plugin", "list"):
            return json.dumps([{"distribution": n, "version": v} for n, v in self.plugins.items()])
        return f"hz {self.version}\nrepository {REPO}\n"


def _bind_target(target):
    prefix = target.prefix
    bin_dir = prefix.parent.parent / "entrypoints"
    bin_dir.mkdir(exist_ok=True)
    (prefix / "bin").mkdir(exist_ok=True)
    (prefix / "bin" / "hz").write_text("fixture", encoding="utf-8")
    if not (prefix / "bin" / "python").is_symlink():
        (prefix / "bin" / "python").symlink_to(PYTHON)
    shim = bin_dir / "hz"
    if not shim.exists():
        shim.symlink_to(prefix / "bin" / "hz")
    receipt = prefix / "uv-receipt.toml"
    receipt.write_text(
        receipt.read_text(encoding="utf-8") + f"entrypoints = [{{ name = 'hz', install-path = {str(shim)!r} }}]\n",
        encoding="utf-8",
    )


def installation(tmp_path, core: str, uv: FakeUv) -> Installation:
    prefix = tmp_path / "tools" / "heinzel"
    prefix.mkdir(parents=True)
    lines = [f'{{ name = "heinzel", {core} }}', f'{{ name = "heinzel-acme", git = "{REPO}-acme?rev=v1.0.0" }}']
    (prefix / "uv-receipt.toml").write_text(
        "[tool]\nrequirements = [\n" + ",\n".join(lines) + "\n]\n", encoding="utf-8"
    )
    result = Installation(Progress(), prefix=prefix, uv="uv", runner=uv)
    _bind_target(result)
    result.hz = uv.hz  # type: ignore[method-assign]
    result.installed_core_version = lambda: uv.version  # type: ignore[method-assign]
    result.rollback_identity = lambda: "b" * 64  # type: ignore[method-assign]
    result.verify_installed_requirements = lambda requirements, **kwargs: {  # type: ignore[method-assign]
        "heinzel": uv.version,
        **uv.plugins,
    }
    return result


def install_command(core: str, *plugins: str, refresh: bool = True) -> list[str]:
    command = ["uv", "tool", "install", "--reinstall", *(["--refresh"] if refresh else []), "--python", PYTHON, core]
    for plugin in plugins:
        command += ["--with", plugin]
    return command


def test_a_tag_pinned_install_moves_to_the_latest_release_keeping_plugins(tmp_path):
    uv = FakeUv("0.2.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases("0.4.0"))
    result = service.update(current="0.2.0")
    assert uv.commands == [install_command(f"heinzel @ git+{REPO}@v0.4.0", PLUGIN)]
    assert (result.before, result.after, result.latest, result.updated) == ("0.2.0", "0.4.0", "0.4.0", True)
    assert result.message.splitlines()[0] == "Updated hz 0.2.0 → 0.4.0."


def test_the_latest_release_installed_changes_nothing(tmp_path):
    uv = FakeUv("0.4.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.4.0"', uv), FakeReleases("0.4.0"))
    result = service.update(current="0.4.0")
    assert uv.commands == []
    assert (result.message, result.pinned_to, result.updated) == ("hz 0.4.0 is up to date (releases).", "v0.4.0", False)


def test_never_moves_back_to_a_lower_release(tmp_path):
    uv = FakeUv("0.5.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.5.0"', uv), FakeReleases("0.4.0"))
    assert service.update(current="0.5.0").message == "hz 0.5.0 is up to date (releases)."
    assert uv.commands == []


@pytest.mark.parametrize(("current", "prereleases"), [("0.2.0", False), ("0.5.0rc1", True)])
def test_pre_releases_count_only_when_one_is_installed(tmp_path, current, prereleases):
    releases = FakeReleases("9.0.0")
    UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v{current}"', FakeUv(current)), releases).update(
        current=current
    )
    assert releases.asked == [prereleases]


def test_an_unreachable_release_source_is_an_error_that_says_what_to_do(tmp_path):
    uv = FakeUv("0.2.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases(OSError("offline")))
    with pytest.raises(HeinzelError, match=r"\(offline\); hz 0.2.0 stays at v0.2.0") as raised:
        service.update(current="0.2.0")
    assert f"hz system update --source git+{REPO}@vX.Y.Z" in str(raised.value)
    assert uv.commands == []


HINT = "A release newer than this build exists: run hz system update --source git+{repo}@v{tag} to switch to releases."


@pytest.mark.parametrize(
    ("core", "pinned", "says"),
    [
        (f'git = "{REPO}?rev=feature"', "feature", (f"Pinned to feature; to get its newest commit run: "
         f"hz system update --source git+{REPO}@feature\n" + HINT.format(repo=REPO, tag="0.4.0"))),
        (f'git = "{REPO}?rev=1a2b3c4"', "1a2b3c4", (f"Pinned to commit 1a2b3c4. To update to the latest release "
         f"(0.4.0) run: hz system update --source git+{REPO}@v0.4.0")),
        ('directory = "/src/heinzel"', "/src/heinzel", ("Installed from /src/heinzel; to update, run hz system update "
         "--source with a newer build.\nA release newer than this build exists: run hz system update --source "
         "git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel@v0.4.0 to switch to releases.")),
    ],
)  # fmt: skip
def test_a_branch_commit_or_local_install_is_left_alone_with_the_command_that_moves_it(tmp_path, core, pinned, says):
    uv = FakeUv("0.2.0")
    result = UpdateService(installation(tmp_path, core, uv), FakeReleases("0.4.0")).update(current="0.2.0")
    assert uv.commands == []
    assert (result.pinned_to, result.updated, result.latest, result.message) == (pinned, False, "0.4.0", says)
    assert result.next[0] in says


def test_a_pinned_install_never_suggests_an_older_release(tmp_path):
    uv = FakeUv("0.5.0.dev3")
    result = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=feature"', uv), FakeReleases("0.4.0")).update(
        current="0.5.0.dev3"
    )
    assert (
        result.message
        == f"Pinned to feature; to get its newest commit run: hz system update --source git+{REPO}@feature"
    )
    assert result.next == [f"hz system update --source git+{REPO}@feature"]


def test_without_the_latest_release_a_pinned_install_suggests_no_release(tmp_path):
    uv = FakeUv("0.2.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=feature"', uv), FakeReleases(OSError("offline")))
    result = service.update(current="0.2.0")
    assert result.latest is None
    assert "release" not in result.message
    assert result.next == [f"hz system update --source git+{REPO}@feature"]


def test_a_main_install_is_updated_in_place_keeping_plugins(tmp_path):
    uv = FakeUv("0.5.0.dev3")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=main"', uv), FakeReleases("0.4.0"))
    uv.next_version = "0.5.0.dev7"  # what uv finds on main
    result = service.update(current="0.5.0.dev3", channel="main")
    assert uv.commands == [install_command(f"heinzel @ git+{REPO}@main", PLUGIN)]
    assert (result.message.splitlines()[0], result.updated, result.after, result.next) == (
        "Updated hz 0.5.0.dev3 → 0.5.0.dev7.",
        True,
        "0.5.0.dev7",
        [],
    )


def test_a_main_install_on_the_newest_commit_is_up_to_date_without_a_release_hint_when_newer_than_the_release(tmp_path):
    uv = FakeUv("0.5.0.dev3")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=main"', uv), FakeReleases("0.4.0"))
    result = service.update(current="0.5.0.dev3", channel="main")
    assert (result.message, result.updated, result.next) == ("hz 0.5.0.dev3 is up to date (main).", False, [])


def test_a_main_install_hints_at_a_release_newer_than_the_installed_version(tmp_path):
    uv = FakeUv("0.4.0.dev3")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=main"', uv), FakeReleases("0.4.0"))
    result = service.update(current="0.4.0.dev3", channel="main")
    command = f"hz system update --source git+{REPO}@v0.4.0"
    assert (
        result.message == "hz 0.4.0.dev3 is up to date (main).\nA release newer than this build exists: "
        f"run {command} to switch to releases."
    )
    assert (result.next, result.updated) == ([command], False)


def test_a_main_install_with_an_unreachable_release_source_still_updates(tmp_path):
    uv = FakeUv("0.5.0.dev3")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=main"', uv), FakeReleases(OSError("offline")))
    result = service.update(current="0.5.0.dev3", channel="main")
    assert (result.message, result.latest, result.next) == ("hz 0.5.0.dev3 is up to date (main).", None, [])


@pytest.mark.parametrize(
    ("core", "requirement"),
    [(f'git = "{REPO}"', f"heinzel @ git+{REPO}"), ('specifier = ">=0.1"', "heinzel>=0.1")],
)
def test_a_default_branch_or_index_install_is_reinstalled_asking_again_with_its_plugins(tmp_path, core, requirement):
    uv = FakeUv("0.2.0")
    result = UpdateService(installation(tmp_path, core, uv), FakeReleases()).update(current="0.2.0")
    assert uv.commands == [install_command(requirement, PLUGIN)]
    assert result.message.splitlines()[0] == "hz 0.2.0 is up to date."


def test_an_upgrade_that_brings_a_newer_version_says_so(tmp_path):
    uv = FakeUv("0.2.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}"', uv), FakeReleases())
    uv.next_version = "0.3.0"  # what uv finds on the default branch
    assert service.update(current="0.2.0").message.splitlines()[0] == "Updated hz 0.2.0 → 0.3.0."


def test_source_reinstalls_from_there_keeping_plugins(tmp_path):
    uv = FakeUv("0.4.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=main"', uv), FakeReleases())
    wheel = tmp_path / "heinzel-0.4.0-py3-none-any.whl"
    wheel.touch()
    result = service.update(str(wheel), current="0.4.0")
    assert uv.commands == [install_command(f"heinzel @ {wheel.as_uri()}", PLUGIN)]
    assert (result.message.splitlines()[0], result.updated) == (f"Reinstalled hz 0.4.0 from {wheel}", True)


def test_a_plugin_that_does_not_allow_the_release_is_kept_and_hz_updates(tmp_path):
    uv = FakeUv("0.2.0")
    uv.failures = [BLOCKED]
    uv.available = {"heinzel-acme": "1.0.0"}
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases("0.4.0"))
    result = service.update(current="0.2.0")
    core = f"heinzel @ git+{REPO}@v0.4.0"
    assert uv.commands == [install_command(core, PLUGIN), install_command(core, PLUGIN)]
    assert (result.after, result.updated) == ("0.4.0", True)
    [acme] = result.plugins
    assert (acme.distribution, acme.status, acme.disabled, acme.before, acme.after) == (
        "heinzel-acme", "failed", False, "1.0.0", "1.0.0",
    )  # fmt: skip
    assert "depends on heinzel<0.3" in str(acme.reason)
    assert "stays at 1.0.0" in str(acme.reason)
    assert "Plugin heinzel-acme: failed." in result.message


def test_a_plugin_that_cannot_be_kept_either_is_taken_out_with_the_command_that_brings_it_back(tmp_path):
    uv = FakeUv("0.2.0")
    uv.failures = [BLOCKED, BLOCKED]
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases("0.4.0"))
    result = service.update(current="0.2.0")
    assert uv.commands[-1] == install_command(f"heinzel @ git+{REPO}@v0.4.0")
    assert (result.after, result.updated) == ("0.4.0", True)
    [acme] = result.plugins
    assert (acme.status, acme.disabled, acme.after) == ("failed", True, None)
    assert acme.next == [f"hz system plugin add {shlex.quote(PLUGIN)} --confirm"]


def test_an_index_plugin_is_kept_at_its_version_when_the_newer_one_blocks(tmp_path):
    uv = FakeUv("0.2.0")
    uv.plugins, uv.available = {"heinzel-acme": "1.0.0"}, {"heinzel-acme": "2.0.0"}
    uv.failures = [BLOCKED]
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    (target.prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.2.0" }},'
        ' { name = "heinzel-acme", specifier = ">=1" }]\n',
        encoding="utf-8",
    )
    _bind_target(target)
    result = UpdateService(target, FakeReleases("0.4.0")).update(current="0.2.0")
    assert uv.commands[-1][-1] == "heinzel-acme==1.0.0"
    assert (result.plugins[0].status, result.plugins[0].after) == ("failed", "1.0.0")


def test_a_plugin_follows_a_newer_version_in_the_same_run_as_hz(tmp_path):
    uv = FakeUv("0.2.0")
    uv.available = {"heinzel-acme": "1.2.0"}
    result = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases("0.4.0")).update(
        current="0.2.0"
    )
    assert len(uv.commands) == 1  # one resolver step for hz and its plugins
    assert [(p.status, p.before, p.after) for p in result.plugins] == [("upgraded", "1.0.0", "1.2.0")]
    assert "Plugin heinzel-acme: upgraded 1.0.0 → 1.2.0." in result.message


def test_plugin_revision_stays_independent_of_the_core_release(tmp_path):
    uv = FakeUv("0.2.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    (target.prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.2.0" }},'
        f' {{ name = "heinzel-clock", git = "{REPO}?subdirectory=plugins%2Fheinzel-clock&rev=v0.2.0" }}]\n',
        encoding="utf-8",
    )
    _bind_target(target)
    UpdateService(target, FakeReleases("0.4.0")).update(current="0.2.0")
    assert uv.commands[0][-1] == f"heinzel-clock @ git+{REPO}@v0.2.0#subdirectory=plugins/heinzel-clock"


def test_a_private_git_plugin_is_fetched_with_the_gh_login(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    uv = FakeUv("0.2.0")
    UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases("0.4.0")).update(
        current="0.2.0"
    )
    assert uv.environments[0]["GIT_CONFIG_VALUE_0"].endswith("auth git-credential")


def test_a_floating_plugin_is_asked_again_when_hz_is_up_to_date(tmp_path):
    uv = FakeUv("0.4.0")
    uv.available = {"heinzel-acme": "1.1.0"}
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.4.0"', uv)
    (target.prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.4.0" }},'
        f' {{ name = "heinzel-acme", git = "{REPO}-acme?rev=main" }}]\n',
        encoding="utf-8",
    )
    _bind_target(target)
    result = UpdateService(target, FakeReleases("0.4.0")).update(current="0.4.0")
    assert uv.commands == [install_command(f"heinzel @ git+{REPO}@v0.4.0", f"heinzel-acme @ git+{REPO}-acme@main")]
    assert (result.updated, result.plugins[0].status) == (True, "upgraded")


def test_a_floating_plugin_that_fails_never_touches_an_up_to_date_hz(tmp_path):
    uv = FakeUv("0.4.0")
    uv.failures = [BLOCKED, BLOCKED]
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.4.0"', uv)
    (target.prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.4.0" }},'
        ' { name = "heinzel-acme", specifier = ">=1" }]\n',
        encoding="utf-8",
    )
    _bind_target(target)
    result = UpdateService(target, FakeReleases("0.4.0")).update(current="0.4.0")
    assert (result.updated, result.after, result.plugins[0].status) == (False, "0.4.0", "failed")


def test_hz_that_cannot_be_installed_even_without_plugins_is_an_error_that_does_not_ask_to_remove_plugins(tmp_path):
    uv = FakeUv("0.2.0")
    uv.failures = [BLOCKED] * 3
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases("0.4.0"))
    with pytest.raises(HeinzelError) as raised:
        service.update(current="0.2.0")
    assert "Could not update hz 0.2.0 to v0.4.0." in str(raised.value)
    assert "hz 0.2.0 is still installed, with every plugin." in str(raised.value)
    assert "plugin remove" not in str(raised.value)
    assert BLOCKED.splitlines()[0] in str(raised.value)


def test_a_failure_that_is_no_conflict_leaves_the_plugins_alone(tmp_path):
    uv = FakeUv("0.2.0")
    uv.failures = ["fatal: could not read Username for 'https://github.com'"]
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases("0.4.0"))
    with pytest.raises(HeinzelError, match="could not read Username"):
        service.update(current="0.2.0")
    assert len(uv.commands) == 1


def test_a_branch_pinned_hz_is_not_moved_by_refreshing_its_plugins(tmp_path):
    uv = FakeUv("0.2.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=feature"', uv)
    (target.prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=feature" }},'
        ' { name = "heinzel-acme", specifier = ">=1" }]\n',
        encoding="utf-8",
    )
    _bind_target(target)
    UpdateService(target, FakeReleases("0.4.0")).update(current="0.2.0")
    assert uv.commands == []


def test_a_git_plugin_is_kept_at_the_commit_it_has(tmp_path):
    uv = FakeUv("0.2.0")
    uv.failures = [BLOCKED]
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    (target.prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.2.0" }},'
        f' {{ name = "heinzel-acme", git = "{REPO}-acme?rev=main" }}]\n',
        encoding="utf-8",
    )
    _bind_target(target)
    uv.hz = (
        lambda *args: (  # type: ignore[method-assign]
            json.dumps([{"distribution": "heinzel-acme", "version": "1.0.0", "revision": "abc1234"}])
            if args[:3] == ("system", "plugin", "list")
            else f"hz {uv.version}\n"
        )
    )
    target.hz = uv.hz  # type: ignore[method-assign]
    UpdateService(target, FakeReleases("0.4.0")).update(current="0.2.0")
    assert uv.commands[-1][-1] == f"heinzel-acme @ git+{REPO}-acme@abc1234"


def test_a_broken_environment_after_a_failure_is_restored(tmp_path):
    uv = FakeUv("0.2.0")
    uv.failures, uv.hz_starts = ["network down"], False
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases("0.4.0"))
    with pytest.raises(HeinzelError, match=r"Could not update hz 0\.2\.0 to v0\.4\.0\.\nhz 0\.2\.0 was restored"):
        service.update(current="0.2.0")
    assert uv.commands[-1] == install_command(f"heinzel @ git+{REPO}@v0.2.0", PLUGIN, refresh=False)


def test_when_restoring_fails_too_the_error_gives_the_command(tmp_path):
    uv = FakeUv("0.2.0")
    uv.failures, uv.hz_starts = ["network down", "still down"], False
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases("0.4.0"))
    with pytest.raises(HeinzelError, match="hz may not start; restore it with: UV_TOOL_DIR=") as raised:
        service.update(current="0.2.0")
    assert "UV_TOOL_BIN_DIR=" in str(raised.value)
    assert "uv tool install --reinstall" in str(raised.value)


def test_outside_a_uv_tool_environment_update_refuses(tmp_path):
    service = UpdateService(Installation(Progress(), prefix=tmp_path, uv="uv"), FakeReleases())
    with pytest.raises(HeinzelError, match="wasn't installed with `uv tool install`"):
        service.update()


def test_blocking_plugins_are_the_ones_uv_names():
    plugins = [PLUGIN, "heinzel-acme-extra @ file:///src/extra", "heinzel-other==1.0", "not a requirement !"]
    assert blocking_plugins(plugins, BLOCKED) == ["heinzel-acme"]
    assert blocking_plugins(plugins, "heinzel-other==1.0 depends on heinzel<0.3; heinzel-acme-extra too") == [
        "heinzel-acme-extra",
        "heinzel-other",
    ]


def test_a_new_hz_that_does_not_start_is_restored(tmp_path):
    uv = FakeUv("0.2.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)

    def install_breaking_hz(command, environment=None):
        result = uv(command, environment)
        uv.hz_starts = len(uv.commands) > 1  # the update breaks hz, the restore mends it
        return result

    target._runner = install_breaking_hz
    with pytest.raises(HeinzelError, match=r"uv installed v0\.4\.0, but hz doesn't start\.\nhz .* was restored"):
        UpdateService(target, FakeReleases("0.4.0")).update(current="0.2.0")
    assert uv.commands[-1] == install_command(f"heinzel @ git+{REPO}@v0.2.0", PLUGIN, refresh=False)


def test_two_blocking_plugins_are_both_named():
    uv_output = "heinzel-acme==1.0.0 and heinzel-zeta==2.0 depend on heinzel<0.3"
    assert blocking_plugins(["heinzel-zeta==2.0", PLUGIN], uv_output) == ["heinzel-zeta", "heinzel-acme"]


def test_channel_release_moves_a_main_install_to_the_newest_release_and_stays_there(tmp_path):
    uv = FakeUv("0.3.0.dev1")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=main"', uv), FakeReleases("0.4.0"))
    result = service.update(current="0.3.0.dev1")
    assert uv.commands == [install_command(f"heinzel @ git+{REPO}@v0.4.0", PLUGIN)]
    assert result.message.splitlines()[0] == "Updated hz 0.3.0.dev1 → 0.4.0."


def test_a_main_install_ahead_of_the_latest_release_stays_and_says_when_it_moves(tmp_path):
    uv = FakeUv("0.5.0.dev3")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=main"', uv), FakeReleases("0.4.0"))
    result = service.update(current="0.5.0.dev3")
    assert uv.commands == []
    assert result.message == (
        "hz 0.5.0.dev3 (main) is ahead of the latest release, 0.4.0; it moves to releases with the next release.\n"
        "To stay on main: hz system update --channel main"
    )


def test_channel_main_moves_a_release_install_to_main(tmp_path):
    uv = FakeUv("0.4.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.4.0"', uv), FakeReleases("0.4.0"))
    service.update(current="0.4.0", channel="main")
    assert uv.commands == [install_command(f"heinzel @ git+{REPO}@main", PLUGIN)]


def test_channel_release_on_a_release_install_is_the_plain_update(tmp_path):
    uv = FakeUv("0.4.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.4.0"', uv), FakeReleases("0.4.0"))
    assert service.update(current="0.4.0", channel="release").message == "hz 0.4.0 is up to date (releases)."


def test_failed_update_restores_with_scoped_target_after_receipt_and_shim_are_removed(tmp_path):
    class DestructiveFakeUv(FakeUv):
        def __call__(self, command, environment=None):
            if not self.commands:
                (target.prefix / "uv-receipt.toml").unlink()
                (target.prefix.parent.parent / "entrypoints" / "hz").unlink()
                self.hz_starts = False
            else:
                (target.prefix / "uv-receipt.toml").write_text(receipt)
                (target.prefix.parent.parent / "entrypoints" / "hz").symlink_to(target.prefix / "bin" / "hz")
            return super().__call__(command, environment)

    uv = DestructiveFakeUv("0.2.0")
    uv.failures = ["network down"]
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    receipt = (target.prefix / "uv-receipt.toml").read_text()
    service = UpdateService(target, FakeReleases("0.4.0"))
    with pytest.raises(HeinzelError, match="was restored, with every plugin"):
        service.update(current="0.2.0")
    assert len(uv.commands) == 2
    assert all(env["UV_TOOL_DIR"] == str(target.prefix.parent) for env in uv.environments)
    assert all(env["UV_TOOL_BIN_DIR"] == str(target.prefix.parent.parent / "entrypoints") for env in uv.environments)
    (target.prefix / "uv-receipt.toml").unlink()
    with pytest.raises(HeinzelError, match="Cannot verify the running uv tool"):
        target.attempt(target.tool_install_command("heinzel", []), "later unrelated operation")


@pytest.mark.parametrize("identical", [False, True])
@pytest.mark.parametrize("plugin_refresh", [False, True])
def test_failed_update_probes_only_an_identical_restored_generation(tmp_path, identical, plugin_refresh):
    class ChangedFakeUv(FakeUv):
        def __call__(self, command, environment=None):
            (target.prefix / "bin/hz").write_text("changed" if not self.commands or not identical else "fixture")
            return super().__call__(command, environment)

    current = "0.4.0" if plugin_refresh else "0.2.0"
    uv = ChangedFakeUv(current)
    uv.failures = ["network down"]
    target = installation(tmp_path, f'git = "{REPO}?rev=v{current}"', uv)
    if plugin_refresh:
        (target.prefix / "uv-receipt.toml").write_text(
            f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v{current}" }},'
            ' { name = "heinzel-acme", specifier = ">=1" }]\n'
        )
        _bind_target(target)
    probes = []

    def probe(*args):
        if uv.commands and args == ("--version",):
            probes.append(args)
        return uv.hz(*args)

    target.hz = probe  # type: ignore[method-assign]
    expected = "was restored" if identical else "restore it with"
    service = UpdateService(target, FakeReleases("0.4.0"))
    if plugin_refresh:
        assert expected in service.update(current=current).message
    else:
        with pytest.raises(HeinzelError, match=expected):
            service.update(current=current)
    assert len(uv.commands) == 2
    assert len(probes) == (1 if identical else 0)


def test_failed_plugin_refresh_restores_removed_tool_in_same_scope(tmp_path):
    class DestructiveFakeUv(FakeUv):
        def __call__(self, command, environment=None):
            if not self.commands:
                (target.prefix / "uv-receipt.toml").unlink()
                (target.prefix.parent.parent / "entrypoints" / "hz").unlink()
                self.hz_starts = False
            else:
                (target.prefix / "uv-receipt.toml").write_text(receipt)
                (target.prefix.parent.parent / "entrypoints" / "hz").symlink_to(target.prefix / "bin" / "hz")
            return super().__call__(command, environment)

    uv = DestructiveFakeUv("0.4.0")
    uv.failures = ["network down"]
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.4.0"', uv)
    (target.prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.4.0" }},'
        ' { name = "heinzel-acme", specifier = ">=1" }]\n',
        encoding="utf-8",
    )
    _bind_target(target)
    receipt = (target.prefix / "uv-receipt.toml").read_text()
    result = UpdateService(target, FakeReleases("0.4.0")).update(current="0.4.0")
    assert len(uv.commands) == 2
    assert "was restored, with every plugin" in result.message
    assert result.plugins[0].status == "failed"


@pytest.mark.parametrize("plugin_refresh", [False, True])
def test_private_git_recovery_reuses_original_helper_environment(tmp_path, monkeypatch, plugin_refresh):
    monkeypatch.setattr("shutil.which", lambda name: "/fixture/gh" if name == "gh" else None)
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_TOKEN", "fictional-token")
    uv = FakeUv("0.4.0" if plugin_refresh else "0.2.0")
    uv.failures, uv.hz_starts = ["network down"], False
    current = "0.4.0" if plugin_refresh else "0.2.0"
    target = installation(tmp_path, f'git = "{REPO}?rev=v{current}"', uv)
    if plugin_refresh:
        (target.prefix / "uv-receipt.toml").write_text(
            f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v{current}" }},'
            ' { name = "heinzel-acme", specifier = ">=1" }]\n',
            encoding="utf-8",
        )
        _bind_target(target)
    service = UpdateService(target, FakeReleases("0.4.0"))
    if plugin_refresh:
        assert "was restored" in service.update(current=current).message
    else:
        with pytest.raises(HeinzelError, match="was restored") as raised:
            service.update(current=current)
        assert "fictional-token" not in str(raised.value)
    assert len(uv.environments) == 2
    first, restore = uv.environments
    assert first == restore
    assert first["GIT_CONFIG_VALUE_0"] == "!/fixture/gh auth git-credential"
    assert "HZ_CREDENTIAL_EXAMPLE_TOKEN" not in first


def test_conflict_after_owned_shim_goes_dangling_restores_without_retrying_install(tmp_path):
    class DestructiveFakeUv(FakeUv):
        def __call__(self, command, environment=None):
            if not self.commands:
                (target.prefix / "bin" / "hz").unlink()
                self.hz_starts = False
            else:
                (target.prefix / "bin" / "hz").write_text("fixture")
            return super().__call__(command, environment)

    uv = DestructiveFakeUv("0.2.0")
    uv.failures = [BLOCKED]
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    service = UpdateService(target, FakeReleases("0.4.0"))
    with pytest.raises(HeinzelError, match="was restored, with every plugin"):
        service.update(current="0.2.0")
    assert len(uv.commands) == 2
    assert uv.commands[1] == install_command(f"heinzel @ git+{REPO}@v0.2.0", PLUGIN, refresh=False)


def test_removed_interpreter_after_failed_update_still_triggers_restore(tmp_path, monkeypatch):
    class DestructiveFakeUv(FakeUv):
        def __call__(self, command, environment=None):
            if not self.commands:
                assert executable.resolve().is_relative_to(tmp_path.resolve())
                assert not executable.samefile(shared_interpreter)
                assert not executable.samefile(shared_base_interpreter)
                executable.unlink()
            else:
                executable.symlink_to(PYTHON)
            return super().__call__(command, environment)

    uv = DestructiveFakeUv("0.2.0")
    uv.failures = ["fixture collision"]
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    executable = target.prefix / "bin" / "python"
    shared_interpreter = sys.executable
    shared_base_interpreter = sys._base_executable
    assert executable.resolve().is_relative_to(tmp_path.resolve())
    assert not executable.samefile(shared_interpreter)
    assert not executable.samefile(shared_base_interpreter)
    executable.write_text("#!/bin/sh\nprintf 'hz 0.2.0\\n'\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setattr(sys, "executable", str(executable))
    target.hz = Installation.hz.__get__(target)  # type: ignore[method-assign]
    service = UpdateService(target, FakeReleases("0.4.0"))
    with pytest.raises(HeinzelError, match="was restored, with every plugin"):
        service.update(current="0.2.0")
    assert len(uv.commands) == 2
    assert executable.is_file()
    assert uv.commands[1] == install_command(f"heinzel @ git+{REPO}@v0.2.0", PLUGIN, refresh=False)


@pytest.mark.parametrize("wrong", ["version", "plugin_source"])
def test_successful_update_rejects_wrong_metadata_before_starting_target(tmp_path, wrong):
    from heinzel.kernel._installed_metadata import inspect_requirements

    uv = FakeUv("0.2.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    site = target.prefix / "lib/python3.14/site-packages"
    site.mkdir(parents=True)
    for name, version, url, revision in [
        ("heinzel", "0.3.0" if wrong == "version" else "0.4.0", REPO, "v0.4.0"),
        ("heinzel-acme", "1.0.0", REPO + ("-other" if wrong == "plugin_source" else "-acme"), "v1.0.0"),
    ]:
        info = site / f"{name}-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        (info / "direct_url.json").write_text(
            json.dumps({"url": url, "vcs_info": {"vcs": "git", "requested_revision": revision, "commit_id": "a" * 40}})
        )
    target.verify_installed_requirements = lambda requirements, **kwargs: inspect_requirements(  # type: ignore[method-assign]
        target.prefix,
        requirements,
        kwargs.get("expected_version"),
        forbidden=kwargs.get("forbidden", ()),
        constraints=kwargs.get("constraints", ()),
    )
    calls = []

    def probe(*args):
        if uv.commands:
            calls.append(args)
        return uv.hz(*args)

    target.hz = probe  # type: ignore[method-assign]
    with pytest.raises(HeinzelError, match="metadata does not match"):
        UpdateService(target, FakeReleases("0.4.0")).update(current="0.2.0")
    assert len(uv.commands) == 1
    assert calls == []


def test_successful_fallback_refuses_a_dropped_plugin_still_installed(tmp_path):
    from heinzel.kernel._installed_metadata import inspect_requirements

    uv = FakeUv("0.2.0")
    uv.failures = [BLOCKED, BLOCKED]
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    site = target.prefix / "lib/python3.14/site-packages"
    for name, direct in [
        ("heinzel", {"url": REPO, "vcs_info": {"vcs": "git", "requested_revision": "v0.4.0", "commit_id": "a" * 40}}),
        ("heinzel-acme", None),
    ]:
        info = site / f"{name}-0.4.0.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.4.0\n")
        if direct is not None:
            (info / "direct_url.json").write_text(json.dumps(direct))
    target.verify_installed_requirements = lambda requirements, **kwargs: inspect_requirements(  # type: ignore[method-assign]
        target.prefix,
        requirements,
        kwargs.get("expected_version"),
        forbidden=kwargs.get("forbidden", ()),
        constraints=kwargs.get("constraints", ()),
    )
    probes = []

    def probe(*args):
        if uv.commands:
            probes.append(args)
        return uv.hz(*args)

    target.hz = probe  # type: ignore[method-assign]
    with pytest.raises(HeinzelError, match="metadata does not match"):
        UpdateService(target, FakeReleases("0.4.0")).update(current="0.2.0")
    assert len(uv.commands) == 3
    assert probes == []


def test_failed_update_with_missing_original_generation_never_probes_recovery(tmp_path, monkeypatch):
    uv = FakeUv("0.2.0")
    uv.failures = ["network down"]
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    calls = []

    def unavailable():
        msg = "generation unavailable"
        raise HeinzelError(msg)

    def probe(*args):
        if uv.commands:
            calls.append(args)
        return uv.hz(*args)

    monkeypatch.setattr(target, "startup_fingerprint", unavailable)
    target.hz = probe  # type: ignore[method-assign]
    with pytest.raises(HeinzelError, match="restore it with"):
        UpdateService(target, FakeReleases("0.4.0")).update(current="0.2.0")
    assert len(uv.commands) == 2
    assert calls == []


class FakeMutationLifecycle:
    def __init__(self, events, *, checkpoint_matched, active=True):
        self.events = events
        self.checkpoint_matched = checkpoint_matched
        self.active = active
        self.pending = False
        self.complete = False
        self.requested = False
        self.aborted = False

    def mutation(self, checkpoint, rollback_identity):
        from contextlib import contextmanager

        @contextmanager
        def guard():
            self.events.append("enter")
            self.pending = True
            assert checkpoint() is self.checkpoint_matched
            self.original_identity = rollback_identity()
            self.rollback_identity = rollback_identity
            yield self
            if self.aborted:
                return
            if not self.complete or not self.requested:
                msg = "mutation is uncertain"
                raise HeinzelError(msg)
            self.pending = False
            self.events.append("exit")

        return guard()

    def capture(self):
        from heinzel.kernel import DaemonHandoverPlan

        self.events.append("capture")
        return DaemonHandoverPlan(self.active, "darwin", "unit" if self.active else None)

    def verified_complete(self, expected):
        assert expected == "a" * 64
        self.events.append("verified")
        self.complete = True

    def abort_unchanged(self):
        if self.rollback_identity() != self.original_identity:
            msg = "installation identity changed"
            raise HeinzelError(msg)
        self.pending = False
        self.aborted = True
        self.events.append("abort")

    def request(self, plan):
        from heinzel.kernel import DaemonHandoverRequest

        self.events.append("request")
        self.requested = True
        return DaemonHandoverRequest(requested=plan.active, reason="fake manager")


def test_core_update_holds_lifecycle_from_before_uv_through_checkpoint_and_handover(tmp_path):
    events = []

    class TrackingUv(FakeUv):
        def __call__(self, command, environment=None):
            events.append("uv")
            return super().__call__(command, environment)

    uv = TrackingUv("0.2.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    target.startup_fingerprint = lambda: events.append("fingerprint") or "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: True  # type: ignore[method-assign]
    target.record_startup_state = lambda *, expected: events.append("checkpoint")  # type: ignore[method-assign]
    target.verify_installed_requirements = lambda requirements, **kwargs: (  # type: ignore[method-assign]
        events.append("metadata") or {"heinzel": "0.4.0", **uv.plugins}
    )
    lifecycle = FakeMutationLifecycle(events, checkpoint_matched=True)
    result = UpdateService(target, FakeReleases("0.4.0"), lifecycle=lifecycle).update(current="0.2.0")
    assert events.index("capture") < events.index("uv") < events.index("metadata") < events.index("checkpoint")
    assert events.index("checkpoint") < events.index("verified") < events.index("request") < events.index("exit")
    assert not lifecycle.pending
    assert "fresh service tick is pending" in result.message


def test_plugin_only_refresh_keeps_plain_sdk_unseeded_and_hands_over(tmp_path):
    events = []

    class TrackingUv(FakeUv):
        def __call__(self, command, environment=None):
            events.append("uv")
            return super().__call__(command, environment)

    uv = TrackingUv("0.4.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.4.0"', uv)
    (target.prefix / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.4.0" }},'
        ' { name = "heinzel-acme", specifier = ">=1" }]\n'
    )
    _bind_target(target)
    target.startup_fingerprint = lambda: events.append("fingerprint") or "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    target.record_startup_state = lambda *, expected: events.append("checkpoint")  # type: ignore[method-assign]
    target.verify_installed_requirements = lambda requirements, **kwargs: (  # type: ignore[method-assign]
        events.append("metadata") or {"heinzel": "0.4.0", **uv.plugins}
    )
    lifecycle = FakeMutationLifecycle(events, checkpoint_matched=False)
    UpdateService(target, FakeReleases("0.4.0"), lifecycle=lifecycle).update(current="0.4.0")
    assert events.index("capture") < events.index("uv") < events.index("metadata") < events.index("request")
    assert "checkpoint" not in events
    assert not lifecycle.pending


def test_failed_unchanged_update_clears_its_pending_token(tmp_path):
    uv = FakeUv("0.2.0")
    uv.failures = ["network down"]
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    with pytest.raises(HeinzelError, match="Could not update"):
        UpdateService(target, FakeReleases("0.4.0"), lifecycle=lifecycle).update(current="0.2.0")
    assert not lifecycle.pending
    assert lifecycle.aborted
    assert not lifecycle.requested


def test_required_lifecycle_unavailable_does_not_start_uv(tmp_path):
    uv = FakeUv("0.2.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    service = UpdateService(target, FakeReleases("0.4.0"), require_lifecycle=True)
    with pytest.raises(HeinzelError, match="lifecycle boundary is unavailable"):
        service.update(current="0.2.0")
    assert not uv.commands


def test_plugin_inventory_mismatch_leaves_mutation_pending(tmp_path):
    events = []
    uv = FakeUv("0.2.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    target.verify_installed_requirements = lambda requirements, **kwargs: {  # type: ignore[method-assign]
        "heinzel": "0.4.0",
        "heinzel-acme": "9.0.0",
    }
    lifecycle = FakeMutationLifecycle(events, checkpoint_matched=False)
    with pytest.raises(HeinzelError, match="plugin inventory does not match"):
        UpdateService(target, FakeReleases("0.4.0"), lifecycle=lifecycle).update(current="0.2.0")
    assert lifecycle.pending
    assert not lifecycle.requested


def test_builtin_update_factory_uses_registered_lifecycle(make_kernel, tmp_path):
    from heinzel.kernel import DaemonInstallationLifecycle

    events = []
    uv = FakeUv("0.2.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle(events, checkpoint_matched=False)
    kernel = make_kernel(builtins=True)
    kernel.bind(Installation, target)
    kernel.bind(ReleaseSource, FakeReleases("0.4.0"))
    kernel.bind(DaemonInstallationLifecycle, lifecycle)
    kernel.resolve(UpdateService).update(current="0.2.0")
    assert events == ["enter", "capture", "verified", "request", "exit"]
    assert not lifecycle.pending


def test_stale_selection_after_another_update_is_rejected_without_uv_or_plugin_loss(tmp_path):
    events = []
    uv = FakeUv("0.2.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]

    class OtherWriter(FakeMutationLifecycle):
        def mutation(self, checkpoint, rollback_identity):
            (target.prefix / "uv-receipt.toml").write_text(
                f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.2.0" }},'
                f' {{ name = "heinzel-acme", git = "{REPO}-acme?rev=v1.0.0" }},'
                ' { name = "heinzel-new", specifier = ">=1" }]\n'
            )
            _bind_target(target)
            return super().mutation(checkpoint, rollback_identity)

    lifecycle = OtherWriter(events, checkpoint_matched=False)
    with pytest.raises(HeinzelError, match="requirements changed"):
        UpdateService(target, FakeReleases("0.4.0"), lifecycle=lifecycle).update(current="0.2.0")
    assert not uv.commands
    assert "heinzel-new" in (target.prefix / "uv-receipt.toml").read_text()
    assert lifecycle.aborted
    assert not lifecycle.pending


def test_failed_uv_with_changed_identity_stays_pending(tmp_path):
    uv = FakeUv("0.2.0")
    uv.failures = ["network down"]
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    target.rollback_identity = lambda: ("b" if not uv.commands else "c") * 64  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    with pytest.raises(HeinzelError, match="Could not update"):
        UpdateService(target, FakeReleases("0.4.0"), lifecycle=lifecycle).update(current="0.2.0")
    assert lifecycle.pending
    assert not lifecycle.aborted


def test_stale_running_process_cannot_automatically_downgrade_newer_installed_core(tmp_path):
    from heinzel.kernel._installed_metadata import inspect_requirements

    uv = FakeUv("0.9.9")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.9.9"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.installed_core_version = Installation.installed_core_version.__get__(target)  # type: ignore[method-assign]
    target.verify_installed_requirements = lambda requirements, **kwargs: inspect_requirements(  # type: ignore[method-assign]
        target.prefix, requirements, kwargs.get("expected_version")
    )
    info = target.prefix / "lib/python3.14/site-packages/heinzel-0.9.9.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel\nVersion: 0.9.9\n")
    (info / "direct_url.json").write_text(
        json.dumps({"url": REPO, "vcs_info": {"vcs": "git", "requested_revision": "v0.9.9", "commit_id": "a" * 40}})
    )
    releases = FakeReleases("0.9.8")
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    with pytest.raises(HeinzelError, match=r"running hz 0\.9\.7.*installation is hz 0\.9\.9.*reload"):
        UpdateService(target, releases, lifecycle=lifecycle).update(current="0.9.7")
    assert not releases.asked
    assert not uv.commands
    assert not lifecycle.pending


def test_same_receipt_new_generation_is_rejected_under_lifecycle_lock(tmp_path):
    uv = FakeUv("0.2.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    generation = ["a" * 64]
    target.startup_fingerprint = lambda: generation[0]  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]

    class OtherWriter(FakeMutationLifecycle):
        def mutation(self, checkpoint, rollback_identity):
            generation[0] = "b" * 64  # same receipt and version, different installed bytes
            return super().mutation(checkpoint, rollback_identity)

    lifecycle = OtherWriter([], checkpoint_matched=False)
    with pytest.raises(HeinzelError, match="installation changed since this update was prepared"):
        UpdateService(target, FakeReleases("0.4.0"), lifecycle=lifecycle).update(current="0.2.0")
    assert not uv.commands
    assert lifecycle.aborted
    assert not lifecycle.pending


def test_explicit_source_can_intentionally_move_to_older_release(tmp_path):
    uv = FakeUv("0.9.9")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.9.9"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    result = UpdateService(target, FakeReleases("0.9.8"), lifecycle=lifecycle).update(
        source=f"git+{REPO}@v0.9.8", current="0.9.7"
    )
    assert result.before == "0.9.7"
    assert result.after == "0.9.8"
    assert "this process is running hz 0.9.7" in result.message
    assert uv.commands


def test_current_installed_release_is_not_moved_back_to_older_latest(tmp_path):
    uv = FakeUv("0.9.9")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.9.9"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    result = UpdateService(target, FakeReleases("0.9.8"), lifecycle=lifecycle).update(current="0.9.9")
    assert not result.updated
    assert result.before == "0.9.9"
    assert result.after == "0.9.9"
    assert not uv.commands


def test_index_floor_preserves_original_requirement_and_constraints_through_plugin_fallback(tmp_path):
    uv = FakeUv("0.9.9")
    uv.failures = [BLOCKED]
    target = installation(tmp_path, 'specifier = ">=0.1,<2", extras = ["web"]', uv)
    receipt = target.prefix / "uv-receipt.toml"
    receipt.write_text(receipt.read_text() + 'constraints = [{ name = "other", specifier = "<3" }]\n')
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    UpdateService(target, FakeReleases("1.0.0"), lifecycle=lifecycle).update(current="0.9.9")
    assert len(uv.commands) == 2
    assert all("heinzel[web]<2,>=0.1" in command for command in uv.commands)
    assert uv.constraint_contents == ["other<3\nheinzel>=0.9.9\n"] * 2
    assert not lifecycle.pending


def test_update_validates_both_plugin_alternatives_without_treating_them_as_one_selection(tmp_path, monkeypatch):
    uv = FakeUv("0.9.9")
    target = installation(tmp_path, 'specifier = ">=0.1,<2"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    validate = target.validate_install_constraints
    checked = []

    def capture(requirements, constraints):
        checked.append((requirements, constraints))
        validate(requirements, constraints)

    monkeypatch.setattr(target, "validate_install_constraints", capture)
    UpdateService(target, FakeReleases("1.0.0"), lifecycle=lifecycle).update(current="0.9.9")
    assert len(checked) == 3
    assert all(len(requirements) == 1 and constraints == ("heinzel>=0.9.9",) for requirements, constraints in checked)
    assert checked[0][0][0] == "heinzel<2,>=0.1"
    assert all("heinzel-acme @ git+" in entry[0][0] for entry in checked[1:])
    assert len(uv.commands) == 1
    assert not lifecycle.pending


def test_incompatible_keep_alternative_refuses_before_uv(tmp_path):
    uv = FakeUv("0.9.9")
    target = installation(tmp_path, 'specifier = ">=0.1,<2"', uv)
    receipt = target.prefix / "uv-receipt.toml"
    receipt.write_text(
        receipt.read_text().replace(
            f'{{ name = "heinzel-acme", git = "{REPO}-acme?rev=v1.0.0" }}',
            '{ name = "heinzel-acme", specifier = ">=1.5" }',
        )
        + 'constraints = [{name="heinzel-acme", specifier=">=1.5"}]\n'
    )
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    with pytest.raises(HeinzelError, match="conflict with existing constraints"):
        UpdateService(target, FakeReleases("1.0.0"), lifecycle=lifecycle).update(current="0.9.9")
    assert not uv.commands
    assert lifecycle.aborted
    assert not lifecycle.pending


@pytest.mark.parametrize("extra", ['{ name = "HEINZEL_ACME", specifier = "==1.0.0" }', '{ name = "heinzel" }'])
def test_duplicate_selected_plugin_or_core_refuses_before_uv_and_aborts_unchanged(tmp_path, extra):
    uv = FakeUv("0.9.9")
    target = installation(tmp_path, 'specifier = ">=0.1,<2"', uv)
    receipt = target.prefix / "uv-receipt.toml"
    receipt.write_text(receipt.read_text().replace("\n]\n", f",\n{extra}\n]\n"))
    original = receipt.read_bytes()
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    with pytest.raises(HeinzelError, match="same distribution more than once"):
        UpdateService(target, FakeReleases("1.0.0"), lifecycle=lifecycle).update(current="0.9.9")
    assert not uv.commands
    assert receipt.read_bytes() == original
    assert lifecycle.aborted
    assert not lifecycle.pending


def test_floating_git_downgrade_is_refused_before_starting_changed_runtime(tmp_path):
    uv = FakeUv("0.9.9")
    uv.next_version = "0.9.8"
    target = installation(tmp_path, f'git = "{REPO}"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    post_mutation_calls = []

    def runtime(*args):
        if uv.commands:
            post_mutation_calls.append(args)
        return uv.hz(*args)

    target.hz = runtime  # type: ignore[method-assign]
    with pytest.raises(HeinzelError, match="older than the installed core"):
        UpdateService(target, FakeReleases("1.0.0"), lifecycle=lifecycle).update(current="0.9.9")
    assert uv.commands
    assert post_mutation_calls == []
    assert lifecycle.pending
    assert not lifecycle.requested


def test_explicit_older_index_source_has_no_automatic_floor(tmp_path):
    uv = FakeUv("0.9.9")
    uv.next_version = "0.9.8"
    target = installation(tmp_path, 'specifier = ">=0.1"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    result = UpdateService(target, FakeReleases("1.0.0"), lifecycle=lifecycle).update(
        source="heinzel==0.9.8", current="0.9.9"
    )
    assert result.after == "0.9.8"
    assert not uv.constraint_contents
    assert not lifecycle.pending


def test_index_resolver_downgrade_is_rejected_before_any_new_runtime_code(tmp_path):
    uv = FakeUv("0.9.9")
    uv.next_version = "0.9.8"  # adversarial fake ignores the resolver constraint
    target = installation(tmp_path, 'specifier = ">=0.1"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    post_mutation_calls = []

    def runtime(*args):
        if uv.commands:
            post_mutation_calls.append(args)
        return uv.hz(*args)

    target.hz = runtime  # type: ignore[method-assign]
    with pytest.raises(HeinzelError, match="older than the installed core"):
        UpdateService(target, FakeReleases("1.0.0"), lifecycle=lifecycle).update(current="0.9.9")
    assert uv.constraint_contents == ["heinzel>=0.9.9\n"]
    assert not post_mutation_calls
    assert lifecycle.pending
    assert not lifecycle.requested


def test_exact_index_pin_keeps_its_requirement_without_added_floor(tmp_path):
    uv = FakeUv("0.9.9")
    target = installation(tmp_path, 'specifier = "==0.9.9"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    UpdateService(target, FakeReleases("1.0.0"), lifecycle=lifecycle).update(current="0.9.9")
    assert "heinzel==0.9.9" in uv.commands[0]
    assert not uv.constraint_contents


def test_unsupported_prior_index_constraint_fails_before_uv_and_clears_unchanged_token(tmp_path):
    uv = FakeUv("0.9.9")
    target = installation(tmp_path, 'specifier = ">=0.1"', uv)
    receipt = target.prefix / "uv-receipt.toml"
    receipt.write_text(receipt.read_text() + 'constraints = [{ name = "other", mystery = "unknown" }]\n')
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    with pytest.raises(HeinzelError, match="constraints cannot be safely preserved"):
        UpdateService(target, FakeReleases("1.0.0"), lifecycle=lifecycle).update(current="0.9.9")
    assert not uv.commands
    assert lifecycle.aborted
    assert not lifecycle.pending


def test_explicit_source_preserves_existing_user_constraints_without_adding_floor(tmp_path):
    uv = FakeUv("0.9.9")
    uv.next_version = "0.9.8"
    target = installation(tmp_path, 'specifier = ">=0.1"', uv)
    receipt = target.prefix / "uv-receipt.toml"
    receipt.write_text(receipt.read_text() + 'constraints = [{ name = "other", specifier = "<3" }]\n')
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    result = UpdateService(target, FakeReleases("1.0.0"), lifecycle=lifecycle).update(
        source="heinzel==0.9.8", current="0.9.9"
    )
    assert result.after == "0.9.8"
    assert uv.constraint_contents == ["other<3\n"]


def test_explicit_source_conflicting_with_prior_floor_reports_manual_choice(tmp_path):
    uv = FakeUv("0.9.9")
    uv.failures = [BLOCKED, BLOCKED, BLOCKED]
    target = installation(tmp_path, 'specifier = ">=0.1"', uv)
    receipt = target.prefix / "uv-receipt.toml"
    receipt.write_text(receipt.read_text() + 'constraints = [{ name = "heinzel", specifier = ">=0.9.9" }]\n')
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    target.startup_state_matches = lambda: False  # type: ignore[method-assign]
    lifecycle = FakeMutationLifecycle([], checkpoint_matched=False)
    with pytest.raises(HeinzelError, match=r"Inspect existing constraints.*explicit uv command"):
        UpdateService(target, FakeReleases("1.0.0"), lifecycle=lifecycle).update(
            source="heinzel==0.9.8", current="0.9.9"
        )
    assert not uv.commands
    assert not uv.constraint_contents
    assert not lifecycle.pending


def test_initial_version_rejects_generation_change_during_inspection(tmp_path):
    uv = FakeUv("0.9.7")
    target = installation(tmp_path, 'specifier = ">=0.9.0"', uv)
    target.installed_core_version = Installation.installed_core_version.__get__(target)  # type: ignore[method-assign]
    info = target.prefix / "lib/python3.14/site-packages/heinzel-0.9.7.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel\nVersion: 0.9.7\n")
    (info / "direct_url.json").write_text(json.dumps({"url": "file:///tmp/heinzel.whl", "archive_info": {}}))
    fingerprints = iter(["a" * 64, "b" * 64])
    target.startup_fingerprint = lambda: next(fingerprints)  # type: ignore[method-assign]
    with pytest.raises(HeinzelError, match="changed during version inspection"):
        target.installed_core_version()
    assert not uv.commands


@pytest.mark.parametrize("wrong", ["none", "source", "hash"])
def test_index_update_uses_pre_uv_direct_constraint_before_runtime(tmp_path, wrong):
    from heinzel.kernel._installed_metadata import inspect_requirements

    uv = FakeUv("0.9.6")
    uv.next_version = "0.9.7"
    target = installation(tmp_path, 'specifier = ">=0.9"', uv)
    target.startup_fingerprint = lambda: "a" * 64  # type: ignore[method-assign]
    receipt = target.prefix / "uv-receipt.toml"
    wheel = (tmp_path / "heinzel-0.9.7-py3-none-any.whl").as_uri()
    digest = "a" * 64
    receipt.write_text(receipt.read_text() + f'constraints = [{{name="heinzel", url="{wheel}#sha256={digest}"}}]\n')
    site = target.prefix / "lib/python3.14/site-packages"
    for name, version, direct in [
        (
            "heinzel",
            "0.9.7",
            {
                "url": wheel + (".different" if wrong == "source" else ""),
                "archive_info": {"hashes": {"sha256": "b" * 64 if wrong == "hash" else digest}},
            },
        ),
        (
            "heinzel-acme",
            "1.0.0",
            {"url": REPO + "-acme", "vcs_info": {"vcs": "git", "requested_revision": "v1.0.0", "commit_id": "a" * 40}},
        ),
    ]:
        info = site / f"{name}-{version}.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        (info / "direct_url.json").write_text(json.dumps(direct))
    target.verify_installed_requirements = lambda requirements, **kwargs: inspect_requirements(  # type: ignore[method-assign]
        target.prefix,
        requirements,
        kwargs.get("expected_version"),
        forbidden=kwargs.get("forbidden", ()),
        constraints=kwargs.get("constraints", ()),
    )
    probes = []

    def runtime(*args):
        if uv.commands:
            probes.append(args)
        return uv.hz(*args)

    target.hz = runtime  # type: ignore[method-assign]
    if wrong == "none":
        original_receipt = receipt.read_text()

        def changed_receipt_after_uv(command, environment):
            result = uv(command, environment)
            receipt.write_text(original_receipt.replace(wheel, wheel + ".different"))
            return result

        target._runner = changed_receipt_after_uv
        result = UpdateService(target, FakeReleases("1.0.0")).update(current="0.9.6")
        assert result.after == "0.9.7"
        assert probes
    else:
        with pytest.raises(HeinzelError, match="metadata does not match"):
            UpdateService(target, FakeReleases("1.0.0")).update(current="0.9.6")
        assert probes == []
    assert len(uv.commands) == 1
    assert wheel in uv.constraint_contents[0]


def test_conflicting_direct_source_is_refused_before_uv(tmp_path):
    uv = FakeUv("0.9.6")
    target = installation(tmp_path, 'specifier = ">=0.9"', uv)
    receipt = target.prefix / "uv-receipt.toml"
    wheel = tmp_path / "heinzel-0.9.7-py3-none-any.whl"
    other = tmp_path / "heinzel-0.9.8-py3-none-any.whl"
    other.touch()
    receipt.write_text(receipt.read_text() + f'constraints = [{{name="heinzel", url="{wheel.as_uri()}"}}]\n')
    with pytest.raises(HeinzelError, match="conflict with existing constraints"):
        UpdateService(target, FakeReleases("1.0.0")).update(source=str(other), current="0.9.6")
    assert not uv.commands


EDGE_WHEEL = (
    "https://github.com/helmut-hoffer-von-ankershoffen/heinzel/releases/download/"
    "edge-v0.5.0.dev7/heinzel-0.5.0.dev7-py3-none-any.whl"
)


class FakeChannels(FakeReleases):
    """Stable and edge as separate answers, recording whether edge was ever asked."""

    def __init__(self, highest="0.4.0", edge="0.5.0.dev7"):
        super().__init__(highest)
        self.edge = edge
        self.edge_asked = 0

    def edge_release(self, timeout):
        self.edge_asked += 1
        return EdgeRelease(self.edge, EDGE_WHEEL, "a" * 64)


def _edge_pin():
    return CorePin("url", f"heinzel @ {EDGE_WHEEL}", EDGE_WHEEL)


def _channels(pin, releases):
    """A service seeing `pin`, whose install step is recorded instead of run."""
    service = object.__new__(UpdateService)
    service._installation = SimpleNamespace(
        core_pin=lambda: pin, requirements=lambda: [pin.requirement], plugin_pins=list
    )
    service._releases = releases
    service._automatic_generation = lambda _current: None
    service._with_plugins = lambda result, *_a: result
    service.installs = []

    def record(core, *_args, **kwargs):
        service.installs.append((core, kwargs.get("channel")))
        return "installed"

    service._install = record
    return service


@pytest.mark.parametrize(
    "pin",
    [CorePin("index", "heinzel"), CorePin("release", f"heinzel @ git+{REPO}@v0.3.0", "v0.3.0", git=REPO)],
)
@pytest.mark.parametrize("channel", ["stable", "release"])
def test_the_default_channel_never_asks_for_or_installs_edge(pin, channel):
    releases = FakeChannels()
    service = _channels(pin, releases)
    service.update(current="0.3.0", channel=channel)
    assert releases.edge_asked == 0
    assert all("edge" not in core for core, _ in service.installs)


def test_stable_to_edge_installs_the_digest_bound_wheel():
    service = _channels(CorePin("index", "heinzel"), FakeChannels())
    assert service.update(current="0.4.0", channel="edge") == "installed"
    assert service.installs == [(f"heinzel @ {EDGE_WHEEL}#sha256={'a' * 64}", "edge")]


def test_edge_never_moves_back_to_an_older_edge():
    service = _channels(_edge_pin(), FakeChannels(edge="0.5.0.dev7"))
    result = service.update(current="0.5.0.dev9", channel="edge")
    assert service.installs == []
    assert result.updated is False


def test_edge_to_stable_stays_until_stable_catches_up():
    releases = FakeChannels(highest="0.4.0")
    service = _channels(_edge_pin(), releases)
    result = service.update(current="0.5.0.dev7", channel="stable")
    assert service.installs == []
    assert result.after == "0.5.0.dev7"
    assert "stays until stable catches up" in result.message
    assert "Nothing is downgraded" in result.message
    assert releases.asked == [False]


@pytest.mark.parametrize("stable", ["0.5.0", "0.5.1"])
def test_edge_to_stable_moves_once_stable_is_as_new(stable):
    service = _channels(_edge_pin(), FakeChannels(highest=stable))
    assert service.update(current="0.5.0.dev7", channel="stable") == "installed"
    assert service.installs == [(f"heinzel>={stable}", "stable")]


def test_someone_elses_wheel_stays_pinned_on_edge():
    other = CorePin("url", "heinzel @ https://example.org/heinzel.whl", "https://example.org/heinzel.whl")
    releases = FakeChannels()
    service = _channels(other, releases)
    service._pinned = lambda *_a: "pinned"
    assert service.update(current="0.4.0", channel="edge") == "pinned"
    assert releases.edge_asked == 0


@pytest.mark.parametrize(
    ("selected", "target", "current", "says"),
    [
        ("stable", "edge", "0.4.0", "moves hz 0.4.0 to edge 0.5.0.dev7"),
        ("edge", "stable", "0.5.0.dev7", "stays until stable catches up"),
        ("edge", "stable", "0.4.0", "moves hz 0.4.0 to stable 0.4.0"),
        ("stable", "stable", "0.4.0", "Nothing changes"),
        ("release", "stable", "0.4.0", "Nothing changes"),
    ],
)
def test_the_statement_says_what_switching_changes_first(selected, target, current, says):
    statement = _channels(CorePin("index", "heinzel"), FakeChannels()).channel_statement(selected, target, current)
    assert says in statement
    if selected not in {target, "release"}:
        assert "Nothing is installed until you run hz system update" in statement
        assert "settings, plugins and data stay" in statement


def test_the_old_release_setting_still_means_stable():
    assert Settings(update_channel="release").update_channel == "stable"
    assert Settings().update_channel == "stable"


@pytest.mark.parametrize(
    "release",
    [
        {"tag_name": "edge-v0.9.10.dev7", "prerelease": True},
        {"tag_name": "edge-channel", "prerelease": True},
        {"tag_name": "v0.9.10", "prerelease": True},  # held for acceptance
    ],
)
@pytest.mark.parametrize("prereleases", [False, True])
def test_edge_and_held_releases_are_never_a_stable_version(release, prereleases):
    assert _published_version({"draft": False, **release}, prereleases=prereleases) is None


@pytest.mark.parametrize(
    "pointer",
    [
        [],
        {"version": "0.5.0", "wheel": EDGE_WHEEL, "sha256": "a" * 64},
        {
            "version": "0.5.0.dev7",
            "wheel": "https://example.org/heinzel-0.5.0.dev7-py3-none-any.whl",
            "sha256": "a" * 64,
        },
        {"version": "0.5.0.dev7", "wheel": EDGE_WHEEL, "sha256": "short"},
    ],
)
def test_a_wrong_edge_pointer_is_refused(pointer):
    with pytest.raises((TypeError, ValueError), match="edge pointer"):
        _edge_release(pointer)


def test_a_valid_edge_pointer_is_read():
    assert _edge_release({"version": "0.5.0.dev7", "wheel": EDGE_WHEEL, "sha256": "a" * 64}).wheel == EDGE_WHEEL


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://github.com/o/r/releases/download/t/a", True),
        ("https://release-assets.githubusercontent.com/x", True),
        ("http://github.com/o/r", False),
        ("https://github.com.example.org/x", False),
        ("https://example.org/x", False),
    ],
)
def test_the_edge_pointer_is_read_only_from_github_over_https(url, allowed):
    assert asset_host(url) is allowed
