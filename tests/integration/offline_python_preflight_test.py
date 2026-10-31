"""Private exact-head diagnostic of uv's no-managed-Python failure."""

import hashlib
import os
import shutil
import zipfile

import pytest

from heinzel import HeinzelError, Kernel, Settings
from heinzel.heads import _tool_install as tool
from heinzel.heads import install
from heinzel.kernel import Installation, ProcessOutcome
from heinzel.modules.system.daemon import _installation_lifecycle
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._service import DaemonService
from tests.unit.heads.standard_install_lifecycle_test import _owned_candidate

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("policy", ["never", "manual"])
def test_real_uv_empty_inventory_refuses_before_pending_and_allows_retry(tmp_path, monkeypatch, policy):
    root = tmp_path.resolve()
    target, wheel, digest, _site = _owned_candidate(root)
    target.binary.joinpath("hz").unlink()
    target.prefix.rename(root / "unused-fixture-prefix")
    settings = Settings(data_dir=root / "data", state_dir=root / "state", log_dir=root / "logs")
    (settings.data_dir / "app-artifacts").mkdir(parents=True)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("heinzel-1.0.dist-info/METADATA", "Name: heinzel\nVersion: 1.0\nRequires-Python: >=3.12\n")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    artifact = install.Artifact(name="heinzel", version="1.0", filename=wheel.name, sha256=digest, url="fixture")
    profile = install.AppProfile(format=2, core_version="1.0", sdk=1, artifacts=[artifact])
    for name in ("python", "cache", "home"):
        (root / name).mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(root / "home"))
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(root / "python"))
    monkeypatch.setenv("UV_CACHE_DIR", str(root / "cache"))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", policy)
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_NO_CONFIG", "1")
    monkeypatch.delenv("UV_PYTHON", raising=False)
    monkeypatch.delenv("UV_NO_MANAGED_PYTHON", raising=False)
    monkeypatch.delenv("UV_PYTHON_PREFERENCE", raising=False)

    def probe(command):
        values = {"managername": "Aqua\n", "manageruid": f"{os.getuid()}\n", "list": "PID Status Label\n"}
        return ProcessOutcome(0, values[command[-1]], "")

    class Bound:
        def bind(self, kind, selected):
            assert kind is Installation
            self.selected = selected

        def provides(self, _kind):
            return True

        def resolve(self, _kind):
            self.service = DaemonService(
                Kernel(settings),
                settings,
                DaemonSettings(unit_dir=root / "units"),
                platform="darwin",
                quiescence_probe=probe,
                continuity_fingerprint=self.selected.startup_fingerprint,
                quiescence_installation=self.selected,
                runner=lambda *_: pytest.fail("manager mutation forbidden"),
            )
            return _installation_lifecycle(self.service)

    bound = Bound()
    uv = shutil.which("uv")
    assert uv is not None
    monkeypatch.setattr(install, "uv_executable", lambda: uv)
    monkeypatch.setattr(install, "resolve_target", lambda _uv: target)
    monkeypatch.setattr(install, "boot", lambda _settings: bound)
    with pytest.raises(HeinzelError, match="no compatible uv-managed Python"):
        install._managed_install(profile, [artifact], [wheel], settings, set())
    pending = settings.state_dir / "daemon/standard-install-pending.json"
    assert not pending.exists()
    assert not target.prefix.exists()
    assert not (target.binary / "hz").exists()
    assert not list((root / "python").iterdir())
    # A later available compatible interpreter permits a normal retry; tool mutation is a fake.
    from tests.unit.heads.offline_python_preflight_test import _key

    (root / "python" / _key("3.14.8")).mkdir()
    monkeypatch.setattr(tool, "run_bounded_process", lambda *_a, **_kw: ProcessOutcome(0, str(root / "python"), ""))

    def fake_install(_command, _target, _expected, _wheels, installation, _preserved):
        assert pending.is_file()
        (root / "unused-fixture-prefix").rename(target.prefix)
        (target.binary / "hz").symlink_to(target.prefix / "bin/hz")
        return installation.startup_fingerprint()

    monkeypatch.setattr(install, "run_tool", fake_install)
    assert install._managed_install(profile, [artifact], [wheel], settings, set()) == 0
    assert bound.selected.startup_state_matches()
    assert not pending.exists()
