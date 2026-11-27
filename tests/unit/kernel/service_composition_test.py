"""Built-in kernel services and injected host services use the same resolver."""

from typing import TYPE_CHECKING

import pytest

from heinzel.kernel import HeinzelError, Kernel, Module, ModuleRecord, Origin, Progress, Settings
from heinzel.kernel._compose import ServiceSet

if TYPE_CHECKING:
    from heinzel.kernel._head_view import HeadKernel
    from heinzel.spec import KernelView

pytestmark = pytest.mark.unit


def test_host_installed_factory_is_lazy_and_uses_the_normal_resolver(settings):
    created: list[Progress] = []

    def make_progress(config: Settings) -> Progress:
        assert config is settings
        progress = Progress()
        created.append(progress)
        return progress

    services = ServiceSet(factories={Progress: make_progress})
    first: HeadKernel = Kernel(settings, services=services)
    second: KernelView = Kernel(settings, services=services)
    assert not created
    assert first.resolve(Progress) is first.resolve(Progress)
    assert first.resolve(Progress) is not second.resolve(Progress)
    assert created == [first.resolve(Progress), second.resolve(Progress)]


def test_host_override_does_not_claim_a_registered_service(settings):
    class Contract:
        pass

    class Implementation(Contract):
        pass

    module = Module("example", "Example")
    module.service(Contract, Implementation)
    record = ModuleRecord("example", module, Origin("heinzel", "1", builtin=True))
    fake = Contract()
    kernel = Kernel(settings, [record], services=ServiceSet(instances={Contract: fake}))
    assert kernel.resolve(Contract) is fake
    with pytest.raises(HeinzelError, match="selected registered service is unavailable"):
        kernel.resolve_registered_service(Contract, record)


def test_host_services_cannot_replace_kernel_settings(settings):
    with pytest.raises(HeinzelError, match="Host services cannot replace"):
        Kernel(settings, services=ServiceSet(instances={Settings: Settings()}))
