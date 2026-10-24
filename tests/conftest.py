"""Shared fixtures. The test-suite policy (one level per test, in order, with timeouts) is heinzel.testing's."""

from collections.abc import Callable

import pytest

from heinzel import Config, Kernel, Module, ModuleRecord, Settings
from heinzel.kernel import Origin, discover

TEST_ORIGIN = Origin(distribution="heinzel-test", version="9.9.9", builtin=False)


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """No test sees the developer's HZ_* variables or state."""
    for name in ("HZ_LOG_LEVEL", "HZ_PLUGINS_ENABLED", "HZ_DISABLED_MODULES", "HZ_CONFIG", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HZ_UPDATE_CHECK", "HZ_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path_factory.mktemp("hz-config")))
    monkeypatch.setenv("HZ_DATA_DIR", str(tmp_path_factory.mktemp("hz-data")))
    monkeypatch.setenv("HZ_CACHE_DIR", str(tmp_path_factory.mktemp("hz-cache")))


@pytest.fixture
def settings() -> Settings:
    return Config.empty().settings(Settings, "hz")


@pytest.fixture
def make_kernel(settings: Settings) -> Callable[..., Kernel]:
    """Build a kernel from modules given as `qualname=module`, optionally on top of the built-in modules."""

    def make(
        *, builtins: bool = False, settings_: Settings | None = None, config: Config | None = None, **modules: Module
    ) -> Kernel:
        records = discover(plugins=False)[0] if builtins else []
        records += [
            ModuleRecord(qualname=name.replace("__", "."), module=module, origin=TEST_ORIGIN)
            for name, module in modules.items()
        ]
        return Kernel(settings_ or settings, records, config=config)

    return make
