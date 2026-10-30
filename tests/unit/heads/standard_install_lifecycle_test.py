"""The standard installer uses the selected target and keeps its lifecycle fence through the checkpoint."""

import hashlib
import json
import os
import sys
from contextlib import contextmanager

import pytest

from heinzel import HeinzelError, Kernel, Settings
from heinzel.heads import _tool_install as tool
from heinzel.heads import install
from heinzel.kernel import Installation, ProcessOutcome
from heinzel.modules.system.daemon import _installation_lifecycle
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._service import DaemonService

pytestmark = pytest.mark.unit


def test_standard_managed_scope_binds_target_before_resolve_and_checkpoints_under_guard(tmp_path, monkeypatch):
    target = tool.ToolTarget(tmp_path / "tools", tmp_path / "bin")
    target.store.mkdir()
    target.binary.mkdir()
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    (settings.data_dir / "app-artifacts").mkdir(parents=True)
    wheel = settings.data_dir / "app-artifacts/heinzel-1.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    artifact = install.Artifact(name="heinzel", version="1.0", filename=wheel.name, sha256="a" * 64, url="fixture")
    profile = install.AppProfile(format=2, core_version="1.0", sdk=1, artifacts=[artifact])
    events = []

    class Lifecycle:
        @contextmanager
        def quiescent(self, binary):
            assert binary == target.binary
            events.append("enter")
            yield
            assert (settings.data_dir / "standard-app.json").exists()
            events.append("commit")

    class Kernel:
        def bind(self, kind, selected):
            assert kind is Installation
            assert selected.prefix == target.prefix
            events.append("bind")

        def provides(self, _kind):
            return True

        def resolve(self, _kind):
            assert events == ["bind"]
            events.append("resolve")
            return Lifecycle()

    def run(command, selected, expected, wheels, installation, preserved):  # noqa: PLR0913, PLR0917
        assert events == ["bind", "resolve", "enter"]
        assert selected == target
        assert installation.prefix == target.prefix
        assert expected == {"heinzel": ("1.0", "a" * 64)}
        assert wheels == {"heinzel": wheel}
        assert preserved == []
        assert command[3] == "heinzel"
        events.append("run")
        return "b" * 64

    def record(self, *, expected):
        assert self.prefix == target.prefix
        assert expected == "b" * 64
        assert events == ["bind", "resolve", "enter", "run"]
        events.append("record")

    monkeypatch.setattr(install, "uv_executable", lambda: "uv")
    monkeypatch.setattr(install, "resolve_target", lambda _uv: target)
    monkeypatch.setattr(install, "boot", lambda _settings: Kernel())
    monkeypatch.setattr(install, "existing_requirements", lambda _prefix: [])
    monkeypatch.setattr(install, "run_tool", run)
    monkeypatch.setattr(tool._TargetInstallation, "record_startup_state", record)
    assert install._managed_install(profile, [artifact], [wheel], settings, set()) == 0
    assert events == ["bind", "resolve", "enter", "run", "record", "commit"]


@pytest.mark.parametrize("quiescent", [True, False])
def test_real_lifecycle_guard_wraps_selected_tool_and_checkpoint_with_fake_manager(tmp_path, monkeypatch, quiescent):
    target, wheel, digest, _site = _owned_candidate(tmp_path)
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state", log_dir=tmp_path / "logs")
    (settings.data_dir / "app-artifacts").mkdir(parents=True)
    artifact = install.Artifact(name="heinzel", version="1.0", filename=wheel.name, sha256=digest, url="fixture")
    profile = install.AppProfile(format=2, core_version="1.0", sdk=1, artifacts=[artifact])
    events = []

    def probe(command):
        events.append(command[-1])
        output = {
            "managername": "Aqua\n" if quiescent else "Background\n",
            "manageruid": f"{os.getuid()}\n",
            "list": "PID Status Label\n",
        }
        return ProcessOutcome(0, output[command[-1]], "")

    class BoundKernel:
        def bind(self, kind, selected):
            assert kind is Installation
            self.selected = selected

        def provides(self, _kind):
            return True

        def resolve(self, _kind):
            self.service = DaemonService(
                Kernel(settings),
                settings,
                DaemonSettings(unit_dir=tmp_path / "units"),
                platform="darwin",
                quiescence_probe=probe,
                continuity_fingerprint=self.selected.startup_fingerprint,
                quiescence_installation=self.selected,
                runner=lambda *_args: pytest.fail("must not call service manager"),
            )
            return _installation_lifecycle(self.service)

    bound = BoundKernel()

    def run(_command, _target, _expected, _wheels, installation, _preserved):
        assert installation is bound.selected
        assert (bound.service.state_dir / "standard-install-pending.json").exists()
        events.append("uv-fixture")
        return installation.startup_fingerprint()

    monkeypatch.setattr(install, "uv_executable", lambda: "fake-uv")
    monkeypatch.setattr(install, "resolve_target", lambda _uv: target)
    monkeypatch.setattr(install, "boot", lambda _settings: bound)
    monkeypatch.setattr(install, "run_tool", run)
    if not quiescent:
        with pytest.raises(HeinzelError, match="not proven quiescent"):
            install._managed_install(profile, [artifact], [wheel], settings, set())
        assert "uv-fixture" not in events
        assert not bound.selected.startup_state_matches()
        return
    assert install._managed_install(profile, [artifact], [wheel], settings, set()) == 0
    assert events.count("uv-fixture") == 1
    assert bound.selected.startup_state_matches()
    assert not (bound.service.state_dir / "standard-install-pending.json").exists()
    assert not (tmp_path / "units").exists()


def test_fresh_system_only_refusal_precedes_pending_and_allows_retry(tmp_path, monkeypatch):
    target, wheel, digest, _site = _owned_candidate(tmp_path)
    prepared = tmp_path / "prepared-prefix"
    assert target.prefix.resolve().is_relative_to(tmp_path.resolve())
    shim = target.binary / "hz"
    assert shim.is_symlink()
    assert shim.readlink() == target.prefix / "bin/hz"
    shim.unlink()
    target.prefix.rename(prepared)
    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state", log_dir=tmp_path / "logs")
    (settings.data_dir / "app-artifacts").mkdir(parents=True)
    artifact = install.Artifact(name="heinzel", version="1.0", filename=wheel.name, sha256=digest, url="fixture")
    profile = install.AppProfile(format=2, core_version="1.0", sdk=1, artifacts=[artifact])
    events = []

    def probe(command):
        events.append(command[-1])
        output = {"managername": "Aqua\n", "manageruid": f"{os.getuid()}\n", "list": "PID Status Label\n"}
        return ProcessOutcome(0, output[command[-1]], "")

    class BoundKernel:
        def bind(self, kind, selected):
            assert kind is Installation
            self.selected = selected

        def provides(self, _kind):
            return True

        def resolve(self, _kind):
            self.service = DaemonService(
                Kernel(settings),
                settings,
                DaemonSettings(unit_dir=tmp_path / "units"),
                platform="darwin",
                quiescence_probe=probe,
                continuity_fingerprint=self.selected.startup_fingerprint,
                quiescence_installation=self.selected,
                runner=lambda *_args: pytest.fail("must not call service manager"),
            )
            return _installation_lifecycle(self.service)

    bound = BoundKernel()

    def run(_command, _target, _expected, _wheels, installation, _preserved):
        assert (settings.state_dir / "daemon/standard-install-pending.json").exists()
        prepared.rename(target.prefix)
        assert not shim.exists()
        assert not shim.is_symlink()
        shim.symlink_to(target.prefix / "bin/hz")
        events.append("uv-fixture")
        return installation.startup_fingerprint()

    monkeypatch.setattr(install, "uv_executable", lambda: "fake-uv")
    monkeypatch.setattr(install, "resolve_target", lambda _uv: target)
    monkeypatch.setattr(install, "boot", lambda _settings: bound)
    monkeypatch.setattr(install, "run_tool", run)
    monkeypatch.setenv("UV_NO_MANAGED_PYTHON", "1")
    with pytest.raises(HeinzelError, match="Remove the system-only Python preference"):
        install._managed_install(profile, [artifact], [wheel], settings, set())
    assert events == []
    assert not (settings.state_dir / "daemon/standard-install-pending.json").exists()
    assert not target.prefix.exists()
    monkeypatch.delenv("UV_NO_MANAGED_PYTHON")
    assert install._managed_install(profile, [artifact], [wheel], settings, set()) == 0
    assert events.count("uv-fixture") == 1
    assert bound.selected.startup_state_matches()
    assert not (settings.state_dir / "daemon/standard-install-pending.json").exists()


def _owned_candidate(tmp_path):
    target = tool.ToolTarget(tmp_path / "tools", tmp_path / "bin")
    target.store.mkdir()
    target.binary.mkdir()
    (target.prefix / "bin").mkdir(parents=True)
    base = tmp_path / "base-python"
    assert not base.exists()
    assert not base.is_symlink()
    base.write_text("inert")
    assert base.resolve().is_relative_to(tmp_path.resolve())
    assert not base.samefile(sys.executable)
    assert not base.samefile(sys._base_executable)
    base.chmod(0o700)
    (target.prefix / "bin/python").symlink_to(base)
    (target.prefix / "bin/hz").write_text("inert")
    (target.binary / "hz").symlink_to(target.prefix / "bin/hz")
    wheel = tmp_path / "heinzel-1.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    site = target.prefix / "lib/python3.14/site-packages"
    metadata = site / "heinzel-1.0.dist-info"
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text("Name: heinzel\nVersion: 1.0\n")
    (metadata / "direct_url.json").write_text(
        json.dumps({"url": wheel.as_uri(), "archive_info": {"hashes": {"sha256": digest}}})
    )
    (target.prefix / "uv-receipt.toml").write_text(
        '[tool]\nrequirements=[{name="heinzel"}]\n'
        f'entrypoints=[{{name="hz", install-path={str(target.binary / "hz")!r}}}]\n'
        f'overrides=[{{name="heinzel", path={str(wheel)!r}}}]\n'
    )
    return target, wheel, digest, site


@pytest.mark.parametrize("unsafe", [False, True])
def test_metadata_and_startup_paths_precede_runtime_and_exact_override_removal(tmp_path, monkeypatch, unsafe):
    target, wheel, digest, site = _owned_candidate(tmp_path)
    installation = tool._TargetInstallation(target.prefix, "fake-uv")
    calls = []
    monkeypatch.setattr(installation, "hz", lambda *_: calls.append("hz") or "hz 1.0\n")
    if unsafe:
        (site / "foreign.pth").write_text("import inert_payload\n")
    before = installation.startup_fingerprint()
    if unsafe:
        with pytest.raises(HeinzelError, match="startup paths"):
            tool._verify_and_normalize(
                target, {"heinzel": ("1.0", digest)}, {"heinzel": wheel}, installation, [], (), before
            )
        assert calls == []
        assert "overrides" in (target.prefix / "uv-receipt.toml").read_text()
    else:
        after = tool._verify_and_normalize(
            target, {"heinzel": ("1.0", digest)}, {"heinzel": wheel}, installation, [], (), before
        )
        assert calls == ["hz"]
        assert after == installation.startup_fingerprint() != before
        assert "overrides" not in (target.prefix / "uv-receipt.toml").read_text()


def test_changed_artifact_is_refused_before_target_probe(tmp_path, monkeypatch):
    target, wheel, digest, _site = _owned_candidate(tmp_path)
    installation = tool._TargetInstallation(target.prefix, "fake-uv")
    before = installation.startup_fingerprint()
    wheel.write_bytes(b"changed")
    monkeypatch.setattr(installation, "hz", lambda *_: pytest.fail("must not execute target"))
    with pytest.raises(HeinzelError, match="artifacts changed"):
        tool._verify_and_normalize(
            target, {"heinzel": ("1.0", digest)}, {"heinzel": wheel}, installation, [], (), before
        )
    assert "overrides" in (target.prefix / "uv-receipt.toml").read_text()


def test_bad_override_set_does_not_launch_target(tmp_path, monkeypatch):
    target, wheel, digest, _site = _owned_candidate(tmp_path)
    installation = tool._TargetInstallation(target.prefix, "fake-uv")
    receipt = target.prefix / "uv-receipt.toml"
    receipt.write_text(receipt.read_text().replace(str(wheel), str(tmp_path / "other.whl")))
    before = installation.startup_fingerprint()
    monkeypatch.setattr(installation, "hz", lambda *_: pytest.fail("must not execute target"))
    with pytest.raises(HeinzelError, match="overrides do not match"):
        tool._verify_and_normalize(
            target, {"heinzel": ("1.0", digest)}, {"heinzel": wheel}, installation, [], (), before
        )


@pytest.mark.parametrize("kind", ["directory", "wheel"])
def test_artifact_publication_never_follows_existing_symlink(tmp_path, kind):
    content = b"fixture"
    digest = hashlib.sha256(content).hexdigest()
    artifact = install.Artifact(
        name="heinzel", version="1.0", filename="heinzel-1.0-py3-none-any.whl", sha256=digest, url="fixture"
    )
    directory = tmp_path / "artifacts"
    directory.mkdir()
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"preserve")
    if kind == "directory":
        (directory / digest).symlink_to(tmp_path, target_is_directory=True)
    else:
        (directory / digest).mkdir()
        (directory / digest / artifact.filename).symlink_to(sentinel)
    with pytest.raises(OSError, match=r"directory|symbolic"):
        install._publish_artifact(directory, artifact, content)
    assert sentinel.read_bytes() == b"preserve"


def test_override_file_ignores_existing_predictable_symlink(tmp_path):
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("preserve")
    (tmp_path / "verified-existing.txt").symlink_to(sentinel)
    wheel = tmp_path / "fixture.whl"
    with install._verified_overrides(tmp_path, {"heinzel": wheel}, {"heinzel": ("1.0", "a" * 64)}) as path:
        assert path != tmp_path / "verified-existing.txt"
        assert path.is_file()
    assert not path.exists()
    assert sentinel.read_text() == "preserve"


def test_file_origin_newer_plugin_refuses_refresh_before_uv(tmp_path, monkeypatch):
    target, wheel, digest, site = _owned_candidate(tmp_path)
    plugin = tmp_path / "heinzel-example-1.0-py3-none-any.whl"
    plugin.write_bytes(b"plugin fixture")
    plugin_digest = hashlib.sha256(plugin.read_bytes()).hexdigest()
    metadata = site / "heinzel_example-2.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: heinzel-example\nVersion: 2.0\n")
    (metadata / "direct_url.json").write_text(json.dumps({"url": "file:///old-verified.whl", "archive_info": {}}))
    installation = tool.target_installation(target, "fake-uv")
    monkeypatch.setattr(installation, "run", lambda *_args: pytest.fail("must refuse before uv"))
    with pytest.raises(HeinzelError, match="would downgrade heinzel-example"):
        tool.run_tool(
            ["fake-uv", "tool", "install", "heinzel"],
            target,
            {"heinzel": ("1.0", digest), "heinzel-example": ("1.0", plugin_digest)},
            {"heinzel": wheel, "heinzel-example": plugin},
            installation,
            [],
        )


def test_changed_generation_during_version_observation_refuses_uv(tmp_path, monkeypatch):
    target, wheel, digest, _site = _owned_candidate(tmp_path)
    installation = tool.target_installation(target, "fake-uv")
    observe = tool._installed_versions

    def changed(*args):
        result = observe(*args)
        (target.prefix / "bin/hz").write_text("changed after inspection")
        return result

    monkeypatch.setattr(tool, "_installed_versions", changed)
    monkeypatch.setattr(installation, "run", lambda *_args: pytest.fail("must refuse before uv"))
    with pytest.raises(HeinzelError, match="changed during version inspection"):
        tool.run_tool(
            ["fake-uv", "tool", "install", "heinzel"],
            target,
            {"heinzel": ("1.0", digest)},
            {"heinzel": wheel},
            installation,
            [],
        )


@pytest.mark.parametrize("damage", ["pth", "metadata-symlink", "oversize", "duplicate", "missing"])
def test_local_version_observation_refuses_unsafe_or_ambiguous_metadata(tmp_path, damage):
    target, _wheel, _digest, site = _owned_candidate(tmp_path)
    metadata = site / "heinzel-1.0.dist-info/METADATA"
    if damage == "pth":
        (site / "safe-looking.pth").write_text(".\n")
    elif damage == "metadata-symlink":
        original = site / "other"
        original.write_text(metadata.read_text())
        metadata.unlink()
        metadata.symlink_to(original)
    elif damage == "oversize":
        metadata.write_text("x" * (1024 * 1024 + 1))
    elif damage == "duplicate":
        other = site / "heinzel-duplicate.dist-info"
        other.mkdir()
        (other / "METADATA").write_text(metadata.read_text())
    else:
        metadata.unlink()
    with pytest.raises(HeinzelError, match="versions cannot be safely inspected"):
        tool._installed_versions(target.prefix, {"heinzel"}, {"heinzel"})
