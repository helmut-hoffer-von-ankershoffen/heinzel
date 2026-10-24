"""Binding capabilities to adapters and the handles requesters get (ADR 0028), with test-only adapters."""

from datetime import UTC, datetime

import pytest

from heinzel import (
    Config,
    Document,
    Documents,
    HeinzelError,
    JobLet,
    Kernel,
    Module,
    ModuleSettings,
    OperationLet,
    SectionReplacement,
    Tracker,
    Window,
)
from heinzel.kernel import CAPABILITIES, Capabilities, CapabilitySettings, ModuleRecord, Origin

pytestmark = pytest.mark.unit

WINDOW = Window(since=datetime(2026, 10, 1, tzinfo=UTC), until=datetime(2026, 10, 8, tzinfo=UTC))
BUILTIN = Origin(distribution="heinzel", version="0.0.0", builtin=True)


class NotesSettings(ModuleSettings):
    prefix: str = "notes"


class Notes:
    """A test-only documents adapter keeping text in memory."""

    written: list[tuple[str, str, str]]

    def __init__(self, settings: NotesSettings) -> None:
        self.prefix = settings.prefix
        self.written = []

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title=f"{self.prefix}:{ref}", text="body")

    def replace_section(self, ref: str, marker: str, text: str) -> SectionReplacement:
        self.written.append((ref, marker, text))
        return SectionReplacement(ref=ref, marker=marker)

    def extra(self) -> str:
        return "not part of the protocol"


def notes_module(*, modes: tuple[str, ...] = ("read", "write"), name: str | None = None) -> Module:
    module = Module("notes", "Provides documents for tests.")
    module.settings(NotesSettings)
    module.provides("documents", modes=set(modes), hosts=["notes.invalid"], name=name)(Notes)
    return module


def bind(**bindings: str) -> Config:
    return Config(files=(), data={"capabilities": bindings})


def digest_module() -> Module:
    module = Module("digest", "Reads and publishes documents.", requires=["documents:read"])

    @module.operation
    def show(documents: Documents) -> str:
        """Read a document."""
        return documents.read("weekly").title

    @module.operation
    def sneak(documents: Documents) -> str:
        """Try to write from a read-only operation."""
        return documents.replace_section("weekly", "summary", "x").ref

    @module.operation(read_only=False, requires=["documents:write"])
    def publish(documents: Documents) -> str:
        """Write a section."""
        return documents.replace_section("weekly", "summary", "new").ref

    @module.operation(read_only=False)
    def publish_undeclared(documents: Documents) -> str:
        """Write without declaring write."""
        return documents.replace_section("weekly", "summary", "new").ref

    @module.operation
    def tracked(tracker: Tracker) -> int:
        """Ask for an undeclared capability."""
        return len(tracker.issues_updated("APP", WINDOW))

    @module.job(schedule="@daily", requires=["documents:read"])
    def nightly(documents: Documents) -> str:
        """Read nightly."""
        return documents.read("nightly").ref

    return module


def operation(kernel: Kernel, qualname: str, name: str) -> OperationLet:
    return next(let for record, let in kernel.lets(OperationLet) if record.qualname == qualname and let.name == name)


def run(kernel: Kernel, qualname: str, name: str) -> object:
    return kernel.invoke(operation(kernel, qualname, name))


@pytest.fixture
def kernel(make_kernel) -> Kernel:
    return make_kernel(notes=notes_module(), digest=digest_module(), config=bind(documents="notes"))


def test_a_declared_read_gets_the_bound_adapter_built_with_injection(kernel):
    assert run(kernel, "digest", "show") == "notes:weekly"


def test_calling_a_write_method_with_a_read_declaration_fails(kernel):
    with pytest.raises(HeinzelError, match=r"digest sneak can't call documents.replace_section, which needs write"):
        run(kernel, "digest", "sneak")


def test_a_publish_operation_that_declared_write_can_write(kernel):
    assert run(kernel, "digest", "publish") == "weekly"
    adapter = kernel.capabilities._instances["documents", "notes"]
    assert isinstance(adapter, Notes)
    assert adapter.written == [("weekly", "summary", "new")]


def test_a_publish_operation_must_declare_write_too(kernel):
    with pytest.raises(HeinzelError, match=r"declare `documents:read,write`"):
        run(kernel, "digest", "publish-undeclared")


def test_asking_for_an_undeclared_capability_fails(kernel):
    with pytest.raises(HeinzelError, match=r"digest tracked asks for Tracker without declaring it"):
        run(kernel, "digest", "tracked")


def test_jobs_resolve_through_kernel_call(kernel):
    job = next(let for _, let in kernel.lets(JobLet))
    assert kernel.call(job.func) == "nightly"


def test_a_function_no_module_declares_cannot_ask_for_a_capability(kernel):
    def stranger(documents: Documents) -> None:
        del documents

    with pytest.raises(HeinzelError, match=r"stranger asks for Documents, but only operations"):
        kernel.call(stranger)


def test_handles_expose_only_protocol_methods(kernel):
    handle = kernel.capabilities.handle(CAPABILITIES["documents"], operation(kernel, "digest", "show").func)
    with pytest.raises(AttributeError, match="documents has no method 'extra'"):
        handle.extra  # noqa: B018 - the attribute access is what is tested
    assert repr(handle) == "<documents:read for digest show via Notes>"


def test_an_unbound_capability_fails_with_the_binding_to_write(make_kernel):
    kernel = make_kernel(notes=notes_module(), digest=digest_module())
    with pytest.raises(
        HeinzelError, match=r'no adapter bound. Bind it in the configuration: \[capabilities\] documents = "notes"'
    ):
        run(kernel, "digest", "show")


def test_a_binding_to_an_unknown_adapter_fails(make_kernel):
    kernel = make_kernel(digest=digest_module(), config=bind(documents="nowhere"))
    with pytest.raises(HeinzelError, match=r"no installed module provides documents as 'nowhere'.*Install a plugin"):
        run(kernel, "digest", "show")
    assert ("documents", "nowhere") in kernel.capabilities.unknown_bindings()


def test_a_read_only_adapter_cannot_serve_a_write(make_kernel):
    kernel = make_kernel(notes=notes_module(modes=("read",)), digest=digest_module(), config=bind(documents="notes"))
    assert run(kernel, "digest", "show") == "notes:weekly"
    with pytest.raises(HeinzelError, match="notes serves only read, not write"):
        run(kernel, "digest", "publish")


def test_a_pinned_requirement_uses_its_adapter_whatever_is_bound(make_kernel):
    pinned = Module("pinned", "Needs one vendor.", requires=["acme:documents:read"])

    @pinned.operation
    def show(documents: Documents) -> str:
        """Read."""
        return documents.read("x").title

    kernel = make_kernel(
        notes=notes_module(), acme=notes_module(name="acme"), pinned=pinned, config=bind(documents="notes")
    )
    assert run(kernel, "pinned", "show") == "notes:x"
    assert kernel.capabilities.matrix()[0].adapter == "acme"


def test_a_write_declared_by_the_module_reaches_only_publish_operations(make_kernel):
    module = Module("writer", "Declares write for the whole module.", requires=["documents:read,write"])

    @module.operation
    def peek(documents: Documents) -> str:
        """Try to write from a read-only operation."""
        return documents.replace_section("a", "b", "c").ref

    @module.operation(read_only=False)
    def publish(documents: Documents) -> str:
        """Write."""
        return documents.replace_section("a", "b", "c").ref

    kernel = make_kernel(notes=notes_module(), writer=module, config=bind(documents="notes"))
    with pytest.raises(HeinzelError, match="only publish operations"):
        run(kernel, "writer", "peek")
    assert run(kernel, "writer", "publish") == "a"


def test_services_get_their_modules_requirements(make_kernel):
    module = Module("reader", "Reads through a service.", requires=["documents:read"])

    class Reader:
        def __init__(self, documents: Documents) -> None:
            self.documents = documents

    module.service(Reader)

    @module.operation
    def show(reader: Reader) -> str:
        """Read through the service."""
        return reader.documents.read("x").title

    kernel = make_kernel(notes=notes_module(), reader=module, config=bind(documents="notes"))
    assert run(kernel, "reader", "show") == "notes:x"


def test_the_kernel_injects_capabilities_so_heads_hide_them(kernel):
    assert kernel.provides(Documents)
    assert kernel.resolve(Capabilities) is kernel.capabilities


def test_the_matrix_lists_every_declared_requirement(kernel):
    rows = [(u.requester, u.kind, u.requirement, u.adapter, u.hosts, u.problem) for u in kernel.capabilities.matrix()]
    assert rows == [
        ("digest", "module", "documents:read", "notes", ["notes.invalid"], None),
        ("digest publish", "operation", "documents:write", "notes", ["notes.invalid"], None),
        ("digest nightly", "job", "documents:read", "notes", ["notes.invalid"], None),
    ]


def test_a_plugin_adapter_colliding_with_another_is_skipped_and_reported(make_kernel):
    kernel = make_kernel(notes=notes_module(), other=notes_module())
    assert kernel.errors["other"] == "Adapter skipped: another module already provides documents as 'notes'"
    assert kernel.capabilities.adapters["documents", "notes"].module == "notes"


def test_an_adapter_class_missing_protocol_methods_is_skipped(make_kernel):
    module = Module("broken", "Provides half a protocol.")

    @module.provides("documents", modes={"read"})
    class Half:
        def read(self, ref: str) -> Document:
            return Document(ref=ref, title="", text="")

    kernel = make_kernel(broken=module)
    assert kernel.errors["broken"] == "Adapter skipped: adapter broken:documents lacks replace_section of documents"
    assert not kernel.capabilities.adapters


def test_an_adapter_factory_returning_half_a_protocol_fails_when_built(make_kernel):
    module = Module("half", "Provides half a protocol through a factory function.")

    class Half:
        def read(self, ref: str) -> Document:
            return Document(ref=ref, title="", text="")

    def make_half() -> Half:
        return Half()

    module.provides("documents", modes={"read"})(make_half)
    kernel = make_kernel(half=module, digest=digest_module(), config=bind(documents="half"))
    with pytest.raises(HeinzelError, match="Adapter half:documents of half lacks replace_section of documents"):
        run(kernel, "digest", "show")


def test_a_builtin_adapter_collision_is_a_bug(settings):
    def adapter_only() -> Module:
        module = Module("notes", "Provides documents without settings.")
        module.provides("documents", modes={"read", "write"})(Notes)
        return module

    records = [ModuleRecord(name, adapter_only(), BUILTIN) for name in ("one", "two")]
    with pytest.raises(HeinzelError, match="Built-in module 'two': another module already provides documents"):
        Kernel(settings, records)


def test_bindings_accept_dashes_and_environment_variables(monkeypatch):
    settings = Config(files=(), data={"capabilities": {"code-host": "acme"}}).settings(
        CapabilitySettings, "capabilities"
    )
    assert settings.bound("code-host") == "acme"
    monkeypatch.setenv("HZ_CAPABILITIES__TRACKER", "acme-tracker")
    assert Config.empty().settings(CapabilitySettings, "capabilities").bound("tracker") == "acme-tracker"
