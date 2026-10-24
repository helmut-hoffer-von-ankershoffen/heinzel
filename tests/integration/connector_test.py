"""`hz system connector` through the CLI: offline report, live test on demand, never in doctor (ADR 0056)."""

import json

import pytest
from typer.testing import CliRunner

from heinzel import Check, Config, Document, Kernel, Login, Module, Status
from heinzel.heads import api, web
from heinzel.heads._exposure import exposed
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

CALLS: list[str] = []


class Wiki:
    def read(self, ref: str) -> Document:
        return Document(ref=ref, title=ref, text="body")


def wiki(*, signed_in: bool) -> Module:
    module = Module("wiki", "Reads the Wiki for tests.")
    module.provides("documents", modes={"read"}, hosts=["wiki.invalid"])(Wiki)

    @module.health
    def health() -> list[Check]:
        login = Login(signed_in=signed_in, account="ada" if signed_in else "", command="wiki login")
        status = Status.UP if signed_in else Status.DEGRADED
        return [Check(name="wiki", status=status, detail="checked locally", fix="wiki login", login=login)]

    @module.health(live=True)
    def live() -> list[Check]:
        CALLS.append("live")
        return [Check(name="whoami", status=Status.UP, detail="ada", login=Login(signed_in=True, account="ada"))]

    return module


def hz(kernel: Kernel, *args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    return result.exit_code, json.loads(result.output)


def test_list_reports_the_connector_and_doctor_never_runs_the_live_test(make_kernel):
    CALLS.clear()
    kernel = make_kernel(builtins=True, wiki=wiki(signed_in=False))
    code, report = hz(kernel, "system", "connector", "list")
    assert code == 0
    connector = next(c for c in report["connectors"] if c["name"] == "wiki")
    assert (connector["state"], connector["connect"], connector["testable"]) == ("not-signed-in", ["wiki login"], True)
    documents = next(c for c in report["capabilities"] if c["capability"] == "documents")
    assert (documents["bound"], documents["how"]) == ("wiki", "automatic")
    code, doctor = hz(kernel, "system", "doctor")
    assert [c["name"] for c in doctor["checks"]["wiki"]] == ["wiki"]
    assert CALLS == []


def test_test_runs_the_live_check_and_a_none_binding_turns_it_off(make_kernel):
    CALLS.clear()
    kernel = make_kernel(builtins=True, wiki=wiki(signed_in=True))
    code, tested = hz(kernel, "system", "connector", "test", "wiki")
    assert (code, tested["passed"], tested["login"]["account"], CALLS) == (0, True, "ada", ["live"])
    off = make_kernel(
        builtins=True, wiki=wiki(signed_in=True), config=Config(files=(), data={"capabilities": {"documents": "none"}})
    )
    code, report = hz(off, "system", "connector", "list")
    connector = next(c for c in report["connectors"] if c["name"] == "wiki")
    assert connector["state"] == "not-bound"
    assert connector["bind"] == ["hz system config set capabilities.documents wiki"]


def test_the_web_head_has_it_and_the_rest_head_does_not(make_kernel):
    kernel = make_kernel(builtins=True)
    assert {"system_connector_list", "system_connector_test"} <= {item.name for item in exposed(kernel, web.HEAD)}
    assert not {"system_connector_list", "system_connector_test"} & {item.name for item in exposed(kernel, api.HEAD)}
