"""Binding capabilities to adapters and the handles requesters get (ADR 0028), with test-only adapters."""

import inspect
from datetime import UTC, datetime
from typing import Annotated

import pytest
from pydantic import Field, field_validator

from heinzel import (
    Chat,
    Check,
    Config,
    Document,
    Documents,
    HeinzelError,
    JobLet,
    Kernel,
    Meeting,
    Message,
    Module,
    ModuleSettings,
    OperationLet,
    Participant,
    PostedMessage,
    ProviderLet,
    SectionReplacement,
    Tracker,
    Window,
)
from heinzel.kernel import CAPABILITIES, CapabilityView, CompiledJobs, ModuleRecord, Origin, Requirement
from heinzel.kernel._bindings import Adapter, Capabilities, CapabilitySettings, Requester

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


READ_DOCUMENTS = (Requirement("documents", frozenset({"read"})),)


def compiler_module(*, broken: bool = False) -> Module:
    """A module compiling one job from data, like `workflow`."""
    module = Module("contracts", "Compiles jobs from data.")

    def compile_jobs() -> CompiledJobs:
        if broken:
            msg = "a contract file broke the compiler"
            raise RuntimeError(msg)
        job = JobLet(name="digest", func=lambda: None, summary="Digest.", schedule="@daily", requires=READ_DOCUMENTS)
        return CompiledJobs(jobs=(job,))

    module.service(CompiledJobs, compile_jobs)
    return module


@pytest.fixture
def compiling(make_kernel) -> Kernel:
    return make_kernel(
        notes=notes_module(), digest=digest_module(), contracts=compiler_module(), config=bind(documents="notes")
    )


def test_the_kernel_hands_the_compilers_jobs_read_handles_by_capability_name(compiling):
    reader = Requester("contracts", "job", "x", (Requirement("documents", frozenset({"read", "write"})),))
    documents = compiling.handle_for(reader, "documents")
    assert documents.read("x").title == "notes:x"
    with pytest.raises(HeinzelError, match="only publish operations"):
        documents.replace_section("x", "m", "t")


@pytest.mark.parametrize(
    ("requester", "text"),
    [
        (Requester("digest", "job", "x", READ_DOCUMENTS), "only jobs of the module that compiles jobs"),
        (Requester("contracts", "operation", "x", READ_DOCUMENTS), "only jobs of the module that compiles jobs"),
        (Requester("contracts", "job", "x", READ_DOCUMENTS, publishes=True), "read-only until approvals exist"),
        (
            Requester("contracts", "job", "x", (Requirement("documents", frozenset({"read"}), "notes"),)),
            "pins notes:documents:read",
        ),
    ],
)
def test_the_kernel_refuses_handles_to_anyone_but_the_compilers_read_only_jobs(compiling, requester, text):
    with pytest.raises(HeinzelError, match=text):
        compiling.handle_for(requester, "documents")


def test_without_a_compiler_nobody_gets_handles_for_another_requester(kernel):
    with pytest.raises(HeinzelError, match=r"\(none enabled\)"):
        kernel.handle_for(Requester("contracts", "job", "x", READ_DOCUMENTS), "documents")
    with pytest.raises(HeinzelError, match="Unknown capability 'spreadsheets'"):
        kernel.handle_for(Requester("contracts", "job", "x", READ_DOCUMENTS), "spreadsheets")
    assert kernel.compiled_jobs() == []


def test_compiled_jobs_are_compiled_afresh_and_appear_in_the_matrix(compiling):
    first, second = compiling.compiled_jobs(), compiling.compiled_jobs()
    assert [(module, let.name) for module, let in first] == [("contracts", "digest")]
    assert first[0][1] is not second[0][1]  # nothing cached: data may change while a process runs
    rows = [(row.requester, row.requirement, row.adapter) for row in compiling.resolve(CapabilityView).matrix()]
    assert ("contracts digest", "documents:read", "notes") in rows


def test_a_broken_compiler_leaves_its_jobs_out_of_the_matrix(make_kernel):
    kernel = make_kernel(digest=digest_module(), contracts=compiler_module(broken=True), config=bind())
    assert not any(row.requester.startswith("contracts") for row in kernel.resolve(CapabilityView).matrix())
    with pytest.raises(RuntimeError, match="broke the compiler"):
        kernel.compiled_jobs()


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
    fix = r"Bind an installed adapter \(notes\): hz system config set capabilities.documents notes$"
    with pytest.raises(HeinzelError, match=rf"no adapter bound. {fix}"):
        run(kernel, "digest", "show")


def test_a_binding_to_an_unknown_adapter_fails(make_kernel):
    kernel = make_kernel(digest=digest_module(), config=bind(documents="nowhere"))
    with pytest.raises(
        HeinzelError, match=r"provides documents as 'nowhere'.*: hz system plugin search --text documents$"
    ):
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


class Talk:
    """A test-only chat adapter keeping messages in memory."""

    def __init__(self) -> None:
        self.posted: list[tuple[str, str, str | None]] = []

    def messages(self, channel: str, window: Window) -> list[Message]:
        author = Participant(name="Mira", email="mira@example.com")
        return [Message(channel="C1", id="1.0", author=author, posted_at=window.since, text="hi")]

    def post(self, channel: str, text: str, thread: str | None = None) -> PostedMessage:
        self.posted.append((channel, text, thread))
        return PostedMessage(channel="C1", id="2.0", thread=thread)


def announcer_module() -> Module:
    module = Module("announcer", "Reads a channel and posts to it.", requires=["chat:read"])

    @module.operation
    def latest(chat: Chat) -> str:
        """The latest message of the channel."""
        return chat.messages("#team", WINDOW)[-1].text

    @module.operation
    def sneak(chat: Chat) -> str:
        """Try to post from a read-only operation."""
        return chat.post("#team", "sneaky").id

    @module.operation(read_only=False, requires=["chat:write"])
    def announce(chat: Chat) -> str:
        """Post the announcement."""
        return chat.post("#team", "shipped", thread="1.0").id

    return module


@pytest.fixture
def talking(make_kernel) -> Kernel:
    module = Module("talk", "Test-only chat adapter.")
    module.provides("chat", modes={"read", "write"}, hosts=["talk.invalid"])(Talk)
    return make_kernel(talk=module, announcer=announcer_module(), config=bind(chat="talk"))


def test_chat_reads_through_the_bound_adapter(talking):
    assert run(talking, "announcer", "latest") == "hi"


def test_posting_from_a_read_declaration_fails(talking):
    with pytest.raises(HeinzelError, match=r"announcer sneak can't call chat.post, which needs write"):
        run(talking, "announcer", "sneak")


def test_a_publish_operation_that_declared_chat_write_posts(talking):
    assert run(talking, "announcer", "announce") == "2.0"
    adapter = talking._capabilities._instances["chat", "talk"]
    assert isinstance(adapter, Talk)
    assert adapter.posted == [("#team", "shipped", "1.0")]


@pytest.mark.parametrize(
    ("capability", "method"), [("meetings", "transcripts"), ("calendar", "events"), ("chat", "messages")]
)
def test_an_adapter_class_must_define_every_read_method_of_the_new_capabilities(make_kernel, capability, method):
    class Partial:
        def ended(self, window: Window) -> list[Meeting]:
            return []

        def transcript(self, meeting: str) -> None:
            return None

        def post(self, channel: str, text: str, thread: str | None = None) -> None:
            return None

    module = Module("partial", "Lacks a read method.")
    module.provides(capability, modes={"read"})(Partial)
    kernel = make_kernel(partial=module)
    assert kernel.errors["partial"] == f"Adapter skipped: adapter partial:{capability} lacks {method} of {capability}"


def test_capabilities_is_kernel_internal():
    assert Capabilities.__module__ == "heinzel.kernel._bindings"


class AcmeSettings(ModuleSettings):
    api_url: str = "https://api.acme.invalid"


class StrictSettings(ModuleSettings):
    api_url: str = "https://api.acme.invalid"

    @field_validator("api_url")
    @classmethod
    def _https(cls, value: str) -> str:
        if not value.startswith("https://"):
            msg = "api_url must be https"
            raise ValueError(msg)
        return value


def acme_module(hosts: object, *, settings: type[ModuleSettings] | None = AcmeSettings) -> Module:
    module = Module("acme", "Provides documents where its settings say.")
    if settings is not None:
        module.settings(settings)
    module.provides("documents", modes={"read"}, hosts=hosts)(Reader)  # type: ignore[arg-type]
    return module


def acme_hosts(make_kernel, hosts: object, **acme: object) -> tuple[tuple[str, ...], str | None]:
    kernel = make_kernel(acme=acme_module(hosts), config=Config(files=(), data={"acme": acme}))
    view = kernel.resolve(CapabilityView)
    return view.hosts(view.adapters[0])


def url(settings: AcmeSettings) -> object:
    return [settings.api_url]


def one_string(settings: AcmeSettings) -> object:
    return settings.api_url


def duplicates(settings: AcmeSettings) -> object:
    return [settings.api_url, "API.acme.invalid", "uploads.acme.invalid"]


def failing(settings: AcmeSettings) -> object:
    msg = f"leaks {settings.api_url}"
    raise RuntimeError(msg)


def not_iterable(settings: AcmeSettings) -> object:
    return 42 if settings else None


def not_hosts(settings: AcmeSettings) -> object:
    return [settings.api_url.replace("acme.invalid", ""), 7, "ok.invalid"]


def nothing(settings: AcmeSettings) -> object:
    return [] if settings else None


def test_static_hosts_are_shown_as_declared(kernel):
    view = kernel.resolve(CapabilityView)
    assert view.hosts(view.adapters[0]) == (("notes.invalid",), None)


def test_hosts_from_settings_are_host_names_read_from_the_effective_settings(make_kernel):
    found = acme_hosts(make_kernel, url, api_url="https://bot:t0ken@GHE.example.invalid:8443/api/v3")
    assert found == (("ghe.example.invalid",), None)


def test_a_lone_string_is_one_host_and_duplicates_are_shown_once(make_kernel):
    assert acme_hosts(make_kernel, one_string) == (("api.acme.invalid",), None)
    assert acme_hosts(make_kernel, duplicates) == (("api.acme.invalid", "uploads.acme.invalid"), None)


def test_hosts_from_settings_appear_in_the_matrix_and_are_read_once(make_kernel):
    calls: list[str] = []

    def counted(settings: AcmeSettings) -> object:
        calls.append(settings.api_url)
        return [settings.api_url]

    kernel = make_kernel(acme=acme_module(counted), digest=digest_module(), config=bind(documents="acme"))
    kernel._capabilities.matrix()
    assert {row.hosts for row in kernel._capabilities.matrix() if row.adapter == "acme"} == {("api.acme.invalid",)}
    assert calls == ["https://api.acme.invalid"]


@pytest.mark.parametrize(
    ("hosts", "problem"),
    [
        (failing, "the hosts of acme:documents come from its settings, which couldn't be read (RuntimeError)"),
        (not_iterable, "the hosts of acme:documents come from its settings, which couldn't be read (TypeError)"),
        (
            not_hosts,
            "the hosts of acme:documents come from its settings, but value 1, 2 of 3 is not a host name or URL",
        ),
        (nothing, "the hosts of acme:documents come from its settings, which name no host"),
    ],
)
def test_hosts_that_cant_be_read_are_unknown_and_the_problem_repeats_no_value(make_kernel, hosts, problem):
    assert acme_hosts(make_kernel, hosts, api_url="https://s3cret@acme.invalid") == ((), problem)


def test_reading_hosts_logs_no_value_and_no_message(make_kernel, caplog):
    acme_hosts(make_kernel, failing, api_url="https://s3cret@acme.invalid")
    assert [record.getMessage() for record in caplog.records] == [
        "Reading the hosts of adapter acme:documents through failing failed: RuntimeError"
    ]
    assert not any(record.exc_info for record in caplog.records)


def test_two_adapters_with_one_name_keep_their_own_hosts(make_kernel):
    class OtherSettings(ModuleSettings):
        api_url: str = "https://other.acme.invalid"

    def other(settings: OtherSettings) -> object:
        return [settings.api_url]

    kernel = make_kernel(acme=acme_module(url), acme2=acme_module(other, settings=OtherSettings))
    assert not kernel.errors
    view = kernel.resolve(CapabilityView)
    assert sorted(view.hosts(adapter)[0] for adapter in view.adapters) == [
        ("api.acme.invalid",),
        ("other.acme.invalid",),
    ]


def test_settings_that_fail_validation_make_the_hosts_unknown(make_kernel):
    def strict(settings: StrictSettings) -> object:
        return [settings.api_url]

    kernel = make_kernel(
        acme=acme_module(strict, settings=StrictSettings),
        config=Config(files=(), data={"acme": {"api_url": "http://s3cret@acme.invalid"}}),
    )
    view = kernel.resolve(CapabilityView)
    assert view.hosts(view.adapters[0]) == (
        (),
        "the hosts of acme:documents come from its settings, which couldn't be read (ValidationError)",
    )


def test_without_a_settings_resolver_hosts_from_settings_are_unknown():
    let = next(let for let in acme_module(url).lets if isinstance(let, ProviderLet))
    capabilities = Capabilities([Adapter("acme", "heinzel-test", let)], [], {}, CapabilitySettings(), build=print)
    assert capabilities.hosts_of(capabilities.view.adapters[0]) == (
        (),
        "the hosts of acme:documents come from its settings, which nothing provides",
    )


def test_hosts_may_read_only_their_own_modules_settings(make_kernel, settings):
    kernel = make_kernel(acme=acme_module(url, settings=None))
    assert kernel.errors["acme"] == (
        "Adapter skipped: the hosts of adapter acme:documents read settings acme doesn't declare: AcmeSettings"
    )
    assert not kernel._capabilities.adapters
    with pytest.raises(HeinzelError, match="Built-in module 'acme': the hosts of adapter acme:documents read"):
        Kernel(settings, [ModuleRecord("acme", acme_module(url, settings=None), BUILTIN)])


def test_a_handle_builds_its_adapter_on_the_first_call_not_when_injected(make_kernel):
    Notes.built = 0
    kernel = make_kernel(notes=notes_module(), digest=digest_module(), config=bind(documents="notes"))
    documents = handle(kernel)
    assert Notes.built == 0
    assert documents.read("weekly").title == "notes:weekly"  # type: ignore[attr-defined]
    assert documents.read("other").title == "notes:other"  # type: ignore[attr-defined]
    assert Notes.built == 1


class Layer:
    """A tracker adapter on top of documents, the way `system.directory` builds on calendar."""

    def __init__(self, documents: Documents) -> None:
        self.documents = documents

    def issues_updated(self, project: str, window: Window) -> list[object]:
        return []


class Top:
    """A chat adapter on top of the tracker."""

    def __init__(self, tracker: Tracker) -> None:
        self.tracker = tracker

    def messages(self, channel: str, window: Window) -> list[Message]:
        return []


def layered(*, with_operation: bool = False) -> Module:
    module = Module("layer", "Issues from documents.", requires=["documents:read"])
    module.provides("tracker", modes={"read"}, hosts=[])(Layer)
    if with_operation:

        @module.operation
        def peek(documents: Documents) -> str:
            """Read through the module's own requirement."""
            return documents.read("x").ref

    return module


def needs(kernel: Kernel) -> set[str]:
    return {f"{use.requester} {use.requirement}" for use in kernel._capabilities.matrix()}


def test_an_adapter_only_modules_needs_count_only_while_its_adapter_serves_someone(make_kernel):
    assert needs(make_kernel(layer=layered(), config=bind(tracker="layer"))) == set()
    tracked = Module("issues", "Reads issues.", requires=["tracker:read"])
    used = needs(make_kernel(layer=layered(), issues=tracked, config=bind(tracker="layer")))
    assert used == {"issues tracker:read", "layer documents:read"}
    unused = needs(make_kernel(layer=layered(), issues=tracked, config=bind(tracker="other")))
    assert unused == {"issues tracker:read"}


def test_a_module_that_also_has_operations_keeps_its_needs(make_kernel):
    assert needs(make_kernel(layer=layered(with_operation=True))) == {"layer documents:read"}


class Service:
    """A service that could use its module's requirements."""


def peek(documents: Documents) -> list[Check]:
    """Read through the module's requirement."""
    return []


def peek_info(documents: Documents) -> dict[str, str]:
    """Read through the module's requirement."""
    return {}


def peek_setup(documents: Documents) -> None:
    """Set nothing up."""


@pytest.mark.parametrize(
    "add",
    [
        lambda m: m.job(schedule="@daily")(peek),
        lambda m: m.service(Service),
        lambda m: m.info(peek_info),
        lambda m: m.health(peek),
        lambda m: m.setup(peek_setup),
    ],
    ids=["job", "service", "info", "health", "setup"],
)
def test_any_let_that_runs_code_keeps_a_modules_needs(make_kernel, add):
    module = layered()
    add(module)
    assert needs(make_kernel(layer=module)) == {"layer documents:read"}


def test_a_module_whose_one_adapter_of_two_serves_someone_keeps_its_needs(make_kernel):
    module = layered()
    module.provides("chat", modes={"read"}, hosts=[])(Top)
    reader = Module("reader", "Reads chat.", requires=["chat:read"])
    config = bind(chat="layer", documents="notes")
    kernel = make_kernel(layer=module, reader=reader, notes=notes_module(), config=config)
    assert needs(kernel) == {"reader chat:read", "layer documents:read"}


def test_a_readers_adapter_that_cant_be_built_fails_on_the_first_call_and_is_retried(make_kernel):
    attempts = []

    def flaky() -> Reader:
        attempts.append(1)
        if len(attempts) == 1:
            msg = "not yet"
            raise HeinzelError(msg)
        return Reader()

    reads = Module("half", "Reads through a factory that fails once.")
    reads.provides("documents", modes={"read"})(flaky)
    kernel = make_kernel(half=reads, digest=digest_module(), config=bind(documents="half"))
    documents = handle(kernel)
    assert attempts == []
    with pytest.raises(HeinzelError, match="not yet"):
        documents.read("weekly")  # type: ignore[attr-defined]
    assert documents.read("weekly").title == "reader:weekly"  # type: ignore[attr-defined]


def test_a_publish_operations_adapter_is_built_before_it_runs(make_kernel):
    def broken() -> Notes:
        msg = "credentials missing"
        raise HeinzelError(msg)

    writes = Module("notes", "Writes through a factory that fails.")
    writes.provides("documents", modes={"read", "write"})(broken)
    kernel = make_kernel(notes=writes, digest=digest_module(), config=bind(documents="notes"))
    with pytest.raises(HeinzelError, match="credentials missing"):
        handle(kernel, "publish")


def test_needs_of_a_chain_of_unused_adapter_modules_all_drop_out(make_kernel):
    top = Module("top", "Chat from issues.", requires=["tracker:read"])
    top.provides("chat", modes={"read"}, hosts=[])(Top)
    kernel = make_kernel(layer=layered(), top=top, config=bind(tracker="layer", chat="top"))
    assert needs(kernel) == set()
    reader = Module("reader", "Reads chat.", requires=["chat:read"])
    kernel = make_kernel(layer=layered(), top=top, reader=reader, config=bind(tracker="layer", chat="top"))
    assert needs(kernel) == {"reader chat:read", "top tracker:read", "layer documents:read"}


def test_a_binding_is_defaulted_only_while_no_file_or_variable_sets_it(make_kernel, monkeypatch):
    view = make_kernel(config=bind(documents="notes")).resolve(CapabilityView)
    assert view.defaulted("directory")
    assert view.defaulted("agent-runner")
    assert not view.defaulted("documents")  # no default at all
    assert not view.defaulted("tracker")
    explicit = make_kernel(config=bind(directory="one-on-ones", agent_runner="claude-code")).resolve(CapabilityView)
    assert not explicit.defaulted("directory")
    assert not explicit.defaulted("agent-runner")
    monkeypatch.setenv("HZ_CAPABILITIES__DIRECTORY", "one-on-ones")
    assert not make_kernel().resolve(CapabilityView).defaulted("directory")
