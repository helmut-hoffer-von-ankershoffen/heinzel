import json
import shlex
import subprocess
import sys

import pytest

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation, ReleaseSource
from heinzel.modules.system._update import UpdateService, blocking_plugins

pytestmark = pytest.mark.unit

REPO = "https://github.com/example-org/heinzel"
PLUGIN = "heinzel-acme @ git+https://github.com/example-org/heinzel-acme@v1.0.0"
PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
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
        self.commands: list[list[str]] = []
        self.failures: list[str] = []
        self.hz_starts = True
        self.plugins = {"heinzel-acme": "1.0.0"}  # what a fresh hz lists, by distribution
        self.available = {"heinzel-acme": "1.0.0"}  # what an install of the plugin's spec brings
        self.environments: list[dict[str, str] | None] = []

    def __call__(
        self, command: list[str], environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        self.environments.append(environment)
        if self.failures:
            return subprocess.CompletedProcess(command, 1, "", self.failures.pop(0))
        if command[1:3] == ["tool", "install"]:
            core = command[command.index(PYTHON) + 1]
            self.version = core.rsplit("@v", 1)[-1] if "@v" in core else self.version
            self.hz_starts = True
            withs = [command[i + 1] for i, part in enumerate(command) if part == "--with"]
            self.plugins = {
                name: version if f"{name}=={version}" in withs else self.available[name]
                for name, version in self.plugins.items()
                if any(w.startswith(name) for w in withs)
            }
        return subprocess.CompletedProcess(command, 0, "", "")

    def hz(self, *args: str) -> str:
        if not self.hz_starts:
            msg = "`hz --version` failed after the change"
            raise HeinzelError(msg)
        if args[:3] == ("system", "plugin", "list"):
            return json.dumps([{"distribution": n, "version": v} for n, v in self.plugins.items()])
        return f"hz {self.version}\nrepository {REPO}\n"


def installation(tmp_path, core: str, uv: FakeUv) -> Installation:
    lines = [f'{{ name = "heinzel", {core} }}', f'{{ name = "heinzel-acme", git = "{REPO}-acme?rev=v1.0.0" }}']
    (tmp_path / "uv-receipt.toml").write_text(
        "[tool]\nrequirements = [\n" + ",\n".join(lines) + "\n]\n", encoding="utf-8"
    )
    result = Installation(Progress(), prefix=tmp_path, uv="uv", runner=uv)
    result.hz = uv.hz  # type: ignore[method-assign]
    return result


def install_command(core: str, *plugins: str, refresh: bool = True) -> list[str]:
    command = ["uv", "tool", "install", "--force", *(["--refresh"] if refresh else []), "--python", PYTHON, core]
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
    uv.version = "0.5.0.dev7"  # what uv finds on main
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
    uv.version = "0.3.0"  # what uv finds on the default branch
    assert service.update(current="0.2.0").message.splitlines()[0] == "Updated hz 0.2.0 → 0.3.0."


def test_source_reinstalls_from_there_keeping_plugins(tmp_path):
    uv = FakeUv("0.4.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=main"', uv), FakeReleases())
    result = service.update("/dist/heinzel.whl", current="0.4.0")
    assert uv.commands == [install_command("/dist/heinzel.whl", PLUGIN)]
    assert (result.message.splitlines()[0], result.updated) == ("Reinstalled hz 0.4.0 from /dist/heinzel.whl", True)


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
    (tmp_path / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.2.0" }},'
        ' { name = "heinzel-acme", specifier = ">=1" }]\n',
        encoding="utf-8",
    )
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


def test_a_plugin_of_the_cores_repository_moves_to_the_cores_new_release(tmp_path):
    uv = FakeUv("0.2.0")
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    (tmp_path / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.2.0" }},'
        f' {{ name = "heinzel-clock", git = "{REPO}?subdirectory=plugins%2Fheinzel-clock&rev=v0.2.0" }}]\n',
        encoding="utf-8",
    )
    UpdateService(target, FakeReleases("0.4.0")).update(current="0.2.0")
    assert uv.commands[0][-1] == f"heinzel-clock @ git+{REPO}@v0.4.0#subdirectory=plugins/heinzel-clock"


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
    (tmp_path / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.4.0" }},'
        f' {{ name = "heinzel-acme", git = "{REPO}-acme?rev=main" }}]\n',
        encoding="utf-8",
    )
    result = UpdateService(target, FakeReleases("0.4.0")).update(current="0.4.0")
    assert uv.commands == [install_command(f"heinzel @ git+{REPO}@v0.4.0", f"heinzel-acme @ git+{REPO}-acme@main")]
    assert (result.updated, result.plugins[0].status) == (True, "upgraded")


def test_a_floating_plugin_that_fails_never_touches_an_up_to_date_hz(tmp_path):
    uv = FakeUv("0.4.0")
    uv.failures = [BLOCKED, BLOCKED]
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.4.0"', uv)
    (tmp_path / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.4.0" }},'
        ' { name = "heinzel-acme", specifier = ">=1" }]\n',
        encoding="utf-8",
    )
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
    (tmp_path / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=feature" }},'
        ' { name = "heinzel-acme", specifier = ">=1" }]\n',
        encoding="utf-8",
    )
    UpdateService(target, FakeReleases("0.4.0")).update(current="0.2.0")
    assert uv.commands == []


def test_a_git_plugin_is_kept_at_the_commit_it_has(tmp_path):
    uv = FakeUv("0.2.0")
    uv.failures = [BLOCKED]
    target = installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv)
    (tmp_path / "uv-receipt.toml").write_text(
        f'[tool]\nrequirements = [{{ name = "heinzel", git = "{REPO}?rev=v0.2.0" }},'
        f' {{ name = "heinzel-acme", git = "{REPO}-acme?rev=main" }}]\n',
        encoding="utf-8",
    )
    uv.hz = (
        lambda *args: (  # type: ignore[method-assign]
            json.dumps([{"distribution": "heinzel-acme", "version": "1.0.0", "revision": "abc1234"}])
            if args[:3] == ("system", "plugin", "list")
            else "hz 0.2.0\n"
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
    with pytest.raises(HeinzelError, match="hz may not start; restore it with: uv tool install --force"):
        service.update(current="0.2.0")


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
