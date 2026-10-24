import pytest

from heinzel import InfoLet, Module, OperationLet, ServiceLet

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", ["Example", "1st", "with_underscore", "", "-dash"])
def test_rejects_invalid_names(name):
    with pytest.raises(ValueError, match="must match"):
        Module(name, "x")


def test_operation_decorator_forms():
    module = Module("example", "x")

    @module.operation
    def say_hello() -> str:
        """Say hello.

        Longer description.
        """
        return "hi"

    @module.operation(name="custom", read_only=False)
    def _other() -> None:
        """Do something."""

    first, second = module.lets
    assert isinstance(first, OperationLet)
    assert (first.name, first.summary, first.read_only, first.func) == ("say-hello", "Say hello.", True, say_hello)
    assert isinstance(second, OperationLet)
    assert (second.name, second.read_only) == ("custom", False)


def test_operation_requires_docstring():
    module = Module("example", "x")
    with pytest.raises(ValueError, match="needs a docstring"):
        module.operation(lambda: None)


def test_rejects_duplicate_lets_of_same_kind():
    module = Module("example", "x")
    module.add(OperationLet(name="a", func=print, summary="a"))
    with pytest.raises(ValueError, match="already has"):

        @module.operation(name="a")
        def again() -> None:
            """Again."""


def test_service_and_info():
    class Service:
        pass

    module = Module("example", "x")
    module.service(Service)

    @module.info
    def section() -> dict[str, str]:
        return {"a": "b"}

    service, info = module.lets
    assert service == ServiceLet(name="Service", provides=Service, factory=Service)
    assert info == InfoLet(name="example", provider=section)
    assert module.heinzel_lets() == module.lets
    assert "example" in repr(module)


def test_credential_declares_a_let_and_returns_it():
    module = Module("example", "x")
    token = module.credential("api-token", purpose="Example API token.", service="shared")
    assert module.lets == (token,)
    assert (token.name, token.purpose, token.service) == ("api-token", "Example API token.", "shared")


@pytest.mark.parametrize(("name", "purpose", "problem"), [("API_TOKEN", "x", "must match"), ("token", " ", "purpose")])
def test_credential_needs_a_valid_name_and_a_purpose(name, purpose, problem):
    with pytest.raises(ValueError, match=problem):
        Module("example", "x").credential(name, purpose=purpose)
