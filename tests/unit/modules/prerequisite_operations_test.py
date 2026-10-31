"""`hz system prerequisite` (ADR 0112): list, check and install as the heads expose them, and doctor's view."""

import pytest

from heinzel import (
    Check,
    CurrentUser,
    HealthLet,
    HeinzelError,
    Homebrew,
    Kernel,
    ManualInstall,
    Module,
    OperationLet,
    PrerequisiteReport,
    PrerequisiteService,
    Progress,
    Status,
)
from heinzel.heads._exposure import exposed
from heinzel.modules.system import prerequisite
from heinzel.modules.system.prerequisite._render import render_list, render_report
from heinzel.modules.system.prerequisite._service import ToolPrerequisites
from tests._prerequisite_machine import BREW, Machine

pytestmark = pytest.mark.unit

LINUX = ManualInstall("Install it with your package manager.", "https://example.test/install")


def example() -> Module:
    module = Module("example", "An example.")
    module.prerequisite("tool", title="Tool", purpose="Does a thing.", linux=LINUX, macos=Homebrew("tool"))
    return module


def kernel_on(machine: Machine, make_kernel) -> Kernel:
    kernel = make_kernel(system__prerequisite=prerequisite.module, example=example())
    kernel.bind(PrerequisiteService, ToolPrerequisites(kernel, Progress(), machine.host()))
    return kernel


def run(kernel: Kernel, name: str, **arguments: object):
    [operation] = [let for _, let in kernel.lets(OperationLet) if let.name == name]
    with kernel.resolve(CurrentUser).local("cli"):
        return operation, kernel.invoke(operation, **arguments)


def test_list_exports_the_declarations_without_looking_at_the_machine(make_kernel):
    machine = Machine(present={BREW: ""})

    _, definitions = run(kernel_on(machine, make_kernel), "list")

    assert [(d.module, d.name, d.macos) for d in definitions] == [("example", "tool", Homebrew("tool"))]
    assert machine.commands == []


def test_check_reports_without_installing_and_exits_non_zero_while_something_is_missing(make_kernel):
    machine = Machine(present={BREW: ""})
    kernel = kernel_on(machine, make_kernel)

    operation, report = run(kernel, "check")

    assert isinstance(report, PrerequisiteReport)
    assert (report.ready, operation.exit_code(report)) == (False, 1)
    assert all(command[0] != BREW for command in machine.commands)


def test_check_is_private_and_install_is_cli_only(make_kernel):
    kernel = kernel_on(Machine(), make_kernel)
    operations = {let.name: let for _, let in kernel.lets(OperationLet) if let.name in {"check", "install", "list"}}

    assert operations["check"].private
    assert (operations["install"].private, operations["install"].heads, operations["install"].read_only) == (
        True,
        {"cli"},
        False,
    )
    assert operations["list"].heads is None
    assert not [item for item in exposed(kernel, "mcp") if item.operation is operations["install"]]


def test_a_private_operation_refuses_a_caller_no_trusted_head_authenticated(make_kernel):
    kernel = kernel_on(Machine(), make_kernel)
    [check] = [let for _, let in kernel.lets(OperationLet) if let.name == "check"]

    with pytest.raises(HeinzelError, match="authenticated private API, local CLI"):
        kernel.invoke(check)


def test_install_without_confirm_changes_nothing_and_says_how_to_look_first(make_kernel):
    machine = Machine(present={BREW: ""})

    with pytest.raises(HeinzelError, match="--confirm"):
        run(kernel_on(machine, make_kernel), "install")

    assert machine.commands == []


def test_install_with_confirm_installs_and_exits_zero(make_kernel):
    machine = Machine(present={BREW: ""})
    machine.provides["tool"] = {"/opt/homebrew/bin/tool": "tool 1.2.3"}

    operation, report = run(kernel_on(machine, make_kernel), "install", confirm=True)

    assert (report.ready, report.checks[0].status, operation.exit_code(report)) == (True, "installed", 0)


def test_install_for_an_owner_leaves_other_owners_alone(make_kernel):
    machine = Machine(present={BREW: ""})

    _, report = run(kernel_on(machine, make_kernel), "install", owner="system.prerequisite", confirm=True)

    assert report.checks == []
    assert report.ready is True


def two_tools(make_kernel, machine: Machine) -> Kernel:
    module = example()
    module.prerequisite(
        "other", purpose="Maybe.", linux=LINUX, when="feature", needed=lambda: False, condition="Chosen."
    )
    kernel = make_kernel(system__prerequisite=prerequisite.module, example=module)
    kernel.bind(PrerequisiteService, ToolPrerequisites(kernel, Progress(), machine.host()))
    return kernel


def test_the_report_renders_a_row_per_tool_hides_the_skipped_and_names_them_below(make_kernel):
    _, report = run(two_tools(make_kernel, Machine(present={"/usr/bin/tool": "tool 1.0"})), "check")

    shown = render_report(report)

    table, note = shown.renderables
    assert [column.header for column in table.columns] == ["Owner", "Tool", "Status", "Version", "Detail"]
    assert table.row_count == 1
    assert "(not ready)" not in str(table.title)
    assert str(note) == "Not needed for this selection: other."


def test_a_report_that_is_not_ready_says_so_in_its_title(make_kernel):
    _, report = run(kernel_on(Machine(present={BREW: ""}), make_kernel), "check")

    table = render_report(report)

    assert table.title == "Prerequisites (not ready)"


def test_an_empty_report_and_list_say_so(make_kernel):
    assert str(render_report(PrerequisiteReport(ready=True, checks=[]))) == "No tool is needed."
    assert str(render_list([])) == "No enabled module declares an external tool."


def test_the_list_shows_owner_tool_need_and_how_each_is_installed(make_kernel):
    module = example()
    module.prerequisite("app", purpose="Maybe.", linux=LINUX, macos=Homebrew("app", cask=True))
    module.prerequisite(
        "hand", purpose="Maybe.", linux=LINUX, macos=ManualInstall("Download it.", "https://example.test/mac")
    )
    module.prerequisite("none", purpose="Maybe.", linux=LINUX, when="feature")
    kernel = make_kernel(system__prerequisite=prerequisite.module, example=module)
    kernel.bind(PrerequisiteService, ToolPrerequisites(kernel, Progress(), Machine().host()))
    _, definitions = run(kernel, "list")

    table = render_list(definitions)

    assert [column.header for column in table.columns] == ["Owner", "Tool", "Needed", "macOS", "Linux"]
    cells = [[str(cell) for cell in column.cells] for column in table.columns]
    assert cells[3] == ["brew install tool", "brew install --cask app", "Download it. https://example.test/mac", "-"]
    assert cells[2] == ["always", "always", "always", "when asked for"]


def health(kernel: Kernel) -> list[Check]:
    [(_, let)] = [(r, h) for r, h in kernel.lets(HealthLet) if r.qualname == "system.prerequisite"]
    return kernel.call(let.provider)


def test_doctor_is_up_for_a_usable_tool_and_degraded_with_the_install_command_for_a_missing_one(make_kernel):
    ready = health(kernel_on(Machine(present={"/usr/bin/tool": "tool 1.0"}), make_kernel))
    missing = health(kernel_on(Machine(present={BREW: ""}), make_kernel))

    assert [(c.name, c.status) for c in ready] == [("example: tool", Status.UP)]
    assert [(c.name, c.status) for c in missing] == [("example: tool", Status.DEGRADED)]
    assert missing[0].fix == "hz system prerequisite install --owner example --confirm"


def test_doctor_gives_manual_instructions_when_nothing_can_be_automatic(make_kernel):
    [check] = health(kernel_on(Machine("linux"), make_kernel))

    assert check.status is Status.DEGRADED
    assert check.fix == "Install it with your package manager. https://example.test/install"


def test_doctor_does_not_list_what_the_selection_does_not_need(make_kernel):
    module = Module("example", "An example.")
    module.prerequisite(
        "tool", purpose="Does a thing.", linux=LINUX, when="feature", needed=lambda: False, condition="Chosen."
    )
    kernel = make_kernel(system__prerequisite=prerequisite.module, example=module)
    kernel.bind(PrerequisiteService, ToolPrerequisites(kernel, Progress(), Machine().host()))

    assert health(kernel) == []
