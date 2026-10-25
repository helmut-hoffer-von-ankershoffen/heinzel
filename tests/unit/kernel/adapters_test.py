"""`Adapters[P]`: read handles on every installed adapter of a capability, for the module that chooses (ADR 0057)."""

import pytest

from heinzel import AccessStatus, AccessTest, Adapters, AiAccess, Config, HeinzelError, Module, OperationLet, Tracker

pytestmark = pytest.mark.unit

BUILT: list[str] = []


def option(name: str) -> type:
    class Option:
        def __init__(self) -> None:
            BUILT.append(name)

        def status(self) -> AccessStatus:
            return AccessStatus(title=name.title(), summary=f"{name} for tests", installed=True)

        def test(self) -> AccessTest:
            return AccessTest(ok=True, detail=name)

    return Option


def options() -> Module:
    module = Module("options", "Two AI access options for tests.")
    module.provides("ai-access", modes={"read"}, name="alpha")(option("alpha"))
    module.provides("ai-access", modes={"read"}, name="beta")(option("beta"))
    return module


def chooser() -> Module:
    module = Module("chooser", "Lists every AI access option.")

    @module.operation(name="list", requires=["ai-access:read"])
    def list_(all_options: Adapters[AiAccess]) -> list[str]:
        """List the options' titles."""
        return [all_options[name].status().title for name in all_options]

    @module.operation
    def sneak(all_options: Adapters[AiAccess]) -> int:
        """Ask without declaring."""
        return len(all_options)

    @module.operation(requires=["tracker:read"])
    def wrong(trackers: Adapters[Tracker]) -> int:
        """Ask for adapters of a capability nobody provides."""
        return len(trackers)

    return module


def run(kernel, qualname: str, name: str):
    let = next(let for record, let in kernel.lets(OperationLet) if record.qualname == qualname and let.name == name)
    return kernel.invoke(let)


def test_every_installed_adapter_is_handed_out_built_on_its_first_call(make_kernel):
    BUILT.clear()
    kernel = make_kernel(options=options(), chooser=chooser(), config=Config(files=(), data={}))
    assert BUILT == []
    assert run(kernel, "chooser", "list") == ["Alpha", "Beta"]
    assert BUILT == ["alpha", "beta"]


def test_the_handles_are_read_only_views_without_the_adapter(make_kernel):
    captured: dict[str, object] = {}
    module = Module("peek", "Looks at a handle.")

    @module.operation(requires=["ai-access:read"])
    def look(all_options: Adapters[AiAccess]) -> int:
        """Keep a handle."""
        captured.update(all_options)
        return len(all_options)

    kernel = make_kernel(options=options(), peek=module)
    assert run(kernel, "peek", "look") == 2
    handle = captured["alpha"]
    assert "ai-access:read" in repr(handle)
    with pytest.raises(AttributeError):
        handle.run  # noqa: B018 - not a method of the capability
    assert set(captured) == {"alpha", "beta"}


def test_without_a_declaration_nothing_is_handed_out(make_kernel):
    kernel = make_kernel(options=options(), chooser=chooser())
    with pytest.raises(HeinzelError, match=r'requires=\["ai-access:read"\]'):
        run(kernel, "chooser", "sneak")


def test_a_capability_without_adapters_gives_an_empty_mapping(make_kernel):
    kernel = make_kernel(chooser=chooser())
    assert run(kernel, "chooser", "wrong") == 0


def test_adapters_of_something_that_is_no_capability_are_refused(make_kernel):
    module = Module("odd", "Asks for adapters of a plain class.")

    @module.operation
    def ask(things: Adapters[str]) -> int:
        """Ask for adapters of str."""
        return len(things)

    kernel = make_kernel(odd=module)
    with pytest.raises(HeinzelError, match=r"Adapters\[AiAccess\]"):
        run(kernel, "odd", "ask")


def test_the_mapping_reads_like_a_mapping():
    adapters = Adapters[AiAccess]({"one": object()})
    assert list(adapters) == ["one"]
    assert len(adapters) == 1
    assert "one" in repr(adapters)
    with pytest.raises(KeyError):
        adapters["two"]


def test_an_adapter_name_two_modules_provide_is_left_out(make_kernel):
    other = Module("others", "Another alpha.")
    other.provides("ai-access", modes={"read"}, name="alpha")(option("alpha"))
    kernel = make_kernel(options=options(), others=other, chooser=chooser())
    assert run(kernel, "chooser", "list") == ["Beta"]
