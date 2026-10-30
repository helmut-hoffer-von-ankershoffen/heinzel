"""Fresh standard app policy refusal before lifecycle mutation."""

import os

import pytest

from heinzel import HeinzelError, Kernel, Settings
from heinzel.heads import install
from heinzel.kernel import Installation, ProcessOutcome
from heinzel.modules.system.daemon import _installation_lifecycle
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._service import DaemonService
from tests.unit.heads.standard_install_lifecycle_test import _owned_candidate

pytestmark = pytest.mark.integration


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
    with pytest.raises(HeinzelError, match="Remove the system-only Python environment setting"):
        install._managed_install(profile, [artifact], [wheel], settings, set())
    assert events == []
    assert not (settings.state_dir / "daemon/standard-install-pending.json").exists()
    assert not target.prefix.exists()
    monkeypatch.delenv("UV_NO_MANAGED_PYTHON")
    assert install._managed_install(profile, [artifact], [wheel], settings, set()) == 0
    assert events.count("uv-fixture") == 1
    assert bound.selected.startup_state_matches()
    assert not (settings.state_dir / "daemon/standard-install-pending.json").exists()
