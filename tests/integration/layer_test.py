"""Layer plugins end to end through the CLI: Acme's made-up company, org, site and team (ADR 0040).

The test plugins in `tests/fixtures/layers` are real packages, discovered the way the kernel walks an installed
plugin: `acme` (the company, with an adapter), `acme-payments` (an org), `acme-lisbon` (a site) and
`acme-payments-checkout` (a team at Lisbon). Payments and Lisbon recommend different calendars, so the stack has an
org-site conflict the team hasn't decided. Nothing reaches the network.
"""

import importlib
import json
import sys
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heinzel import Config, Kernel, Module, ModuleRecord, Settings
from heinzel.heads.cli import _setup, build
from heinzel.kernel import Origin, discover, user_config_dir, walk

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parents[1] / "fixtures" / "layers"
ACME = ("heinzel_acme", "heinzel_acme_payments", "heinzel_acme_lisbon", "heinzel_acme_payments_checkout")
STACK = "acme → acme-payments + acme-lisbon → acme-payments-checkout"


@pytest.fixture
def acme(monkeypatch, settings: Settings):
    """Builds a kernel with the built-in modules, the Acme plugins asked for (all by default) and `extra` modules."""
    monkeypatch.syspath_prepend(str(FIXTURES))

    def make(*plugins: str, data: dict | None = None, extra: tuple[Module, ...] = ()) -> Kernel:
        records = discover(plugins=False)[0]
        for name in plugins or ACME:
            origin = Origin(distribution=name.replace("_", "-"), version="1.0.0", builtin=False)
            records += walk(importlib.import_module(name), origin)
        test = Origin(distribution="heinzel-test", version="1.0.0", builtin=False)
        records += [ModuleRecord(qualname=module.name, module=module, origin=test) for module in extra]
        return Kernel(settings, records, config=Config(files=(), data=data or {}))

    yield make
    for name in ACME:  # imported afresh by the next test, so no test sees another's modules
        sys.modules.pop(name, None)


def layer(name: str, parent: str | None = None) -> Module:
    module = Module(name, f"The {name} layer.")
    module.layer(parent=parent)
    return module


def hz(kernel: Kernel, *args: str, json_output: bool = True, stdin: str | None = None) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"] if json_output else list(args), input=stdin)
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def flat(text: object) -> str:
    """Output with every run of whitespace one space: the terminal is 80 columns wide and wraps lines."""
    return " ".join(str(text).split())


def team_repository(path: Path, plugin: str) -> Path:
    (path / "team").mkdir(parents=True)
    (path / "hz.yaml").write_text(f"kind: team\nname: checkout\nplugin:\n  name: {plugin}\n", encoding="utf-8")
    (path / "team" / "roster.yaml").write_text("members: []\n", encoding="utf-8")
    return path


def test_layer_list_shows_where_each_plugin_sits_and_what_it_brings(acme):
    code, listed = hz(acme(), "system", "layer", "list")
    assert code == 0, listed
    assert {(layer["name"], layer["label"], layer["parent"], layer["site"]) for layer in listed["layers"]} == {
        ("acme", "company", None, None),
        ("acme-payments", "org", "acme", None),
        ("acme-lisbon", "site", "acme", None),
        ("acme-payments-checkout", "team", "acme-payments", "acme-lisbon"),
    }
    company = next(layer for layer in listed["layers"] if layer["name"] == "acme")
    assert (company["plugin"], company["adapters"]) == ("heinzel-acme", ["acme:tracker"])
    assert listed["problems"] == []
    code, text = hz(acme(), "system", "layer", "list", json_output=False)
    assert code == 0
    assert "acme-payments-checkout" in flat(text)
    assert "role checkout-captain" in flat(text)


def test_your_stack_runs_general_to_specific_with_recommendations_most_specific_first(acme):
    code, shown = hz(acme(), "system", "layer", "show")
    assert code == 0, shown
    assert (shown["layer"], shown["stack"], shown["source"]) == (
        "acme-payments-checkout",
        STACK,
        "the only team layer installed",
    )
    assert shown["roles"] == ["payments-lead", "checkout-captain"]
    assert shown["recommendations"] == [
        {
            "key": "capabilities.tracker",
            "value": "acme-payments-checkout",
            "layer": "acme-payments-checkout",
            "alternatives": {"acme-lisbon": "acme", "acme": "acme"},
            "command": "hz system config set capabilities.tracker acme-payments-checkout",
        }
    ]
    (conflict,) = shown["conflicts"]
    assert conflict["key"] == "capabilities.calendar"
    assert conflict["values"] == {"acme": "acme", "acme-payments": "acme-payments", "acme-lisbon": "acme-lisbon"}
    assert conflict["fix"].startswith("acme-payments-checkout decides by recommending capabilities.calendar")
    assert shown["next"] == ["hz system config set capabilities.tracker acme-payments-checkout"]
    code, text = hz(acme(), "system", "layer", "show", json_output=False)
    assert code == 0
    assert "Undecided: capabilities.calendar" in flat(text)
    config = user_config_dir() / "config.toml"
    assert not config.exists()  # showing applies nothing


def test_show_takes_another_layers_stack(acme):
    code, shown = hz(acme(), "system", "layer", "show", "--layer", "acme-payments")
    assert code == 0, shown
    assert (shown["stack"], shown["source"]) == ("acme → acme-payments", "asked for")
    assert [r["key"] for r in shown["recommendations"]] == ["capabilities.calendar", "capabilities.tracker"]
    assert shown["conflicts"] == []


def test_the_team_repository_and_your_setting_say_which_layer_is_yours(acme, tmp_path):
    checkout = team_repository(tmp_path / "team", "heinzel-acme-payments-checkout")
    code, shown = hz(acme(data={"team": {"checkout": str(checkout)}}), "system", "layer", "show")
    assert code == 0, shown
    assert (shown["layer"], shown["source"], shown["stack"]) == ("acme-payments-checkout", "your team", STACK)
    data = {"team": {"checkout": str(checkout), "layer": "acme-payments"}}
    code, shown = hz(acme(data=data), "system", "layer", "show")
    assert code == 0, shown
    assert (shown["layer"], shown["stack"]) == ("acme-payments", "acme → acme-payments")


def test_a_broken_team_manifest_still_shows_the_installed_stack_and_says_why(acme, tmp_path):
    checkout = tmp_path / "team"
    checkout.mkdir()
    (checkout / "hz.yaml").write_text("name: [broken\n", encoding="utf-8")
    code, shown = hz(acme(data={"team": {"checkout": str(checkout)}}), "system", "layer", "show")
    assert code == 0, shown
    assert shown["stack"] == STACK
    assert [p["problem"] for p in shown["problems"]] == ["unreadable"]


def test_system_info_shows_your_stack(acme):
    code, info = hz(acme(), "system", "info")
    assert code == 0, info
    assert info["sections"]["system.layer"] == {
        "stack": STACK,
        "layer": "acme-payments-checkout",
        "from": "the only team layer installed",
        "roles": "payments-lead, checkout-captain",
    }
    code, info = hz(acme("heinzel_acme"), "system", "info")
    assert code == 0, info
    assert info["sections"]["system.layer"]["stack"] == "acme"


def test_system_info_says_what_is_wrong_with_your_stack(acme):
    _, info = hz(acme("heinzel_acme", "heinzel_acme_lisbon", "heinzel_acme_payments_checkout"), "system", "info")
    assert "hz system plugin add heinzel-acme-payments" in info["sections"]["system.layer"]["problems"]


def test_without_layers_there_is_nothing_to_report(make_kernel):
    kernel = make_kernel(builtins=True)
    _, info = hz(kernel, "system", "info")
    assert "system.layer" not in info["sections"]
    _, doctor = hz(kernel, "system", "doctor")
    assert doctor["checks"]["system.layer"] == []
    _, listed = hz(kernel, "system", "layer", "list")
    assert listed == {"layers": [], "problems": [], "next": ["hz system plugin add heinzel-<your-team>"]}
    _, text = hz(kernel, "system", "layer", "show", json_output=False)
    assert "You are in no layer." in flat(text)


def test_doctor_names_the_undecided_conflict_with_both_layers_and_the_key(acme):
    _, report = hz(acme(), "system", "doctor")
    checks = {check["name"]: check for check in report["checks"]["system.layer"]}
    conflict = checks["conflict capabilities.calendar"]
    assert conflict["status"] == "degraded"
    assert "acme-payments recommends" in conflict["detail"]
    assert "acme-lisbon recommends" in conflict["detail"]
    assert "hz system config set capabilities.calendar" in conflict["fix"]


def test_doctor_names_the_plugin_a_missing_parent_comes_with(acme):
    kernel = acme("heinzel_acme", "heinzel_acme_lisbon", "heinzel_acme_payments_checkout")
    _, report = hz(kernel, "system", "doctor")
    checks = {check["name"]: check for check in report["checks"]["system.layer"]}
    missing = checks["acme-payments-checkout missing-parent"]
    assert (missing["status"], missing["fix"]) == ("degraded", "hz system plugin add heinzel-acme-payments")
    _, listed = hz(kernel, "system", "layer", "list", json_output=False)
    assert "Fix: hz system plugin add heinzel-acme-payments" in flat(listed)
    _, shown = hz(kernel, "system", "layer", "show")
    assert shown["stack"] == "acme-lisbon → acme-payments-checkout"


def test_doctor_says_how_to_pick_your_team_when_two_are_installed(acme):
    _, report = hz(acme(extra=(layer("acme-payments-refunds", "acme-payments"),)), "system", "doctor")
    stack = [check for check in report["checks"]["system.layer"] if check["name"].startswith("stack")]
    assert [(c["name"], c["status"]) for c in stack] == [("stack ambiguous", "degraded")]
    assert stack[0]["fix"] == "hz system config set team.layer acme-payments-checkout"


def test_a_missing_parent_is_one_finding_not_also_a_stack_one(acme):
    _, report = hz(acme("heinzel_acme", "heinzel_acme_lisbon", "heinzel_acme_payments_checkout"), "system", "doctor")
    assert [c["name"] for c in report["checks"]["system.layer"]] == ["acme-payments-checkout missing-parent"]


def test_when_every_layer_is_left_out_list_says_what_fixes_the_first(make_kernel):
    kernel = make_kernel(builtins=True, **{"acme-a": layer("acme-a", "acme-b"), "acme-b": layer("acme-b", "acme-a")})
    _, listed = hz(kernel, "system", "layer", "list")
    assert listed["layers"] == []
    assert listed["next"] == [listed["problems"][0]["fix"]]


def test_a_healthy_stack_is_up_and_a_name_off_the_path_is_only_a_hint(make_kernel):
    kernel = make_kernel(builtins=True, acme=layer("acme"), checkout=layer("checkout", "acme"))
    _, report = hz(kernel, "system", "doctor")
    checks = {check["name"]: check for check in report["checks"]["system.layer"]}
    assert checks["stack"] == {"name": "stack", "status": "up", "detail": "acme → checkout", "fix": ""}
    assert checks["checkout naming"]["status"] == "up"
    assert "acme-<name>" in checks["checkout naming"]["detail"]
    _, listed = hz(kernel, "system", "layer", "list", json_output=False)
    assert "hint:" in flat(listed)


def test_setup_runs_your_stacks_layers_most_specific_first(acme):
    plugins = [record.qualname for record, _ in _setup.steps(acme()) if not record.origin.builtin]
    assert plugins == ["acme-payments-checkout", "acme-lisbon", "acme-payments", "acme"]


def test_setup_offers_each_setting_with_the_layer_that_wins_it(acme, monkeypatch, tmp_path):
    monkeypatch.setattr(_setup, "has_terminal", lambda: True)
    monkeypatch.chdir(tmp_path)
    code, text = hz(acme(), "setup", "acme", json_output=False, stdin="\n")
    assert code == 0, text
    assert "capabilities.tracker: acme-payments-checkout recommends" in flat(text)
    assert "capabilities.calendar: acme recommends" in flat(text)
    assert "acme-payments-checkout decides, so neither is offered" in flat(text)
    config = user_config_dir() / "config.toml"
    assert not config.exists()
    code, text = hz(acme(), "setup", "acme-payments-checkout", json_output=False, stdin="y\n")
    assert code == 0, text
    assert tomllib.loads(config.read_text())["capabilities"] == {"tracker": "acme-payments-checkout"}


def test_org_sync_offers_your_layers_bindings_and_writes_only_what_you_accept(acme):
    _, report = hz(acme("heinzel_acme"), "org", "sync")
    items = {item["id"]: item for item in report["items"]}
    assert items["binding:tracker"]["state"] == "pending"
    assert items["binding:tracker"]["origin"] == "layer acme (heinzel-acme), its playbook"
    assert items["binding:calendar"]["state"] == "by-hand"  # no installed plugin provides an acme calendar
    code, report = hz(acme("heinzel_acme"), "org", "sync", "--accept", "binding:tracker")
    assert code == 0, report
    assert tomllib.loads((user_config_dir() / "config.toml").read_text())["capabilities"] == {"tracker": "acme"}


def test_org_sync_offers_the_binding_the_stack_decides_and_leaves_the_undecided_out(acme):
    _, report = hz(acme(), "org", "sync")
    items = {item["id"]: item for item in report["items"]}
    assert set(items) == {"binding:tracker"}  # the calendar is undecided between acme-payments and acme-lisbon
    assert items["binding:tracker"]["recommended"] == "acme-payments-checkout"
