"""An update with a plugin that depends on another plugin, against real uv, ending with every head on the new code.

Stand-in wheels, offline and in temporary uv folders as in `auto_update_real_uv_test.py`: hz, the plugin
heinzel-clock and the plugin heinzel-alarm, which requires heinzel-clock. The daemon and the persistent web server are
the owned-restart seams `system.update` uses (`DaemonInstallationLifecycle`, `WebHeadRefresh`), here backed by fakes
that report the version the code installed at that moment declares, as a live process would after its restart. The
update is complete only if each head reports the new version, and a dependency that can't move puts everything back.
"""

import shutil
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from heinzel import Progress
from heinzel.kernel import (
    DaemonHandoverObservation,
    DaemonHandoverPlan,
    DaemonHandoverRequest,
    HeadRefresh,
    Installation,
    WebHeadRefresh,
)
from heinzel.kernel._installation import _run
from heinzel.modules.system._announced import Announcement, environment_versions
from heinzel.modules.system._update import UpdateService
from tests.integration.auto_update_real_uv_test import private_python, unavailable, wheel

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def needs_uv():
    if shutil.which("uv") is None:
        unavailable("uv isn't installed, so there is no real uv to test with")


def release(folder: Path, version: str, *, alarm_needs: str = "heinzel-clock>=1") -> None:
    wheel(folder, "heinzel", version, script="hz")
    wheel(folder, "heinzel-clock", version, requires=(f"heinzel>={version}",))
    wheel(folder, "heinzel-alarm", version, requires=("heinzel>=1", alarm_needs))


class Heads:
    """The installed daemon and web server: each restarts only through its seam and reports what is installed then."""

    def __init__(self, prefix: Path) -> None:
        self.prefix = prefix
        self.pending = False
        self.events: list[str] = []
        self.running = {"daemon": "1.0.0", "web": "1.0.0"}

    def installed(self) -> str:
        return environment_versions(self.prefix)["heinzel"]

    # DaemonInstallationLifecycle.mutation and its scope
    def mutation(self, checkpoint, rollback_identity):
        @contextmanager
        def guard():
            self.pending, self.checkpoint_matched, self.complete = True, checkpoint(), False
            yield self
            assert self.complete, "the mutation must complete before it is released"
            self.pending = False
            self.events.append("mutation complete")

        return guard()

    def capture(self) -> DaemonHandoverPlan:
        return DaemonHandoverPlan(active=True, platform="darwin", unit_signature="unit")

    def verified_complete(self, _generation: str) -> None:
        self.complete = True

    def abort_unchanged(self) -> None:
        self.complete = True

    def request(self, _plan: DaemonHandoverPlan) -> DaemonHandoverRequest:
        return DaemonHandoverRequest(requested=True, reason="kickstart", request_id="e" * 64)

    def observation(self, request_id: str) -> DaemonHandoverObservation | None:
        assert not self.pending, "the daemon is observed only after the mutation completed"
        self.running["daemon"] = self.installed()  # the kickstarted tick imports what is installed now
        self.events.append(f"daemon {self.running['daemon']}")
        return DaemonHandoverObservation(request_id, self.running["daemon"], "f" * 64, (), "now", "unit", "unit")

    def web(self) -> WebHeadRefresh:
        def refresh(target: str) -> HeadRefresh:
            assert not self.pending, "the web server restarts only after the mutation completed"
            self.running["web"] = self.installed()
            self.events.append(f"web {self.running['web']}")
            status = "verified" if self.running["web"] == target else "failed"
            return HeadRefresh(head="web", status=status, version=self.running["web"])

        return WebHeadRefresh(refresh=refresh)


@pytest.fixture
def tool(tmp_path, monkeypatch):
    """hz 1.0.0 with heinzel-clock and heinzel-alarm (which needs heinzel-clock), installed by real uv."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    release(wheels, "1.0.0")
    for variable, folder in [("UV_TOOL_DIR", "tools"), ("UV_TOOL_BIN_DIR", "bin"), ("UV_CACHE_DIR", "cache")]:
        monkeypatch.setenv(variable, str(tmp_path / folder))
    monkeypatch.setenv("UV_NO_CONFIG", "1")
    monkeypatch.delenv("UV_EXCLUDE_NEWER", raising=False)
    offline = ["--no-index", "--find-links", str(wheels)]

    def runner(command, environment=None):
        result = _run([*command, *offline] if command[1:3] == ["tool", "install"] else command, environment)
        receipt = tmp_path / "tools" / "heinzel" / "uv-receipt.toml"
        if receipt.is_file():
            text = receipt.read_text()
            for line in ("no-index = true\n", f'find-links = ["{wheels.as_uri()}"]\n'):
                text = text.replace(line, "")
            receipt.write_text(text)
        return result

    python = private_python(tmp_path)
    command = ["uv", "tool", "install", "--python", python, "heinzel", "--with", "heinzel-clock"]
    first = runner([*command, "--with", "heinzel-alarm"])
    assert first.returncode == 0, first.stderr
    receipt = tmp_path / "tools" / "heinzel" / "uv-receipt.toml"
    receipt.write_text(receipt.read_text().replace("\n[tool.options]\n", "\n").rstrip("\n") + "\n")
    prefix = (tmp_path / "tools" / "heinzel").resolve()
    installation = Installation(Progress(), prefix=prefix, uv=shutil.which("uv"), runner=runner)
    installation.record_startup_state(expected=installation.startup_fingerprint())
    return installation, wheels


def apply(installation, tmp_path, heads, plugins):
    now = datetime.now(UTC)
    announced = Announcement(
        "2.0.0", plugins, exclude_newer=now + timedelta(days=1), exclude_newer_core=now + timedelta(days=1)
    )
    service = UpdateService(installation, None, lifecycle=heads, web=heads.web())  # type: ignore[arg-type]
    return service.apply_announced(announced, current="1.0.0", folder=tmp_path / "state", trial=lambda _: True)


def test_an_update_moves_a_plugin_and_the_plugin_it_needs_and_every_head_runs_the_new_version(tool, tmp_path):
    installation, wheels = tool
    release(wheels, "2.0.0", alarm_needs="heinzel-clock>=2")  # the new alarm needs the new clock
    heads = Heads(installation.prefix)
    result = apply(installation, tmp_path, heads, {"heinzel-clock": "2.0.0", "heinzel-alarm": "2.0.0"})
    assert result.updated, result.message
    versions = environment_versions(installation.prefix)
    assert [versions[name] for name in ("heinzel", "heinzel-clock", "heinzel-alarm")] == ["2.0.0"] * 3
    assert heads.running == {"daemon": "2.0.0", "web": "2.0.0"}
    assert [(head.head, head.status, head.version) for head in result.heads] == [
        ("daemon", "verified", "2.0.0"),
        ("web", "verified", "2.0.0"),
    ]
    assert heads.events[0] == "mutation complete"  # no head restarted while the installation was changing
    assert installation.startup_state_matches()


def test_a_plugin_whose_new_dependency_is_not_announced_puts_everything_back_and_restarts_nothing(tool, tmp_path):
    installation, wheels = tool
    release(wheels, "2.0.0", alarm_needs="heinzel-clock>=2")
    heads = Heads(installation.prefix)
    receipt = (installation.prefix / "uv-receipt.toml").read_bytes()
    # Only the alarm is announced: the clock it needs is held at 1.0.0, so uv can't install exactly this.
    result = apply(installation, tmp_path, heads, {"heinzel-alarm": "2.0.0"})
    assert (result.updated, result.restored) == (False, True), result.message
    versions = environment_versions(installation.prefix)
    assert [versions[name] for name in ("heinzel", "heinzel-clock", "heinzel-alarm")] == ["1.0.0"] * 3
    assert (installation.prefix / "uv-receipt.toml").read_bytes() == receipt
    assert result.heads == []
    assert heads.running == {"daemon": "1.0.0", "web": "1.0.0"}
    assert installation.startup_state_matches()
