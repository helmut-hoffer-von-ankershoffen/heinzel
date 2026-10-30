import pytest

from heinzel import HeinzelError, Kernel, Module, ModuleRecord, ModuleSettings, OperationLet, Settings
from heinzel.kernel import Origin

pytestmark = pytest.mark.unit


def _module(name: str, *operations: str) -> Module:
    module = Module(name, f"{name} module")
    for operation in operations:
        module.add(OperationLet(name=operation, func=print, summary=operation))
    return module


def test_orders_modules_parents_first(make_kernel):
    kernel = make_kernel(b=_module("b"), a__child=_module("child"), a=_module("a"))
    assert [r.qualname for r in kernel.modules] == ["a", "a.child", "b"]
    assert [r.qualname for r in kernel.children(None)] == ["a", "b"]
    assert [r.qualname for r in kernel.children("a")] == ["a.child"]
    assert kernel.module("a.child").parent == "a"


def test_colliding_plugin_modules_are_skipped_and_reported(settings):
    plugin = Origin(distribution="heinzel-x", version="1", builtin=False)
    builtin = Origin(distribution="heinzel", version="1", builtin=True)
    parent = _module("a", "b")
    records = [
        ModuleRecord(qualname="a", module=parent, origin=builtin),
        ModuleRecord(qualname="a", module=_module("a"), origin=plugin),
        ModuleRecord(qualname="a.b", module=_module("b"), origin=plugin),
    ]
    kernel = Kernel(settings, records)
    assert [r.module for r in kernel.modules] == [parent]
    assert set(kernel.errors) == {"a", "a.b"}
    assert "already has an operation named 'b'" in kernel.errors["a.b"]


def test_colliding_builtin_modules_are_a_bug(settings):
    builtin = Origin(distribution="heinzel", version="1", builtin=True)
    records = [ModuleRecord(qualname="a", module=_module("a"), origin=builtin)] * 2
    with pytest.raises(HeinzelError, match="Built-in module 'a'"):
        Kernel(settings, records)


def test_essential_modules_cannot_be_disabled(make_kernel):
    essential = Module("core", "c", essential=True)
    kernel = make_kernel(settings_=Settings(disabled_modules=frozenset({"core"})), core=essential)
    assert [r.qualname for r in kernel.modules] == ["core"]


def test_invoke_validates_arguments_against_their_annotations(make_kernel):
    from typing import Annotated

    from pydantic import Field

    def repeat(kernel: Kernel, times: Annotated[int, Field(ge=1)] = 1) -> int:
        """Repeat."""
        return times

    kernel = make_kernel()
    operation = OperationLet(name="repeat", func=repeat, summary="Repeat.")
    assert kernel.invoke(operation, times=3) == 3
    with pytest.raises(HeinzelError, match="Invalid arguments for repeat: times: Input should be greater"):
        kernel.invoke(operation, times=0)


def test_lets_by_kind_with_owner(make_kernel):
    kernel = make_kernel(a=_module("a", "x", "y"), b=_module("b", "z"))
    assert [(r.qualname, let.name) for r, let in kernel.lets(OperationLet)] == [("a", "x"), ("a", "y"), ("b", "z")]


def test_disabled_modules_and_their_submodules_are_blocked(make_kernel):
    settings = Settings(disabled_modules=frozenset({"a"}))
    kernel = make_kernel(settings_=settings, a=_module("a"), a__child=_module("child"), b=_module("b"))
    assert [r.qualname for r in kernel.modules] == ["b"]
    assert [r.qualname for r in kernel.blocked] == ["a", "a.child"]  # known, so they can be switched on again


class Clock:
    pass


class Clock2Settings(ModuleSettings):
    x: int = 0


class Greeter:
    def __init__(self, clock: Clock, settings: Settings) -> None:
        self.clock = clock
        self.settings = settings


def test_injects_kernel_settings_and_services_once(make_kernel):
    module = Module("a", "a")
    module.service(Clock)
    module.service(Greeter)
    kernel = make_kernel(a=module)

    greeter = kernel.resolve(Greeter)
    assert greeter is kernel.resolve(Greeter)
    assert greeter.clock is kernel.resolve(Clock)
    assert greeter.settings is kernel.settings
    assert kernel.resolve(Kernel) is kernel


def test_call_injects_only_missing_parameters(make_kernel):
    module = Module("a", "a")
    module.service(Clock)
    kernel = make_kernel(a=module)

    def operation(name: str, clock: Clock, kernel: Kernel) -> tuple[str, Clock, Kernel]:
        return name, clock, kernel

    explicit = Clock()
    assert kernel.call(operation, name="x", clock=explicit) == ("x", explicit, kernel)


def test_unknown_service_is_an_error(make_kernel):
    with pytest.raises(HeinzelError, match="No module provides Clock"):
        make_kernel().resolve(Clock)


def test_service_provided_twice_by_a_plugin_is_reported(make_kernel):
    first, second = Module("a", "a"), Module("b", "b")
    first.service(Clock)
    second.service(Clock)
    kernel = make_kernel(a=first, b=second)
    assert kernel.errors == {"b": "Service skipped: Clock is already provided by another module"}


def test_a_plugin_sorting_before_a_builtin_cannot_take_over_its_service(make_kernel):
    from heinzel import Credentials
    from heinzel.testing import MemoryBackend

    impostor = Module("aaa", "Sorts before system.")
    impostor.service(Credentials, lambda: Credentials(MemoryBackend()))
    kernel = make_kernel(builtins=True, aaa=impostor)
    assert kernel.errors == {"aaa": "Service skipped: Credentials is already provided by another module"}
    assert kernel.resolve(Credentials).backend.name == "environment"


def test_service_provided_twice_by_builtins_is_a_bug(settings):
    first, second = Module("a", "a"), Module("b", "b")
    first.service(Clock)
    second.service(Clock)
    builtin = Origin(distribution="heinzel", version="1", builtin=True)
    records = [ModuleRecord(qualname=m.name, module=m, origin=builtin) for m in (first, second)]
    with pytest.raises(HeinzelError, match="already provided"):
        Kernel(settings, records)


def test_injects_module_settings_from_the_module_section(make_kernel, monkeypatch):
    from heinzel import Config

    class GreeterSettings(ModuleSettings):
        greeting: str = "Hello"

    module = Module("a", "a")
    module.settings(GreeterSettings)
    child = Module("b", "b")
    child.settings(Clock2Settings)
    kernel = make_kernel(a=module, a__b=child, config=Config(files=(), data={"a": {"greeting": "Hi", "b": {"x": 3}}}))

    assert kernel.resolve(GreeterSettings).greeting == "Hi"
    assert kernel.resolve(Clock2Settings).x == 3
    assert [section for section, _ in kernel.settings_of()] == ["hz", "capabilities", "a", "a.b"]
    assert kernel.resolve(Config) is kernel.config


def test_operations_needing_a_disabled_modules_service_are_unavailable(make_kernel):
    provider, consumer = Module("provider", "p"), Module("consumer", "c")
    provider.service(Clock)

    def needs_clock(clock: Clock) -> None:
        """Need a clock."""

    def standalone(name: str) -> None:
        """Need nothing."""

    kernel = make_kernel(
        settings_=Settings(disabled_modules=frozenset({"provider"})), provider=provider, consumer=consumer
    )
    assert not kernel.available(needs_clock)
    assert kernel.available(standalone)


def test_bind_replaces_an_injected_instance(make_kernel):
    from heinzel import Progress

    class Loud(Progress):
        pass

    kernel = make_kernel()
    loud = Loud()
    kernel.bind(Progress, loud)
    assert kernel.resolve(Progress) is loud


def test_checks_must_explain_problems():
    from heinzel import Check, Status

    assert Check(name="ok", status=Status.UP).detail == ""
    with pytest.raises(ValueError, match="needs a detail"):
        Check(name="bad", status=Status.DOWN)


def test_registered_service_requires_exact_kernel_record_and_factory_instance(settings):
    class Contract:
        pass

    class Provider(Contract):
        pass

    module = Module("plug", "Installed plugin")
    module.service(Contract, Provider)
    record = ModuleRecord(
        qualname="plug", module=module, origin=Origin(distribution="fictional-plugin", version="1", builtin=False)
    )
    kernel = Kernel(settings, [record])
    selected = kernel.module("plug")
    assert kernel.resolve_registered_service(Contract, selected) is kernel.resolve(Contract)
    fabricated = ModuleRecord(qualname="plug", module=module, origin=record.origin)
    with pytest.raises(HeinzelError):
        kernel.resolve_registered_service(Contract, fabricated)
    other = ModuleRecord(qualname="other", module=module, origin=record.origin)
    with pytest.raises(HeinzelError):
        kernel.resolve_registered_service(Contract, other)
    kernel.bind(Contract, Provider())
    with pytest.raises(HeinzelError):
        kernel.resolve_registered_service(Contract, selected)

    def factory() -> Contract:
        return Provider()

    other_module = Module("factory", "Factory plugin")
    other_module.service(Contract, factory)
    other_record = ModuleRecord(qualname="factory", module=other_module, origin=record.origin)
    other_kernel = Kernel(settings, [other_record])
    with pytest.raises(HeinzelError):
        other_kernel.resolve_registered_service(Contract, other_kernel.module("factory"))
