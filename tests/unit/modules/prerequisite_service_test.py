"""The prerequisite engine (ADR 0112) against described machines: no real Homebrew, shell or search path is touched."""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from heinzel import HeinzelError, Homebrew, Kernel, ManualInstall, Module, PrerequisiteLet, Progress
from heinzel.modules.system.prerequisite._host import ToolHost
from heinzel.modules.system.prerequisite._service import BREW_FLAGS, ToolPrerequisites
from tests._prerequisite_machine import BREW, Machine

pytestmark = pytest.mark.unit

LINUX = ManualInstall("Install it with your package manager.", "https://example.test/install")


def owner(*declarations: Callable[[Module], object], name: str = "example") -> Module:
    module = Module(name, "An example.")
    for declare in declarations:
        declare(module)
    return module


FORMULA = Homebrew("tool")


def tool(
    name: str = "tool", macos: Homebrew | ManualInstall | None = FORMULA, **options: object
) -> Callable[[Module], object]:
    return lambda module: module.prerequisite(name, purpose="Does a thing.", linux=LINUX, macos=macos, **options)  # type: ignore[arg-type]


def engine(machine: Machine, kernel_factory: Callable[..., Kernel], **modules: Module) -> ToolPrerequisites:
    return ToolPrerequisites(kernel_factory(**modules), Progress(), machine.host())


def test_a_working_executable_on_the_path_is_available_with_its_version(make_kernel):
    machine = Machine(present={"/usr/bin/tool": "tool version 2.39.5 (Apple)"})

    [check] = engine(machine, make_kernel, example=owner(tool())).check().checks

    assert (check.status, check.version, check.path) == ("available", "2.39.5", "/usr/bin/tool")
    assert machine.commands == [["xcode-select", "-p"], ["/usr/bin/tool", "--version"]]


def test_a_probe_gets_the_owners_arguments_and_no_credentials(make_kernel):
    machine = Machine(present={"/usr/bin/tool": "1.2"})
    machine.environment["HZ_CREDENTIAL_SERVICE_TOKEN"] = "secret"

    engine(machine, make_kernel, example=owner(tool(version_args=("version", "--short")))).check()

    assert machine.commands[-1] == ["/usr/bin/tool", "version", "--short"]
    assert "HZ_CREDENTIAL_SERVICE_TOKEN" not in machine.environments[-1]


@pytest.mark.parametrize("output", ["no version here", "tool 1", ""])
def test_output_without_a_version_does_not_count_as_a_working_tool(make_kernel, output: str):
    machine = Machine(present={"/usr/bin/tool": output})

    [check] = engine(machine, make_kernel, example=owner(tool())).check().checks

    assert check.status == "missing"


def test_a_tool_older_than_the_minimum_is_incompatible_and_never_replaced(make_kernel):
    machine = Machine(present={"/usr/bin/tool": "tool 2.0.1"})
    service = engine(machine, make_kernel, example=owner(tool(minimum_version="2.1")))

    [check] = service.install().checks

    assert (check.status, check.version) == ("incompatible", "2.0.1")
    assert "2.1" in check.detail
    assert not any(command[0] == BREW for command in machine.commands)


def test_a_newer_copy_elsewhere_satisfies_the_minimum(make_kernel):
    machine = Machine(present={"/usr/bin/tool": "tool 1.0", "/opt/homebrew/bin/tool": "tool 3.0"})

    [check] = engine(machine, make_kernel, example=owner(tool(minimum_version="2.1"))).check().checks

    assert (check.status, check.path) == ("available", "/opt/homebrew/bin/tool")


def test_homebrew_directories_are_searched_though_the_path_lacks_them(make_kernel):
    machine = Machine(present={"/opt/homebrew/bin/tool": "tool 4.5.6"})

    [check] = engine(machine, make_kernel, example=owner(tool())).check().checks

    assert (check.status, check.path) == ("available", "/opt/homebrew/bin/tool")


def test_a_tool_found_outside_the_path_is_added_to_it_for_the_continuing_process(make_kernel):
    machine = Machine(present={"/opt/homebrew/bin/tool": "tool 4.5.6"}, path="/usr/bin:/bin")

    engine(machine, make_kernel, example=owner(tool())).check()

    assert machine.environment["PATH"] == "/usr/bin:/bin:/opt/homebrew/bin"


def test_an_empty_path_still_searches_the_default_path(make_kernel):
    machine = Machine(present={"/usr/bin/tool": "tool 1.0"}, path="")
    machine.environment["PATH"] = ""

    assert machine.host().search_path == os.defpath
    assert engine(machine, make_kernel, example=owner(tool())).check().checks[0].path == "/usr/bin/tool"


def test_a_relative_path_entry_never_becomes_a_candidate(make_kernel):
    machine = Machine(present={"bin/tool": "tool 1.0"}, path="bin")

    assert engine(machine, make_kernel, example=owner(tool())).check().checks[0].status == "missing"


@pytest.mark.parametrize("trouble", ["exit", "timeout", "truncated"])
def test_a_probe_that_fails_times_out_or_overflows_is_not_a_working_tool(make_kernel, trouble: str):
    machine = Machine(present={"/usr/bin/tool": "tool 1.0"})
    if trouble == "exit":
        machine.probe_codes["tool"] = 2
    elif trouble == "timeout":
        machine.probe_timeouts.add("tool")
    else:
        machine.probe_truncated.add("tool")

    assert engine(machine, make_kernel, example=owner(tool())).check().checks[0].status == "missing"


def test_a_tool_that_cannot_start_is_missing(make_kernel):
    machine = Machine(present={"/usr/bin/tool": "tool 1.2"})
    machine.unstartable.add("tool")

    [check] = engine(machine, make_kernel, example=owner(tool())).check().checks

    assert check.status == "missing"


def test_missing_on_macos_with_homebrew_is_planned_as_an_automatic_install(make_kernel):
    machine = Machine(present={BREW: ""})

    report = engine(machine, make_kernel, example=owner(tool())).check()

    [check] = report.checks
    assert (report.ready, check.status, check.automatic) == (False, "missing", True)
    assert "brew install --formula tool" in check.detail
    assert [c[0] for c in machine.commands] == []


def test_a_tap_qualified_package_is_called_out_before_it_is_installed(make_kernel):
    machine = Machine(present={BREW: ""})

    [check] = engine(machine, make_kernel, example=owner(tool(macos=Homebrew("owner/tap/tool")))).check().checks

    assert "from a third-party tap" in check.detail


def test_missing_on_macos_without_homebrew_points_to_homebrew_and_is_not_automatic(make_kernel):
    machine = Machine()

    [check] = engine(machine, make_kernel, example=owner(tool())).check().checks

    assert (check.status, check.automatic) == ("missing", False)
    assert "https://brew.sh" in check.detail
    assert "hz system prerequisite install --confirm" in check.detail


def test_missing_on_macos_with_a_manual_recipe_shows_the_owners_instructions(make_kernel):
    recipe = ManualInstall("Download the installer.", "https://example.test/mac")

    [check] = engine(Machine(present={BREW: ""}), make_kernel, example=owner(tool(macos=recipe))).check().checks

    assert (check.automatic, check.detail) == (False, "Download the installer. https://example.test/mac")


def test_missing_on_macos_without_any_recipe_says_so_and_gives_the_linux_hint(make_kernel):
    [check] = engine(Machine(), make_kernel, example=owner(tool(macos=None))).check().checks

    assert not check.automatic
    assert "no macOS installer is declared" in check.detail
    assert "https://example.test/install" in check.detail


def test_missing_on_linux_shows_the_owners_instructions_and_installs_nothing(make_kernel):
    machine = Machine("linux", present={BREW: ""})
    service = engine(machine, make_kernel, example=owner(tool()))

    report = service.install()

    [check] = report.checks
    assert (report.ready, check.status, check.automatic) == (False, "manual", False)
    assert check.detail == "Install it with your package manager. https://example.test/install"
    assert machine.commands == []


def test_other_platforms_are_told_to_install_by_hand(make_kernel):
    [check] = engine(Machine("win32"), make_kernel, example=owner(tool())).install().checks

    assert (check.status, "macOS" in check.detail) == ("manual", True)


def test_install_runs_homebrew_with_fixed_arguments_then_trusts_only_a_new_probe(make_kernel):
    machine = Machine(present={BREW: ""})
    machine.provides["tool"] = {"/opt/homebrew/bin/tool": "tool 1.2.3"}
    service = engine(machine, make_kernel, example=owner(tool()))

    report = service.install()

    [check] = report.checks
    assert (report.ready, check.status, check.version) == (True, "installed", "1.2.3")
    [install] = [c for c in machine.commands if c[:2] == [BREW, "install"]]
    assert install == [BREW, "install", "--formula", "tool"]
    flags = machine.environments[machine.commands.index(install)]
    assert all(flags[key] == value for key, value in BREW_FLAGS)
    assert machine.commands[-1] == ["/opt/homebrew/bin/tool", "--version"]


def test_the_new_tools_directory_joins_the_path_of_the_continuing_process(make_kernel):
    machine = Machine(present={BREW: ""}, path="/usr/bin:/bin")
    machine.provides["tool"] = {"/opt/homebrew/bin/tool": "tool 1.2"}
    service = engine(machine, make_kernel, example=owner(tool()))

    service.install()
    service.install()

    assert machine.environment["PATH"] == "/usr/bin:/bin:/opt/homebrew/bin"


def test_casks_and_formulae_install_separately_and_each_recipe_once(make_kernel):
    machine = Machine(present={BREW: ""})
    machine.provides |= {"one": {"/opt/homebrew/bin/one": "1.0"}, "app": {"/opt/homebrew/bin/app": "2.0"}}
    first = owner(tool("one", Homebrew("one")), tool("app", Homebrew("app", cask=True)), name="first")
    second = owner(tool("one", Homebrew("one")), name="second")

    report = engine(machine, make_kernel, first=first, second=second).install()

    installs = [c for c in machine.commands if c[:2] == [BREW, "install"]]
    assert installs == [[BREW, "install", "--formula", "one"], [BREW, "install", "--cask", "app"]]
    assert {c.status for c in report.checks} == {"installed"}


def test_a_failing_homebrew_leaves_the_tool_failed_with_the_command_to_see_why(make_kernel):
    machine = Machine(present={BREW: ""})
    machine.brew_code = 1

    report = engine(machine, make_kernel, example=owner(tool())).install()

    [check] = report.checks
    assert (report.ready, check.status) == (False, "failed")
    assert "exited with status 1" in check.detail
    assert "brew install tool" in check.detail


def test_a_slow_homebrew_is_reported_as_too_slow(make_kernel):
    machine = Machine(present={BREW: ""})
    machine.brew_timeout = True

    [check] = engine(machine, make_kernel, example=owner(tool())).install().checks

    assert (check.status, check.detail.startswith("Homebrew took too long.")) == ("failed", True)


def test_a_homebrew_that_cannot_start_is_a_failure_not_a_crash(make_kernel):
    machine = Machine(present={BREW: ""})
    machine.unstartable.add("brew")

    [check] = engine(machine, make_kernel, example=owner(tool())).install().checks

    assert (check.status, "could not be started" in check.detail) == ("failed", True)


def test_exit_zero_without_a_working_tool_is_not_success(make_kernel):
    machine = Machine(present={BREW: ""})

    report = engine(machine, make_kernel, example=owner(tool())).install()

    assert report.ready is False
    assert report.checks[0].status == "failed"
    assert "still not usable" in report.checks[0].detail


def test_a_tool_that_works_after_a_failing_homebrew_is_what_counts(make_kernel):
    machine = Machine(present={BREW: ""})
    machine.brew_code = 1
    machine.provides_when_failing["tool"] = {"/opt/homebrew/bin/tool": "tool 1.0"}

    report = engine(machine, make_kernel, example=owner(tool())).install()

    assert [c[:2] for c in machine.commands if c[0] == BREW] == [[BREW, "install"]]
    assert (report.ready, report.checks[0].status) == (True, "installed")


def test_a_tool_that_is_too_old_after_the_install_stays_incompatible(make_kernel):
    machine = Machine(present={BREW: ""})
    machine.provides["tool"] = {"/opt/homebrew/bin/tool": "tool 1.0"}

    [check] = engine(machine, make_kernel, example=owner(tool(minimum_version="2.0"))).install().checks

    assert (check.status, check.version) == ("incompatible", "1.0")


def test_one_failing_package_does_not_hide_the_others_that_installed(make_kernel):
    machine = Machine(present={BREW: ""})
    machine.provides |= {"one": {"/opt/homebrew/bin/one": "1.0"}, "app": {"/opt/homebrew/bin/app": "2.0"}}
    machine.failing_packages.add("app")
    both = owner(tool("one", Homebrew("one")), tool("app", Homebrew("app", cask=True)))

    report = engine(machine, make_kernel, example=both).install()

    assert {c.requirement.name: c.status for c in report.checks} == {"one": "installed", "app": "failed"}
    assert "brew install app" in report.checks[1].detail


def test_nothing_runs_when_everything_is_ready(make_kernel):
    machine = Machine(present={"/usr/bin/tool": "tool 1.0", BREW: ""})

    report = engine(machine, make_kernel, example=owner(tool())).install()

    assert report.ready is True
    assert all(c[0] != BREW for c in machine.commands)


def test_manual_tools_are_reported_after_automatic_ones_were_installed(make_kernel):
    machine = Machine(present={BREW: ""})
    machine.provides["auto"] = {"/opt/homebrew/bin/auto": "1.0"}
    kernel_owner = owner(tool("auto", Homebrew("auto")), tool("hand", ManualInstall("Do it by hand.")))

    report = engine(machine, make_kernel, example=kernel_owner).install()

    assert {c.requirement.name: c.status for c in report.checks} == {"auto": "installed", "hand": "manual"}
    assert report.ready is False


def test_apple_stubs_are_never_run_without_the_command_line_tools(make_kernel):
    machine = Machine(present={"/usr/bin/tool": "tool 2.39"})
    machine.developer_tools = False

    [check] = engine(machine, make_kernel, example=owner(tool())).check().checks

    assert check.status == "missing"
    assert machine.commands == [["xcode-select", "-p"]]


def test_apple_stubs_run_once_the_command_line_tools_exist(make_kernel):
    machine = Machine(present={"/usr/bin/tool": "tool 2.39", "/usr/bin/other": "other 1.0"})
    both = owner(tool(), tool("other", None))

    report = engine(machine, make_kernel, example=both).check()

    assert [c.status for c in report.checks] == ["available", "available"]
    assert [c[0] for c in machine.commands].count("xcode-select") == 1


def test_a_missing_xcode_select_counts_as_no_command_line_tools(make_kernel):
    machine = Machine(present={"/usr/bin/tool": "tool 2.39"})
    machine.unstartable.add("xcode-select")

    assert engine(machine, make_kernel, example=owner(tool())).check().checks[0].status == "missing"


def test_on_linux_usr_bin_is_an_ordinary_directory(make_kernel):
    machine = Machine("linux", present={"/usr/bin/tool": "tool 2.39"})

    assert engine(machine, make_kernel, example=owner(tool())).check().ready is True
    assert all(c[0] != "xcode-select" for c in machine.commands)


def test_owners_that_disagree_on_a_recipe_fail_closed(make_kernel):
    machine = Machine(present={BREW: ""})
    one, two = owner(tool(macos=Homebrew("tool-a")), name="one"), owner(tool(macos=Homebrew("tool-b")), name="two")

    report = engine(machine, make_kernel, one=one, two=two).install()

    assert {c.status for c in report.checks} == {"failed"}
    assert all("conflicting" in c.detail for c in report.checks)
    assert all(c[0] != BREW for c in machine.commands)


def test_a_plugin_without_a_homebrew_recipe_does_not_conflict_with_core_wording(make_kernel):
    machine = Machine(present={BREW: ""})
    core, plugin = owner(tool(), name="core"), owner(tool(macos=None), name="plugin")

    report = engine(machine, make_kernel, core=core, plugin=plugin).check()

    assert {c.status for c in report.checks} == {"missing"}


def test_a_scoped_install_still_sees_a_conflict_with_another_module(make_kernel):
    machine = Machine(present={BREW: ""})
    one, two = owner(tool(macos=Homebrew("tool-a")), name="one"), owner(tool(macos=Homebrew("tool-b")), name="two")

    report = engine(machine, make_kernel, one=one, two=two).install("one")

    assert [c.status for c in report.checks] == ["failed"]
    assert all(c[0] != BREW for c in machine.commands)


@pytest.mark.parametrize(
    ("when", "context", "status"),
    [
        ("install", "install", "available"),
        ("setup", "install", "skipped"),
        ("feature", "install", "skipped"),
        ("install", "setup", "available"),
        ("setup", "setup", "available"),
        ("feature", "setup", "skipped"),
        ("feature", "feature", "available"),
        ("setup", "feature", "available"),
    ],
)
def test_a_context_selects_its_own_and_narrower_declarations(make_kernel, when: str, context: str, status: str):
    machine = Machine(present={"/usr/bin/tool": "tool 1.0"})

    [check] = engine(machine, make_kernel, example=owner(tool(when=when))).check(context=context).checks  # type: ignore[arg-type]

    assert check.status == status


def selected(*, answer: object) -> Callable[[], object]:
    return lambda: answer


def test_a_feature_with_a_predicate_joins_setup_when_the_selection_needs_it(make_kernel):
    machine = Machine(present={"/usr/bin/tool": "tool 1.0"})
    wanted = tool(when="feature", needed=selected(answer=True), condition="Chosen.")
    unwanted = tool("other", when="feature", needed=selected(answer=False), condition="Chosen.")

    checks = engine(machine, make_kernel, example=owner(wanted, unwanted)).check().checks

    assert {c.requirement.name: c.status for c in checks} == {"tool": "available", "other": "skipped"}


@pytest.mark.parametrize(
    ("answer", "context", "status"),
    [(True, "install", "skipped"), (True, "feature", "available"), (False, "feature", "skipped")],
)
def test_a_feature_predicate_is_never_part_of_the_installer_and_decides_in_the_feature_context(
    make_kernel, answer: bool, context: str, status: str
):
    machine = Machine(present={"/usr/bin/tool": "tool 1.0"})
    declaration = tool(when="feature", needed=selected(answer=answer), condition="Chosen.")

    [check] = engine(machine, make_kernel, example=owner(declaration)).check(context=context).checks  # type: ignore[arg-type]

    assert check.status == status


def test_a_predicate_that_raises_or_does_not_answer_is_a_finding_never_permission_to_install(make_kernel):
    def broken() -> bool:
        raise RuntimeError

    machine = Machine(present={BREW: ""})
    declarations = owner(
        tool("raises", when="install", needed=broken, condition="x"),
        tool("vague", when="install", needed=selected(answer="yes"), condition="x"),
    )

    report = engine(machine, make_kernel, example=declarations).install()

    assert {c.status for c in report.checks} == {"failed"}
    assert all(c[0] != BREW for c in machine.commands)


def test_a_predicate_can_read_the_selection_through_injected_settings(make_kernel):
    from heinzel import ModuleSettings

    class Choice(ModuleSettings):
        on: bool = False

    def chosen(settings: Choice) -> bool:
        return settings.on

    module = Module("example", "An example.")
    module.settings(Choice)
    tool(when="feature", needed=chosen, condition="It is on.")(module)
    machine = Machine(present={"/usr/bin/tool": "tool 1.0"})

    service = engine(machine, make_kernel, example=module)

    assert service.check().checks[0].status == "skipped"
    service.kernel.resolve(Choice).__dict__["on"] = True  # the person turned the feature on
    assert service.check().checks[0].status == "available"


def test_a_check_is_limited_to_the_module_asked_for(make_kernel):
    machine = Machine(present={"/usr/bin/a": "1.0", "/usr/bin/b": "1.0"})
    one, two = owner(tool("a"), name="one"), owner(tool("b"), name="two")
    service = engine(machine, make_kernel, one=one, two=two)

    assert [c.requirement.name for c in service.check("two").checks] == ["b"]
    assert [d.name for d in service.definitions("one")] == ["a"]
    assert {c.requirement.name for c in service.check().checks} == {"a", "b"}


def test_an_unknown_module_or_context_is_refused_with_what_to_do(make_kernel):
    service = engine(Machine(), make_kernel, example=owner(tool()))

    with pytest.raises(HeinzelError, match="hz system prerequisite list"):
        service.check("nobody")
    with pytest.raises(HeinzelError, match="install, setup or feature"):
        service.check(context="later")  # type: ignore[arg-type]


def test_the_export_never_probes_runs_or_evaluates(make_kernel):
    def explode() -> bool:
        raise AssertionError

    machine = Machine(present={BREW: ""})
    declaration = tool(when="feature", needed=explode, condition="Chosen.", minimum_version="1.2")

    [definition] = engine(machine, make_kernel, example=owner(declaration)).definitions()

    assert (definition.module, definition.name, definition.condition, definition.minimum_version) == (
        "example",
        "tool",
        "Chosen.",
        "1.2",
    )
    assert definition.plugin == "heinzel-test"
    assert definition.macos == Homebrew("tool")
    assert machine.commands == []
    assert "needed" not in definition.model_dump()


def test_the_suite_describes_the_real_machine_as_a_linux_host_that_refuses_homebrew():
    host = ToolHost.system()

    assert host.platform == "linux"
    with pytest.raises(AssertionError, match="real brew"):
        host.execute(["/opt/homebrew/bin/brew", "--version"], input=None, cwd=None, env={}, timeout=1.0)


def test_a_tool_directory_joins_the_path_once():
    host = ToolHost(environment={"PATH": "/a:/b"})

    host.add_to_path("/c")
    host.add_to_path("/a")

    assert host.environment["PATH"] == "/a:/b:/c"


def test_declarations_are_found_through_the_kernel(make_kernel):
    kernel = make_kernel(example=owner(tool()))

    [(record, let)] = kernel.lets(PrerequisiteLet)

    assert (record.qualname, let.name) == ("example", "tool")


def test_the_real_defaults_find_only_executable_files_on_the_given_path(tmp_path):
    runnable, plain = tmp_path / "runnable", tmp_path / "plain"
    for file in (runnable, plain):
        file.write_text("#!/bin/sh\n")
    runnable.chmod(0o755)
    plain.chmod(0o644)
    host = ToolHost(environment={"PATH": str(tmp_path)})

    assert host.which("runnable", host.search_path) == str(runnable)
    assert host.which("plain", host.search_path) is None
    assert host.is_executable(str(runnable))
    assert not host.is_executable(str(plain))
    assert not host.is_executable(str(tmp_path / "absent"))


def test_a_home_that_does_not_exist_does_not_stop_a_probe_from_running(tmp_path):
    host = ToolHost(environment={"HOME": str(tmp_path / "gone")}, execute=lambda *args, **options: options["cwd"])  # type: ignore[arg-type,return-value]

    assert host.run(["tool"], 1.0) == Path("/")
