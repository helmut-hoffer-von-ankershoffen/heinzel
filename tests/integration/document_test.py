"""`hz document read` and `search` through the CLI with two documents connectors on (ADR 0161)."""

import json

import pytest
from typer.testing import CliRunner

from heinzel import Config, Document, DocumentHit, HeinzelError, Kernel, Module
from heinzel.heads._exposure import exposed
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration


class Drive:
    """A test-only drive-like connector: Docs-style links."""

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title="Roadmap", text="drive text", url=ref)

    def search(self, query: str, limit: int) -> list[DocumentHit]:
        return [DocumentHit(ref="https://docs.example.test/d/1", title=f"Drive {query}")][:limit]


class Wiki:
    """A test-only wiki-like connector: page links on its site, numeric page ids."""

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title="Team page", text="wiki text")

    def search(self, query: str, limit: int) -> list[DocumentHit]:
        return [DocumentHit(ref="4242", title=f"Wiki {query}", url="https://wiki.example.test/pages/4242")][:limit]


class Down:
    """A test-only connector whose search is down."""

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title="", text="")

    def search(self, query: str, limit: int) -> list[DocumentHit]:
        msg = "The archive is down for maintenance"
        raise HeinzelError(msg)


def stores() -> Module:
    module = Module("stores", "Test-only documents connectors.")
    module.provides(
        "documents", modes={"read"}, hosts=["api.docs.example.test"], links=["docs.example.test"], name="drive"
    )(Drive)
    module.provides("documents", modes={"read"}, hosts=["wiki.example.test"], name="wiki")(Wiki)
    module.provides("documents", modes={"read"}, hosts=["archive.example.test"], name="down")(Down)
    module.provides("documents", modes={"read"}, links=["docs.example.test"], name="clone")(Wiki)
    return module


def kernel_with(make_kernel, documents: object) -> Kernel:
    config = Config(files=(), data={"capabilities": {"documents": documents}})
    return make_kernel(builtins=True, stores=stores(), config=config)


def hz(kernel: Kernel, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def test_links_open_in_the_connector_they_belong_to(make_kernel):
    kernel = kernel_with(make_kernel, ["drive", "wiki"])
    code, drive = hz(kernel, "document", "read", "https://docs.example.test/d/1")
    assert code == 0
    assert (drive["title"], drive["source"]) == ("Roadmap", "drive")
    code, wiki = hz(kernel, "document", "read", "https://wiki.example.test/pages/4242")
    assert code == 0
    assert (wiki["title"], wiki["source"]) == ("Team page", "wiki")


def test_search_lists_both_connectors_results_labelled_and_reports_one_that_is_down(make_kernel):
    kernel = kernel_with(make_kernel, "drive,wiki,down")
    code, found = hz(kernel, "document", "search", "plan")
    assert code == 0
    assert [(hit["source"], hit["title"]) for hit in found["hits"]] == [("drive", "Drive plan"), ("wiki", "Wiki plan")]
    assert found["failures"] == [{"source": "down", "reason": "The archive is down for maintenance"}]


def test_an_id_without_a_link_names_the_choices(make_kernel):
    kernel = kernel_with(make_kernel, ["drive", "wiki"])
    result = CliRunner().invoke(build(kernel), ["document", "read", "4242"])
    assert result.exit_code == 1
    assert "could be in drive or wiki: pass its link, or name the connector, e.g. drive:4242" in " ".join(
        result.output.split()
    )
    code, page = hz(kernel, "document", "read", "wiki:4242")
    assert code == 0
    assert page["source"] == "wiki"


def test_one_connector_works_as_before(make_kernel):
    kernel = kernel_with(make_kernel, "wiki")
    code, page = hz(kernel, "document", "read", "4242")
    assert code == 0
    assert (page["title"], page["source"]) == ("Team page", "wiki")


def test_without_a_documents_connector_the_commands_say_how_to_get_one(make_kernel):
    kernel = make_kernel(builtins=True)
    result = CliRunner().invoke(build(kernel), ["document", "search", "plan"])
    assert result.exit_code == 1
    assert "hz system plugin search --text documents" in " ".join(result.output.split())


def test_documents_are_on_the_cli_mcp_and_web_app_but_not_the_open_rest_head(make_kernel):
    kernel = kernel_with(make_kernel, ["drive", "wiki"])
    for head in ("cli", "mcp", "web"):
        assert {"document_read", "document_search"} <= {item.name for item in exposed(kernel, head)}, head
    assert not {"document_read", "document_search"} & {item.name for item in exposed(kernel, "api")}


@pytest.mark.parametrize("value", ['["drive", "wiki"]', "drive,wiki"])
def test_config_set_switches_several_documents_connectors_on(make_kernel, value):
    kernel = kernel_with(make_kernel, None)
    result = CliRunner().invoke(build(kernel), ["system", "config", "set", "capabilities.documents", value])
    assert result.exit_code == 0, result.output
    reloaded = make_kernel(builtins=True, stores=stores(), config=Config.load())
    assert reloaded.capability_settings.bound_all("documents") == ("drive", "wiki")


def test_doctor_warns_when_connectors_that_are_on_claim_the_same_link_hosts(make_kernel):
    code, report = hz(kernel_with(make_kernel, ["drive", "clone", "wiki"]), "system", "doctor")
    assert code == 0
    [check] = [c for c in report["checks"]["system.capability"] if c["name"] == "links documents"]
    assert check["status"] == "degraded"
    assert "docs.example.test: clone and drive" in check["detail"]
    code, report = hz(kernel_with(make_kernel, ["drive", "wiki"]), "system", "doctor")
    assert not [c for c in report["checks"]["system.capability"] if c["name"] == "links documents"]
