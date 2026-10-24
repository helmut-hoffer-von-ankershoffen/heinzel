"""Shared fixtures. The test-suite policy (one level per test, in order, with timeouts) and the isolation of every
test from the developer's HZ_* variables, config and state (`hz_isolation`) are heinzel.testing's."""

from collections.abc import Callable

import pytest

from heinzel import Config, Kernel, Module, ModuleRecord, Settings
from heinzel.kernel import Origin, discover
from heinzel.modules.org import _discovery as discovery

TEST_ORIGIN = Origin(distribution="heinzel-test", version="9.9.9", builtin=False)


@pytest.fixture(autouse=True)
def _no_machine_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test finds the developer's GitHub or Google login, time zone or Claude Code (or asks gh and gws)."""
    monkeypatch.setattr(discovery, "discover", discovery.Discovered)
    monkeypatch.setattr(discovery, "_run", lambda args: None)  # and no later caller reaches the real CLIs


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
