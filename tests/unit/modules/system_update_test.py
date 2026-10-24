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

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if self.failures:
            return subprocess.CompletedProcess(command, 1, "", self.failures.pop(0))
        if command[1:3] == ["tool", "install"]:
            self.version = command[6].rsplit("@v", 1)[-1] if "@v" in command[6] else self.version
            self.hz_starts = True
        return subprocess.CompletedProcess(command, 0, "", "")

    def hz(self, *args: str) -> str:
        if not self.hz_starts:
            msg = "`hz --version` failed after the change"
            raise HeinzelError(msg)
        return f"hz {self.version}\nrepository {REPO}\n"


def installation(tmp_path, core: str, uv: FakeUv) -> Installation:
    lines = [f'{{ name = "heinzel", {core} }}', f'{{ name = "heinzel-acme", git = "{REPO}-acme?rev=v1.0.0" }}']
    (tmp_path / "uv-receipt.toml").write_text(
        "[tool]\nrequirements = [\n" + ",\n".join(lines) + "\n]\n", encoding="utf-8"
    )
    result = Installation(Progress(), prefix=tmp_path, uv="uv", runner=uv)
    result.hz = uv.hz  # type: ignore[method-assign]
    return result


def install_command(core: str, *plugins: str) -> list[str]:
    command = ["uv", "tool", "install", "--force", "--python", PYTHON, core]
    for plugin in plugins:
        command += ["--with", plugin]
    return command


def test_a_tag_pinned_install_moves_to_the_latest_release_keeping_plugins(tmp_path):
    uv = FakeUv("0.2.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases("0.4.0"))
    result = service.update(current="0.2.0")
    assert uv.commands == [install_command(f"heinzel @ git+{REPO}@v0.4.0", PLUGIN)]
    assert (result.before, result.after, result.latest, result.updated) == ("0.2.0", "0.4.0", "0.4.0", True)
    assert result.message == "Updated hz 0.2.0 → 0.4.0"


def test_the_latest_release_installed_changes_nothing(tmp_path):
    uv = FakeUv("0.4.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.4.0"', uv), FakeReleases("0.4.0"))
    result = service.update(current="0.4.0")
    assert uv.commands == []
    assert (result.message, result.pinned_to, result.updated) == ("hz 0.4.0 is the latest release", "v0.4.0", False)


def test_never_moves_back_to_a_lower_release(tmp_path):
    uv = FakeUv("0.5.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.5.0"', uv), FakeReleases("0.4.0"))
    assert service.update(current="0.5.0").message == "hz 0.5.0 is newer than the latest release, 0.4.0"
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


@pytest.mark.parametrize(
    ("core", "pinned", "says"),
    [
        (f'git = "{REPO}?rev=main"', "main", (f"Pinned to main; to update to the latest release (0.4.0) run: "
         f"hz system update --source git+{REPO}@v0.4.0\nTo get the newest commit on main instead: "
         f"hz system update --source git+{REPO}@main")),
        (f'git = "{REPO}?rev=1a2b3c4"', "1a2b3c4", (f"Pinned to commit 1a2b3c4; to update to the latest release "
         f"(0.4.0) run: hz system update --source git+{REPO}@v0.4.0")),
        ('directory = "/src/heinzel"', "/src/heinzel", ("Installed from /src/heinzel; to update, run hz system update "
         "--source with a newer build, or move to the latest release (0.4.0): hz system update --source "
         "git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel@v0.4.0")),
    ],
)  # fmt: skip
def test_a_branch_commit_or_local_install_is_left_alone_with_the_command_that_moves_it(tmp_path, core, pinned, says):
    uv = FakeUv("0.2.0")
    result = UpdateService(installation(tmp_path, core, uv), FakeReleases("0.4.0")).update(current="0.2.0")
    assert uv.commands == []
    assert (result.pinned_to, result.updated, result.latest, result.message) == (pinned, False, "0.4.0", says)
    assert result.next[0] in says


def test_without_the_latest_release_the_pinned_message_has_a_placeholder(tmp_path):
    uv = FakeUv("0.2.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=main"', uv), FakeReleases(OSError("offline")))
    result = service.update(current="0.2.0")
    assert result.latest is None
    assert f"git+{REPO}@vX.Y.Z" in result.message
    assert result.next == [f"hz system update --source git+{REPO}@main"]


@pytest.mark.parametrize(
    ("core", "after", "message"),
    [
        (f'git = "{REPO}"', "0.2.0", f"hz 0.2.0 is the newest from {REPO}"),
        ('specifier = ">=0.1"', "0.2.0", "hz 0.2.0 is the newest from the package index"),
    ],
)
def test_a_default_branch_or_index_install_is_upgraded_by_uv(tmp_path, core, after, message):
    uv = FakeUv(after)
    result = UpdateService(installation(tmp_path, core, uv), FakeReleases()).update(current="0.2.0")
    assert uv.commands == [["uv", "tool", "upgrade", "heinzel"]]
    assert result.message == message


def test_an_upgrade_that_brings_a_newer_version_says_so(tmp_path):
    uv = FakeUv("0.2.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}"', uv), FakeReleases())
    uv.version = "0.3.0"  # what uv finds on the default branch
    assert service.update(current="0.2.0").message == "Updated hz 0.2.0 → 0.3.0"


def test_source_reinstalls_from_there_keeping_plugins(tmp_path):
    uv = FakeUv("0.4.0")
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=main"', uv), FakeReleases())
    result = service.update("/dist/heinzel.whl", current="0.4.0")
    assert uv.commands == [install_command("/dist/heinzel.whl", PLUGIN)]
    assert (result.message, result.updated) == ("Reinstalled hz 0.4.0 from /dist/heinzel.whl", True)


def test_a_plugin_that_does_not_allow_the_release_is_named_and_hz_stays(tmp_path):
    uv = FakeUv("0.2.0")
    uv.failures = [BLOCKED]
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases("0.4.0"))
    with pytest.raises(HeinzelError) as raised:
        service.update(current="0.2.0")
    lines = str(raised.value).splitlines()
    assert lines[:3] == [
        "Could not update hz 0.2.0 to v0.4.0: the plugin heinzel-acme does not allow it.",
        "hz 0.2.0 is still installed, with every plugin.",
        (
            "Remove it (hz system plugin remove heinzel-acme), run hz system update, then add a release of it "
            "that allows the new hz (hz system plugin add <spec> --confirm)."
        ),
    ]
    assert BLOCKED.splitlines()[0] in lines
    assert len(uv.commands) == 1  # uv left the previous environment in place: nothing to restore


def test_a_broken_environment_after_a_failure_is_restored(tmp_path):
    uv = FakeUv("0.2.0")
    uv.failures, uv.hz_starts = ["network down"], False
    service = UpdateService(installation(tmp_path, f'git = "{REPO}?rev=v0.2.0"', uv), FakeReleases("0.4.0"))
    with pytest.raises(HeinzelError, match=r"Could not update hz 0\.2\.0 to v0\.4\.0\.\nhz 0\.2\.0 was restored"):
        service.update(current="0.2.0")
    assert uv.commands[-1] == install_command(f"heinzel @ git+{REPO}@v0.2.0", PLUGIN)


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

    def install_breaking_hz(command):
        result = uv(command)
        uv.hz_starts = len(uv.commands) > 1  # the update breaks hz, the restore mends it
        return result

    target._runner = install_breaking_hz
    with pytest.raises(HeinzelError, match=r"uv installed v0\.4\.0, but hz doesn't start\.\nhz .* was restored"):
        UpdateService(target, FakeReleases("0.4.0")).update(current="0.2.0")
    assert uv.commands[-1] == install_command(f"heinzel @ git+{REPO}@v0.2.0", PLUGIN)


def test_two_blocking_plugins_are_both_named():
    uv_output = "heinzel-acme==1.0.0 and heinzel-zeta==2.0 depend on heinzel<0.3"
    assert blocking_plugins(["heinzel-zeta==2.0", PLUGIN], uv_output) == ["heinzel-zeta", "heinzel-acme"]
