"""The connector service: states from fake checks, tools from the registry, live tests only on demand."""

import pytest

from heinzel import Check, Config, Document, HeinzelError, Login, Module, Status
from heinzel.kernel import CapabilityView
from heinzel.modules.system.connector import ConnectorCapability, ConnectorService
from heinzel.modules.system.connector._service import login_of, state_of, title_of

pytestmark = pytest.mark.unit

UP = Check(name="tool", status=Status.UP)
LIVE_CALLS: list[str] = []


def use(*, used: bool) -> ConnectorCapability:
    return ConnectorCapability(
        capability="documents", title="Documents", adapter="notes", modes=["read"], used=used, bound=None, how="unbound"
    )


def signed(*, value: bool | None, **more: str) -> Check:
    return Check(name="tool", status=Status.UP, login=Login(signed_in=value, **more))


@pytest.mark.parametrize(
    ("checks", "used", "state"),
    [
        ([signed(value=False)], True, "not-signed-in"),
        ([signed(value=False)], False, "not-signed-in"),
        ([signed(value=True)], False, "not-bound"),
        ([signed(value=True), Check(name="site", status=Status.DEGRADED, detail="no site")], True, "attention"),
        ([signed(value=True)], True, "connected"),
        ([signed(value=None)], True, "unknown"),
        ([UP], True, "unknown"),
    ],
)
def test_the_state_comes_from_the_sign_in_the_bindings_and_the_other_checks(checks, used, state):
    assert state_of(checks, [use(used=used)]) == state


def test_a_missing_sign_in_is_the_login_shown_even_after_a_good_one():
    assert login_of([signed(value=True, account="ada"), signed(value=False, command="tool login")]) == Login(
        signed_in=False, command="tool login"
    )
    assert login_of([UP]) is None


def test_a_login_is_one_clean_line():
    login = Login(signed_in=True, account="ada\u202e\n  lovelace", site="x" * 300)
    assert login.account == "ada lovelace"
    assert len(login.site) == 200


@pytest.mark.parametrize(
    ("name", "summary", "title"),
    [
        ("github", "Read pull requests from GitHub.", "GitHub"),
        ("atlassian", "Read Jira issues.", "Atlassian"),
        ("system.runner", "Run agents.", "Runner"),
    ],
)
def test_a_title_takes_the_casing_its_summary_uses(name, summary, title):
    assert title_of(name, summary) == title


class Notes:
    def read(self, ref: str) -> Document:
        return Document(ref=ref, title=ref, text="body")


def notes_module(*, signed_in: bool | None = True, live: bool = True) -> Module:
    module = Module("notes", "Reads Notes for tests.")
    module.provides("documents", modes={"read"}, hosts=["notes.invalid"])(Notes)

    @module.health
    def health() -> list[Check]:
        return [signed(value=signed_in, account="ada", command="notes login")]

    if live:

        @module.health(live=True)
        def live_check() -> list[Check]:
            LIVE_CALLS.append("called")
            return [Check(name="whoami", status=Status.UP, detail="ada", login=Login(signed_in=True, account="ada"))]

    return module


def service(make_kernel, module: Module, **bindings: str) -> ConnectorService:
    kernel = make_kernel(notes=module, config=Config(files=(), data={"capabilities": bindings}))
    return ConnectorService(kernel, kernel.resolve(CapabilityView))


def test_the_report_lists_state_tools_commands_and_the_choice_without_running_the_live_test(make_kernel):
    LIVE_CALLS.clear()
    report = service(make_kernel, notes_module()).report()
    [connector] = report.connectors
    assert (connector.name, connector.title, connector.state, connector.testable) == (
        "notes",
        "Notes",
        "connected",
        True,
    )
    assert connector.login == Login(signed_in=True, account="ada", command="notes login")
    assert [(t.capability, t.method, t.mode) for t in connector.tools] == [("documents", "read", "read")]
    assert connector.connect == ["notes login"]
    assert connector.bind == []
    documents = next(c for c in report.capabilities if c.capability == "documents")
    assert (documents.title, documents.bound, documents.how, documents.key) == (
        "Documents",
        "notes",
        "automatic",
        "capabilities.documents",
    )
    assert [c.adapter for c in documents.candidates] == ["notes"]
    assert LIVE_CALLS == []


def test_turned_off_it_is_not_bound_and_says_how_to_bind_it(make_kernel):
    [connector] = service(make_kernel, notes_module(), documents="none").report().connectors
    assert connector.state == "not-bound"
    assert connector.bind == ["hz system config set capabilities.documents notes"]


def test_the_live_test_runs_the_live_checks_and_times_them(make_kernel):
    LIVE_CALLS.clear()
    tested = service(make_kernel, notes_module()).test("notes")
    assert (tested.passed, tested.login, LIVE_CALLS) == (True, Login(signed_in=True, account="ada"), ["called"])
    assert tested.latency_ms >= 0


def test_an_unknown_connector_or_one_without_a_live_test_says_what_exists(make_kernel):
    with pytest.raises(HeinzelError, match=r"No connector is named 'nope'; the connectors are: notes"):
        service(make_kernel, notes_module()).test("nope")
    with pytest.raises(HeinzelError, match="has no live test"):
        service(make_kernel, notes_module(live=False)).test("notes")


def test_a_failing_check_is_a_down_check_without_the_exception_text(make_kernel):
    module = Module("notes", "Reads Notes for tests.")
    module.provides("documents", modes={"read"}, hosts=["notes.invalid"])(Notes)

    @module.health(live=True)
    def boom() -> list[Check]:
        msg = "token=secret"
        raise RuntimeError(msg)

    tested = service(make_kernel, module).test("notes")
    assert not tested.passed
    assert tested.checks[0].detail == "the checks failed (RuntimeError)"
