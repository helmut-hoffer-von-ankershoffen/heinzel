"""How the CLI shows connectors: states, what to run next, how each capability is served, and a live test."""

import pytest
from rich.console import Console

from heinzel import Check, Login, Status
from heinzel.modules.system.connector import (
    AdapterCandidate,
    CapabilityChoice,
    Connector,
    ConnectorCapability,
    ConnectorReport,
    ConnectorTest,
)
from heinzel.modules.system.connector._render import render_report, render_test

pytestmark = pytest.mark.unit


def text(renderable: object) -> str:
    console = Console(width=200, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


def connector(name: str, state: str, **more: object) -> Connector:
    used = state != "not-bound"
    use = ConnectorCapability(
        capability="tracker", title="Tracker", adapter=name, modes=["read"], used=used, bound=None, how="configured"
    )
    fields: dict[str, object] = {
        "name": name,
        "title": name.title(),
        "summary": "x",
        "distribution": f"heinzel-{name}",
        "builtin": False,
        "state": state,
        "login": None,
        "capabilities": [use],
        "tools": [],
        "checks": [],
        "connect": [f"{name} login"],
        "bind": [f"hz system config set capabilities.tracker {name}"],
        "testable": True,
    }
    return Connector.model_validate(fields | more)


def choice(capability: str, bound: str | None, how: str, **more: object) -> CapabilityChoice:
    fields: dict[str, object] = {
        "capability": capability,
        "title": capability.title(),
        "purpose": "things",
        "key": f"capabilities.{capability}",
        "bound": bound,
        "how": how,
        "problem": None,
        "candidates": [],
        "find": f"hz system plugin search --text {capability}",
    }
    return CapabilityChoice.model_validate(fields | more)


def test_the_report_says_who_is_connected_and_what_to_run_next():
    ada = AdapterCandidate(adapter="ada", connector="ada", title="Ada", state="connected")
    report = ConnectorReport(
        connectors=[
            connector("ada", "connected", login=Login(signed_in=True, account="ada")),
            connector("bob", "not-signed-in"),
            connector("cy", "not-bound"),
            connector("dee", "unknown"),
        ],
        capabilities=[
            choice("chat", None, "unbound"),
            choice("mail", None, "off"),
            choice("tracker", "ada", "automatic", candidates=[ada]),
            choice("directory", "one-on-ones", "default", problem="not installed"),
        ],
    )
    shown = text(render_report(report))
    assert "connected as ada" in shown
    assert "bob login" in shown
    assert "hz system config set capabilities.tracker cy" in shown
    assert "hz system connector test dee" in shown
    assert "nothing installed: hz system plugin search --text chat" in shown
    assert "none (turned off)" in shown
    assert "ada (automatic)" in shown
    assert "one-on-ones (default): not installed" in shown


@pytest.mark.parametrize(("passed", "verdict"), [(True, "passed"), (False, "failed")])
def test_a_live_test_says_whether_it_passed_how_long_it_took_and_each_check(passed, verdict):
    status = Status.UP if passed else Status.DOWN
    tested = ConnectorTest(
        name="ada",
        passed=passed,
        latency_ms=42,
        login=None,
        checks=[Check(name="whoami", status=status, detail="asked", fix="" if passed else "ada login")],
    )
    shown = text(render_test(tested))
    assert f"ada: the live test {verdict} in 42 ms" in " ".join(shown.split())
    assert "whoami" in shown
