"""Several adapters serving one capability at once (ADR 0161): routing, fan-out, writes, one binding as before."""

import threading
from datetime import UTC, datetime

import pytest

from heinzel import (
    Config,
    Document,
    DocumentHit,
    Documents,
    Gathered,
    HeinzelError,
    Kernel,
    Module,
    ModuleSettings,
    OperationLet,
    SectionReplacement,
)
from heinzel.kernel import CAPABILITIES, Requirement
from heinzel.kernel._bindings import CapabilitySettings

pytestmark = pytest.mark.unit

RELEASE = threading.Event()
FAILURES: list[tuple[str, str]] = []


class Drive:
    """A test-only drive-like adapter: links on drive.example.test, ids it gave out, writes."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title=f"drive:{ref}", text="drive text", url=f"https://drive.example.test/d/{ref}")

    def replace_section(self, ref: str, marker: str, text: str) -> SectionReplacement:
        self.written.append(ref)
        return SectionReplacement(ref=ref, marker=marker)

    def search(self, query: str, limit: int) -> list[DocumentHit]:
        return [DocumentHit(ref=f"d{n}", title=f"Drive {query} {n}") for n in range(1, 4)][:limit]


class Wiki:
    """A test-only wiki-like adapter that reads and searches, but can't write."""

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title=f"wiki:{ref}", text="wiki text")

    def search(self, query: str, limit: int) -> list[DocumentHit]:
        return [DocumentHit(ref=f"https://wiki.example.test/p/{n}", title=f"Wiki {query} {n}") for n in (1, 2)][:limit]


class Broken:
    """A test-only adapter whose search fails with a message for people."""

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title=f"broken:{ref}", text="")

    def search(self, query: str, limit: int) -> list[DocumentHit]:
        msg = "The archive is offline"
        raise HeinzelError(msg)


class Crashing:
    """A test-only adapter whose search crashes with a message that must never reach people."""

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title="", text="")

    def search(self, query: str, limit: int) -> list[DocumentHit]:
        msg = "https://user:secret@archive.example.test failed"
        raise RuntimeError(msg)


class Hanging:
    """A test-only adapter whose search never answers until the test releases it."""

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title="", text="")

    def search(self, query: str, limit: int) -> list[DocumentHit]:
        RELEASE.wait(5)
        return []


class Plain:
    """A test-only adapter without the optional search."""

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title=f"plain:{ref}", text="")


class WikiSettings(ModuleSettings):
    site: str = "https://wiki.example.test"


def links_of(settings: WikiSettings) -> list[str]:
    return [settings.site]


def adapters() -> Module:
    module = Module("stores", "Provides several documents adapters for tests.")
    module.settings(WikiSettings)
    module.provides(
        "documents",
        modes={"read", "write"},
        hosts=["api.drive.example.test"],
        links=["drive.example.test"],
        name="drive",
    )(Drive)
    module.provides("documents", modes={"read"}, hosts=["api.wiki.example.test"], links=links_of, name="wiki")(Wiki)
    module.provides("documents", modes={"read"}, hosts=["broken.example.test"], name="broken")(Broken)
    module.provides("documents", modes={"read"}, hosts=["crash.example.test"], name="crashing")(Crashing)
    module.provides("documents", modes={"read"}, hosts=["hang.example.test"], name="hanging")(Hanging)
    module.provides("documents", modes={"read"}, hosts=["drive.example.test"], name="plain")(Plain)
    return module


def users() -> Module:
    module = Module("desk", "Reads, searches and writes documents.", requires=["documents:read"])

    @module.operation
    def show(documents: Documents, ref: str) -> Document:
        """Read one document."""
        return documents.read(ref)

    @module.operation
    def find(documents: Documents, query: str, limit: int = 10) -> list[DocumentHit]:
        """Search every documents connector."""
        found = documents.search(query, limit)
        assert isinstance(found, Gathered)
        FAILURES[:] = [(failure.source, failure.reason) for failure in found.failures]
        return list(found)

    @module.operation(read_only=False, requires=["documents:read,write"])
    def publish(documents: Documents, ref: str) -> str:
        """Write a section."""
        return documents.replace_section(ref, "summary", "new").ref

    return module


def make(make_kernel, documents: object) -> Kernel:
    return make_kernel(
        stores=adapters(), desk=users(), config=Config(files=(), data={"capabilities": {"documents": documents}})
    )


def call(kernel: Kernel, name: str, **kwargs: object) -> object:
    let = next(let for record, let in kernel.lets(OperationLet) if record.qualname == "desk" and let.name == name)
    return kernel.invoke(let, **kwargs)


def failures(_kernel: Kernel) -> list[tuple[str, str]]:
    return list(FAILURES)


@pytest.fixture
def two(make_kernel) -> Kernel:
    return make(make_kernel, ["drive", "wiki"])


def test_a_link_goes_to_the_adapter_whose_links_match_its_host(two):
    drive = call(two, "show", ref="https://drive.example.test/d/abc")
    wiki = call(two, "show", ref="https://wiki.example.test/p/7")
    assert (drive.title, drive.source) == ("drive:https://drive.example.test/d/abc", "drive")
    assert (wiki.title, wiki.source) == ("wiki:https://wiki.example.test/p/7", "wiki")


def test_without_links_the_hosts_route_and_an_unknown_host_names_the_choices(make_kernel):
    kernel = make(make_kernel, ["wiki", "plain"])
    assert call(kernel, "show", ref="https://drive.example.test/x").source == "plain"
    with pytest.raises(HeinzelError, match=r"No documents connector .* opens links on elsewhere\.test") as caught:
        call(kernel, "show", ref="https://elsewhere.test/doc")
    assert "wiki opens wiki.example.test" in str(caught.value)
    assert "plain opens drive.example.test" in str(caught.value)


def test_overlapping_links_are_refused_naming_both(make_kernel):
    kernel = make(make_kernel, ["drive", "plain"])
    with pytest.raises(HeinzelError, match=r"could belong to drive or plain"):
        call(kernel, "show", ref="https://drive.example.test/d/1")


def test_a_bare_id_is_ambiguous_until_an_adapter_gave_it_out(two):
    with pytest.raises(HeinzelError, match=r"'d2' could be in drive or wiki: pass its link, or name the connector"):
        call(two, "show", ref="d2")
    call(two, "find", query="plan")
    assert call(two, "show", ref="d2").source == "drive"


def test_the_adapter_prefix_names_the_adapter_and_is_stripped(two):
    document = call(two, "show", ref="wiki:12345")
    assert (document.title, document.source) == ("wiki:12345", "wiki")


def test_search_fans_out_merges_in_turns_and_labels_each_result(two):
    found = call(two, "find", query="plan", limit=10)
    assert [(hit.source, hit.title) for hit in found] == [
        ("drive", "Drive plan 1"),
        ("wiki", "Wiki plan 1"),
        ("drive", "Drive plan 2"),
        ("wiki", "Wiki plan 2"),
        ("drive", "Drive plan 3"),
    ]
    assert failures(two) == []
    assert len(call(two, "find", query="plan", limit=3)) == 3


def test_a_failing_adapter_is_reported_next_to_the_results(make_kernel):
    kernel = make(make_kernel, ["drive", "broken", "crashing", "plain"])
    found = call(kernel, "find", query="plan")
    assert {hit.source for hit in found} == {"drive"}
    assert failures(kernel) == [
        ("broken", "The archive is offline"),
        ("crashing", "failed (RuntimeError)"),
        ("plain", "can't search"),
    ]


def test_a_hanging_adapter_times_out_without_holding_the_others(make_kernel):
    kernel = make(make_kernel, ["hanging", "wiki"])
    kernel._capabilities.fan_out_timeout = 0.2
    RELEASE.clear()
    try:
        started = datetime.now(UTC)
        found = call(kernel, "find", query="plan")
        assert (datetime.now(UTC) - started).total_seconds() < 3
    finally:
        RELEASE.set()
    assert [hit.source for hit in found] == ["wiki", "wiki"]
    assert failures(kernel) == [("hanging", "didn't answer within 0.2 s")]


def test_when_every_adapter_fails_the_search_fails_with_every_reason(make_kernel):
    kernel = make(make_kernel, ["broken", "plain"])
    with pytest.raises(
        HeinzelError, match=r"No documents connector could search: broken: The archive is offline; plain: can't search"
    ):
        call(kernel, "find", query="plan")


def test_a_write_goes_to_the_adapter_that_owns_the_document(two):
    assert call(two, "publish", ref="https://drive.example.test/d/abc") == "https://drive.example.test/d/abc"
    drive = two._capabilities._instances["documents", "drive"]
    assert drive.written == ["https://drive.example.test/d/abc"]


def test_a_write_to_a_read_only_adapter_says_it_cant(two):
    with pytest.raises(HeinzelError, match=r"wiki serves documents only to read; it can't replace_section"):
        call(two, "publish", ref="https://wiki.example.test/p/1")


def test_a_read_only_operation_still_cant_write_through_several(two):
    handle = two._capabilities.handle(
        CAPABILITIES["documents"],
        next(let for record, let in two.lets(OperationLet) if let.name == "show").func,
    )
    with pytest.raises(HeinzelError, match=r"needs write"):
        handle.replace_section("https://drive.example.test/d/1", "m", "t")


def test_one_bound_adapter_behaves_as_before(make_kernel):
    kernel = make(make_kernel, "wiki")
    assert kernel._capabilities.bindings("documents") == (("wiki",), "configured")
    document = call(kernel, "show", ref="anything")
    assert (document.title, document.source) == ("wiki:anything", None)
    assert [hit.source for hit in call(kernel, "find", query="q")] == ["wiki", "wiki"]


def test_a_single_adapter_without_search_says_it_cant(make_kernel):
    kernel = make(make_kernel, ["plain"])
    with pytest.raises(HeinzelError, match=r"This documents connector can't search"):
        call(kernel, "find", query="q")


def test_the_route_names_the_adapter_or_none_for_the_default(two):
    assert two.capability_route("documents", "read", {"ref": "https://wiki.example.test/p/1"}) == "wiki"
    assert two.capability_route("documents", "search", {"query": "x"}) is None
    assert two.capability_route("calendar", "events", {}) is None
    assert two.selected_capability_provider("documents")[1].adapter == "drive"
    assert two.selected_capability_provider("documents", "wiki")[1].adapter == "wiki"
    with pytest.raises(HeinzelError, match="unavailable"):
        two.selected_capability_provider("documents", "plain")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("google", ("google",)),
        ("google,atlassian", ("google", "atlassian")),
        (["google", "atlassian", "google"], ("google", "atlassian")),
        ([], ()),
        (["none"], ()),
        (None, ()),
    ],
)
def test_the_documents_binding_takes_one_name_a_list_or_commas(value, expected):
    settings = CapabilitySettings.model_validate({"documents": value})
    assert settings.bound_all("documents") == expected
    assert settings.bound("documents") == (expected[0] if expected else None)


def test_none_cant_be_listed_with_adapters_and_other_capabilities_take_one():
    with pytest.raises(ValueError, match="binds nothing"):
        CapabilitySettings.model_validate({"documents": ["none", "google"]})
    with pytest.raises(ValueError, match="string"):
        CapabilitySettings.model_validate({"calendar": ["google", "work"]})


def test_the_schema_of_documents_offers_several_switches():
    schema = CapabilitySettings.model_json_schema()["properties"]
    assert schema["documents"]["x-options"]["multiple"] is True
    assert "multiple" not in schema["calendar"]["x-options"]


def test_unknown_names_in_a_list_are_reported(make_kernel):
    kernel = make(make_kernel, ["drive", "gone"])
    assert ("documents", "gone") in kernel._capabilities.unknown_bindings()
    assert ("documents", "drive") not in kernel._capabilities.unknown_bindings()


def test_links_must_be_host_names_and_not_given_twice():
    module = Module("bad", "Declares broken links.")
    with pytest.raises(ValueError, match="links must be host names"):
        module.provides("documents", modes={"read"}, links=["Not A Host!"])(Plain)
    module.settings(WikiSettings)
    from heinzel.kernel._lets import ProviderLet

    with pytest.raises(ValueError, match="not both"):
        ProviderLet(
            name="x:documents",
            adapter="x",
            capability="documents",
            modes=frozenset({"read"}),
            factory=Plain,
            links=("a.test",),
            configured_links=links_of,
        )


def test_the_view_reports_every_binding_and_the_links(two):
    view = two._capabilities.view
    assert view.bindings("documents") == (("drive", "wiki"), "configured")
    wiki = next(adapter for adapter in view.adapters if adapter.let.adapter == "wiki")
    assert view.links(wiki) == ("wiki.example.test",)


def test_requirement_pins_still_use_one_adapter(two):
    assert two._capabilities.adapter_for(Requirement("documents", frozenset({"read"}), "wiki"))[0].let.adapter == "wiki"


def test_only_the_newest_ids_are_remembered(two, monkeypatch):
    from heinzel.kernel import _bindings

    monkeypatch.setattr(_bindings, "_ISSUED", 2)
    two._capabilities.remember("documents", ["a", "b", None, "c"], "wiki")
    assert list(two._capabilities._issued) == [("documents", "b"), ("documents", "c")]
    two._capabilities.remember("documents", ["b"], "drive")
    assert two._capabilities._issued == {("documents", "c"): "wiki", ("documents", "b"): "drive"}
