"""Binding capabilities to adapters and the handles requesters get (ADR 0028), with test-only adapters."""

import inspect
from datetime import UTC, datetime
from typing import Annotated

import pytest
from pydantic import Field

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
from heinzel.kernel import CAPABILITIES, CapabilityView, ModuleRecord, Origin, Requirement
from heinzel.kernel._bindings import Capabilities, CapabilitySettings, Requester

pytestmark = pytest.mark.unit

WINDOW = Window(since=datetime(2026, 10, 1, tzinfo=UTC), until=datetime(2026, 10, 8, tzinfo=UTC))
BUILTIN = Origin(distribution="heinzel", version="0.0.0", builtin=True)


class NotesSettings(ModuleSettings):
    prefix: str = "notes"


class Notes:
    """A test-only documents adapter keeping text in memory."""

    built = 0

    def __init__(self, settings: NotesSettings) -> None:
        Notes.built += 1
        self.prefix = settings.prefix
        self.written: list[tuple[str, str, str]] = []

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title=f"{self.prefix}:{ref}", text="body")

    def replace_section(self, ref: str, marker: str, text: str) -> SectionReplacement:
        self.written.append((ref, marker, text))
        return SectionReplacement(ref=ref, marker=marker)

    def extra(self) -> str:
        return "not part of the protocol"


class Reader:
    """A test-only documents adapter that can only read."""

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title=f"reader:{ref}", text="")


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

    @module.operation
    def count(documents: Documents, times: Annotated[int, Field(ge=1)] = 1) -> int:
        """Validate a constrained parameter next to a capability."""
        return times * len(documents.read("weekly").title)

    @module.job(schedule="@daily", requires=["documents:read"])
    def nightly(documents: Documents) -> str:
        """Read nightly."""
        return documents.read("nightly").ref

    return module


def operation(kernel: Kernel, qualname: str, name: str) -> OperationLet:
    return next(let for record, let in kernel.lets(OperationLet) if record.qualname == qualname and let.name == name)


def run(kernel: Kernel, qualname: str, name: str, **kwargs: object) -> object:
    return kernel.invoke(operation(kernel, qualname, name), **kwargs)


def handle(kernel: Kernel, name: str = "show") -> object:
    return kernel._capabilities.handle(CAPABILITIES["documents"], operation(kernel, "digest", name).func)


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
    adapter = kernel._capabilities._instances["documents", "notes"]
    assert isinstance(adapter, Notes)
    assert adapter.written == [("weekly", "summary", "new")]


def test_a_publish_operation_must_declare_write_itself(kernel):
    with pytest.raises(HeinzelError, match=r"declare `documents:read,write`"):
        run(kernel, "digest", "publish-undeclared")


def test_write_does_not_imply_read(make_kernel):
    module = Module("writer", "Writes only.")

    @module.operation(read_only=False, requires=["documents:write"])
    def publish(documents: Documents) -> str:
        """Read, having declared only write."""
        return documents.read("x").title

    kernel = make_kernel(notes=notes_module(), writer=module, config=bind(documents="notes"))
    with pytest.raises(HeinzelError, match=r"writer publish can't call documents.read, which needs read"):
        run(kernel, "writer", "publish")


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


def test_a_handle_holds_no_adapter_attribute(kernel):
    documents = handle(kernel)
    with pytest.raises(AttributeError):
        documents._adapter  # noqa: B018 - the attribute access is what is tested
    assert not hasattr(documents, "__dict__")
    assert type(documents).__slots__ == ("_allowed", "_denied", "_label")


def test_handle_methods_are_wrappers_not_bound_methods(kernel):
    read = handle(kernel).read
    assert not inspect.ismethod(read)
    assert not hasattr(read, "__self__")
    assert not hasattr(read, "__wrapped__")
    assert read.__qualname__ == "Documents.read"
    assert read.__doc__ == Documents.read.__doc__


def test_modules_get_a_view_that_hands_out_no_handles(kernel):
    from heinzel.kernel._bindings import Capabilities

    assert not kernel.provides(Capabilities)
    view = kernel.resolve(CapabilityView)
    assert not hasattr(view, "handle")
    assert not hasattr(view, "handle_for")
    assert not hasattr(view, "_capabilities") or not hasattr(view, "__dict__")
    assert view.bound("documents") == "notes"
    assert [(a.let.adapter, a.distribution) for a in view.adapters] == [("notes", "heinzel-test")]


def test_handles_expose_only_protocol_methods(kernel):
    documents = handle(kernel)
    with pytest.raises(AttributeError, match="has no method 'extra'"):
        documents.extra  # noqa: B018 - the attribute access is what is tested
    assert repr(documents) == "<documents:read for digest show>"


def test_a_requester_without_a_usable_mode_is_refused_at_handle_time(kernel):
    writer = Requester("digest", "job", "odd", (Requirement("documents", frozenset({"write"})),))
    with pytest.raises(
        HeinzelError, match=r"may not use it: only publish operations may write; declare `documents:read`"
    ):
        kernel._capabilities.handle_for(writer, CAPABILITIES["documents"])


def test_handle_for_serves_requesters_built_by_compilers(kernel):
    reader = Requester("digest", "job", "contract", (Requirement("documents", frozenset({"read"})),))
    assert kernel._capabilities.handle_for(reader, CAPABILITIES["documents"]).read("x").title == "notes:x"


def test_the_kernel_hands_compilers_read_handles_by_capability_name(kernel):
    reader = Requester("digest", "job", "contract", (Requirement("documents", frozenset({"read", "write"})),))
    documents = kernel.handle_for(reader, "documents")
    assert documents.read("x").title == "notes:x"
    with pytest.raises(HeinzelError, match="only publish operations"):
        documents.replace_section("x", "m", "t")


def test_the_kernel_refuses_publishing_requesters_and_unknown_capabilities(kernel):
    publisher = Requester("digest", "operation", "p", (Requirement("documents", frozenset({"write"})),), publishes=True)
    with pytest.raises(HeinzelError, match="read-only until approvals exist"):
        kernel.handle_for(publisher, "documents")
    with pytest.raises(HeinzelError, match="Unknown capability 'mail'"):
        kernel.handle_for(publisher, "mail")


def test_the_view_says_which_adapter_would_serve_a_requirement_without_building_it(make_kernel):
    Notes.built = 0
    kernel = make_kernel(notes=notes_module(modes=("read",)), config=bind(documents="notes"))
    view = kernel.resolve(CapabilityView)
    assert view.serves(Requirement("documents", frozenset({"read"}))) == ("notes", None)
    assert view.serves(Requirement("documents", frozenset({"write"}))) == ("notes", "notes serves only read, not write")
    assert view.serves(Requirement("tracker", frozenset({"read"}))) == (None, "no adapter bound")
    assert Notes.built == 0


def test_an_unbound_capability_fails_with_the_binding_to_write(make_kernel):
    kernel = make_kernel(notes=notes_module(), digest=digest_module())
    with pytest.raises(
        HeinzelError,
        match=r'no adapter bound. Bind it in your user configuration: \[capabilities\] documents = "notes"',
    ):
        run(kernel, "digest", "show")


def test_a_binding_to_an_unknown_adapter_fails(make_kernel):
    kernel = make_kernel(digest=digest_module(), config=bind(documents="nowhere"))
    with pytest.raises(HeinzelError, match=r"no installed module provides documents as 'nowhere'.*Install a plugin"):
        run(kernel, "digest", "show")
    assert ("documents", "nowhere") in kernel._capabilities.unknown_bindings()


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
    assert kernel._capabilities.matrix()[0].adapter == "acme"


def test_services_get_their_modules_requirements(make_kernel):
    module = Module("reader", "Reads through a service.", requires=["documents:read"])

    class ReaderService:
        def __init__(self, documents: Documents) -> None:
            self.documents = documents

    module.service(ReaderService)

    @module.operation
    def show(reader: ReaderService) -> str:
        """Read through the service."""
        return reader.documents.read("x").title

    kernel = make_kernel(notes=notes_module(), reader=module, config=bind(documents="notes"))
    assert run(kernel, "reader", "show") == "notes:x"


def test_the_kernel_injects_capabilities_so_heads_hide_them(kernel):
    assert kernel.provides(Documents)
    assert kernel.provides(CapabilityView)


def test_the_matrix_lists_every_declared_requirement(kernel):
    rows = [(u.requester, u.kind, u.requirement, u.adapter, u.hosts, u.problem) for u in kernel._capabilities.matrix()]
    assert rows == [
        ("digest", "module", "documents:read", "notes", ("notes.invalid",), None),
        ("digest publish", "operation", "documents:write", "notes", ("notes.invalid",), None),
        ("digest nightly", "job", "documents:read", "notes", ("notes.invalid",), None),
    ]


def test_two_plugins_with_one_adapter_name_make_the_binding_ambiguous(make_kernel):
    other = Module("other", "Provides documents under the same adapter name.")
    other.provides("documents", modes={"read"}, name="notes")(Reader)
    kernel = make_kernel(notes=notes_module(), other=other, digest=digest_module(), config=bind(documents="notes"))
    assert not kernel.errors
    with pytest.raises(HeinzelError, match=r"'notes' is ambiguous: notes \(heinzel-test\) and other \(heinzel-test\)"):
        run(kernel, "digest", "show")


def test_an_adapter_class_must_define_the_methods_of_its_declared_modes(make_kernel):
    module = Module("broken", "Claims to write without a write method.")
    module.provides("documents", modes={"read", "write"})(Reader)
    kernel = make_kernel(broken=module)
    assert kernel.errors["broken"] == "Adapter skipped: adapter broken:documents lacks replace_section of documents"
    assert not kernel._capabilities.adapters


def test_a_read_only_adapter_class_needs_only_the_read_methods(make_kernel):
    module = Module("reader", "Reads only.")
    module.provides("documents", modes={"read"})(Reader)
    kernel = make_kernel(reader=module, digest=digest_module(), config=bind(documents="reader"))
    assert not kernel.errors
    assert run(kernel, "digest", "show") == "reader:weekly"


def test_an_adapter_factory_is_checked_against_its_declared_modes_when_built(make_kernel):
    def make_reader() -> Reader:
        return Reader()

    claims_write = Module("half", "Claims to write through a factory.")
    claims_write.provides("documents", modes={"read", "write"})(make_reader)
    kernel = make_kernel(half=claims_write, digest=digest_module(), config=bind(documents="half"))
    with pytest.raises(HeinzelError, match="Adapter half:documents of half lacks replace_section of documents"):
        run(kernel, "digest", "show")
    reads = Module("half", "Reads through a factory.")
    reads.provides("documents", modes={"read"})(make_reader)
    kernel = make_kernel(half=reads, digest=digest_module(), config=bind(documents="half"))
    assert run(kernel, "digest", "show") == "reader:weekly"


def test_a_builtin_adapter_collision_is_a_bug(settings):
    def adapter_only() -> Module:
        module = Module("notes", "Provides documents without settings.")
        module.provides("documents", modes={"read", "write"})(Notes)
        return module

    records = [ModuleRecord(name, adapter_only(), BUILTIN) for name in ("one", "two")]
    with pytest.raises(HeinzelError, match="Built-in module 'two': another module already provides documents"):
        Kernel(settings, records)


def test_one_function_registered_for_two_operations_is_refused(make_kernel, settings):
    def show() -> None:
        """Show."""

    def twice() -> Module:
        module = Module("twice", "Registers one function twice.")
        module.operation(show)
        module.operation(name="again")(show)
        return module

    kernel = make_kernel(twice=twice())
    assert kernel.errors["twice"] == (
        "Requirement skipped: test_one_function_registered_for_two_operations_is_refused.<locals>.show "
        "is registered for twice show and twice again; a function acts for one only"
    )
    with pytest.raises(HeinzelError, match="Built-in module 'twice'"):
        Kernel(settings, [ModuleRecord("twice", twice(), BUILTIN)])


def test_unknown_binding_keys_are_kept_for_doctor(make_kernel):
    kernel = make_kernel(config=bind(trackr="acme", documents="notes"))
    assert kernel._capabilities.unknown_keys == ("trackr",)
    assert kernel.resolve(CapabilityView).unknown_keys == ("trackr",)


def test_bindings_accept_dashes_and_environment_variables(monkeypatch):
    settings = Config(files=(), data={"capabilities": {"code-host": "acme"}}).settings(
        CapabilitySettings, "capabilities"
    )
    assert settings.bound("code-host") == "acme"
    monkeypatch.setenv("HZ_CAPABILITIES__TRACKER", "acme-tracker")
    assert Config.empty().settings(CapabilitySettings, "capabilities").bound("tracker") == "acme-tracker"


def test_a_project_file_cannot_bind_capabilities(tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    (user / "config.toml").write_text('[capabilities]\ntracker = "mine"\n', encoding="utf-8")
    project = tmp_path / "repo"
    project.mkdir()
    (project / "hz.toml").write_text(
        '[capabilities]\ntracker = "evil"\ncode-host = "evil"\n[example]\nkept = 1\n', encoding="utf-8"
    )
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user))
    config = Config.load(project)
    settings = config.settings(CapabilitySettings, "capabilities")
    assert (settings.bound("tracker"), settings.bound("code-host")) == ("mine", None)
    assert config.project_keys("capabilities", CapabilitySettings.ignored_in_project) == ["code-host", "tracker"]
    assert config.section("example") == {"kept": 1}


class TestInvokeValidatesBeforeInjecting:
    """Kernel.invoke validates what the caller passes, then injects (regressions for the capability change)."""

    def test_an_unexpected_keyword_is_rejected(self, kernel):
        with pytest.raises(HeinzelError, match="Invalid arguments for show: surprise: Unexpected keyword argument"):
            run(kernel, "digest", "show", surprise=1)

    def test_constrained_parameters_are_validated_before_an_adapter_is_built(self, kernel):
        built = Notes.built
        with pytest.raises(HeinzelError, match="Invalid arguments for count: times: Input should be greater"):
            run(kernel, "digest", "count", times=0)
        assert Notes.built == built
        assert run(kernel, "digest", "count", times="2") == 2 * len("notes:weekly")

    def test_an_explicitly_passed_injectable_is_still_validated(self, make_kernel):
        module = Module("explicit", "Takes settings.")
        module.settings(NotesSettings)

        @module.operation
        def prefix(settings: NotesSettings) -> str:
            """Show the prefix."""
            return settings.prefix

        kernel = make_kernel(explicit=module)
        assert run(kernel, "explicit", "prefix") == "notes"
        assert run(kernel, "explicit", "prefix", settings=NotesSettings(prefix="given")) == "given"
        with pytest.raises(HeinzelError, match="Invalid arguments for prefix: settings"):
            run(kernel, "explicit", "prefix", settings="not settings")

    def test_an_explicitly_passed_capability_is_accepted_as_is(self, kernel):
        assert run(kernel, "digest", "show", documents=Reader()) == "reader:weekly"


def test_capabilities_is_kernel_internal():
    assert Capabilities.__module__ == "heinzel.kernel._bindings"
