"""The CLI head's setup runner (ADR 0032), with scripted answers and test modules."""

import io

import pytest
from pydantic import Field
from rich.console import Console

from heinzel import (
    Config,
    ConfigEditor,
    CredentialLet,
    Credentials,
    HeinzelError,
    Homebrew,
    Kernel,
    ManualInstall,
    Module,
    ModuleSettings,
    PrerequisiteService,
    Progress,
    Prompter,
)
from heinzel.heads.cli import _setup
from heinzel.heads.cli._setup import (
    ConsolePrompter,
    Outcome,
    Runner,
    StepResult,
    equivalent_commands,
    exit_code,
    is_configured,
    prerequisite_owners,
    steps,
    summary,
)
from heinzel.kernel import INTERRUPT, ModuleRecord, Origin, ScriptedPrompter, ScriptExhausted, discover
from heinzel.modules.system.prerequisite._service import ToolPrerequisites
from heinzel.testing import MemoryBackend
from tests._prerequisite_machine import Machine

pytestmark = pytest.mark.unit

GREETING_SET = Config(files=(), data={"greet": {"greeting": "Moin"}})
BUILTIN = Origin(distribution="heinzel", version="9.9.9", builtin=True)
PLUGIN = Origin(distribution="heinzel-test", version="9.9.9", builtin=False)


class GreetSettings(ModuleSettings):
    """Settings of the greet module."""

    greeting: str = Field(default="Hello", description="How to greet.")
    nickname: str | None = Field(default=None, description="Optional nickname.")


def greet_module() -> Module:
    module = Module("greet", "Greets.")
    module.settings(GreetSettings)

    @module.setup
    def setup(prompter: Prompter, settings: GreetSettings, editor: ConfigEditor) -> None:
        """Choose a greeting."""
        editor.write("greet.greeting", prompter.text("Greeting", default=settings.greeting))

    return module


def vault_module() -> Module:
    module = Module("vault", "Keeps a token.")
    token = module.credential("api-token", purpose="Token for the vault, from vault.example.org")

    @module.setup(required=True)
    def setup(prompter: Prompter, credentials: Credentials) -> None:
        """Store the vault token."""
        credentials.set(token, prompter.secret("Token"))

    return module


def builtin_module() -> Module:
    module = Module("core", "A built-in.")

    @module.setup
    def setup(prompter: Prompter) -> None:
        """Say hello."""
        prompter.say("hello")

    return module


def kernel_of(config: Config | None = None, **modules: tuple[Module, Origin]) -> Kernel:
    records = [ModuleRecord(qualname=name, module=module, origin=origin) for name, (module, origin) in modules.items()]
    # The built-in org step asks who you are first in every run; these tests are about the runner, so it is left out.
    builtins = [record for record in discover(plugins=False)[0] if record.qualname != "org"]
    kernel = Kernel(records=[*builtins, *records], config=config or Config.empty())
    kernel.bind(Credentials, Credentials(MemoryBackend(), [(r.qualname, let) for r, let in _credential_lets(kernel)]))
    return kernel


def _credential_lets(kernel: Kernel):
    return kernel.lets(CredentialLet)


@pytest.fixture
def kernel(make_kernel, monkeypatch, tmp_path) -> Kernel:
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    return kernel_of(
        greet=(greet_module(), PLUGIN),
        vault=(vault_module(), PLUGIN),
        core=(builtin_module(), BUILTIN),
        plain=(Module("plain", "No step."), PLUGIN),
    )


def test_builtins_come_first_then_the_others_by_name(kernel):
    assert [record.qualname for record, _ in steps(kernel)] == ["core", "greet", "vault"]
    assert {record.qualname: let for record, let in steps(kernel)}["vault"].required


def test_a_step_needing_a_disabled_module_is_left_out(make_kernel, settings):
    provider = Module("provider", "Provides.")

    class Service:
        pass

    provider.service(Service)
    needy = Module("needy", "Needs the service.")

    @needy.setup
    def setup(service: Service) -> None:
        """Use it."""

    disabled = settings.model_copy(update={"disabled_modules": frozenset({"provider"})})
    assert steps(make_kernel(settings_=disabled, provider=provider, needy=needy)) == []


def test_configured_when_a_setting_is_set_or_a_credential_stored(kernel):
    assert not is_configured(kernel, kernel.module("greet"))
    assert not is_configured(kernel, kernel.module("vault"))
    assert not is_configured(kernel, kernel.module("plain"))
    fresh = kernel_of(GREETING_SET, greet=(greet_module(), PLUGIN))
    assert is_configured(fresh, fresh.module("greet"))
    credentials = kernel.resolve(Credentials)
    credentials.set(next(d.let for d in credentials.declared if d.let.name == "api-token"), "s3cret")
    assert is_configured(kernel, kernel.module("vault"))


def test_an_unreadable_keychain_counts_as_not_configured(kernel):
    class Broken(MemoryBackend):
        def get(self, service, name):
            msg = "locked"
            raise HeinzelError(msg)

    kernel.bind(Credentials, Credentials(Broken(), [(r.qualname, let) for r, let in _credential_lets(kernel)]))
    assert not is_configured(kernel, kernel.module("vault"))
    kernel.bind(Credentials, Credentials(Broken(), []))
    assert equivalent_commands(kernel, kernel.module("vault")) == []


def test_equivalent_commands_set_each_setting_and_store_each_credential(kernel):
    assert equivalent_commands(kernel, kernel.module("greet")) == ["hz system config set greet.greeting '\"Hello\"'"]
    assert equivalent_commands(kernel, kernel.module("vault")) == ["hz system credential set api-token --service vault"]


def test_without_a_credential_service_nothing_is_stored_or_listed(monkeypatch, tmp_path):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    records = [ModuleRecord(qualname="vault", module=vault_module(), origin=PLUGIN)]
    kernel = Kernel(records=records, config=Config.empty())
    assert not is_configured(kernel, kernel.module("vault"))
    assert equivalent_commands(kernel, kernel.module("vault")) == []


def test_a_run_skips_optional_steps_on_request_and_never_required_ones(kernel):
    prompter = ScriptedPrompter(["", "n", "s3cret"])
    results = Runner(kernel, prompter).run(steps(kernel), offer_skip=True)
    assert results == [
        StepResult(module="core", required=False, outcome=Outcome.DONE),
        StepResult(module="greet", required=False, outcome=Outcome.SKIPPED),
        StepResult(module="vault", required=True, outcome=Outcome.DONE),
    ]
    assert "Set up greet now? [Y/n] n" in prompter.transcript
    assert not any(line.startswith("Set up vault") for line in prompter.transcript)
    assert "vault (required): Store the vault token." in prompter.transcript
    assert exit_code(results) == 0


def test_a_configured_module_is_offered_a_change():
    fresh = kernel_of(GREETING_SET, greet=(greet_module(), PLUGIN))
    prompter = ScriptedPrompter(["", "y", "Servus"])
    assert Runner(fresh, prompter).run(steps(fresh), offer_skip=True)[0].outcome is Outcome.KEPT
    assert "greet is already set up. Change it? [y/N] " in prompter.transcript
    assert Runner(fresh, prompter).run(steps(fresh), offer_skip=True)[0].outcome is Outcome.DONE
    assert "Greeting [Moin]: Servus" in prompter.transcript


def test_one_module_runs_without_the_skip_question(kernel):
    prompter = ScriptedPrompter(["Hi"])
    plan = [(record, let) for record, let in steps(kernel) if record.qualname == "greet"]
    assert Runner(kernel, prompter).run(plan, offer_skip=False)[0].outcome is Outcome.DONE
    assert prompter.transcript[-1] == "Greeting [Hello]: Hi"


def test_ctrl_c_keeps_finished_steps_and_reaches_no_further(kernel, tmp_path):
    prompter = ScriptedPrompter(["", "", "Hi", INTERRUPT])
    results = Runner(kernel, prompter).run(steps(kernel), offer_skip=True)
    assert [r.outcome for r in results] == [Outcome.DONE, Outcome.DONE, Outcome.INTERRUPTED]
    assert 'greeting = "Hi"' in (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert exit_code(results) == 130

    prompter = ScriptedPrompter([INTERRUPT])
    results = Runner(kernel, prompter).run(steps(kernel), offer_skip=True)
    assert [r.outcome for r in results] == [Outcome.INTERRUPTED, Outcome.NOT_REACHED, Outcome.NOT_REACHED]


def test_a_failing_step_is_reported_and_the_rest_still_run(make_kernel):
    def failing(message: str, error: Exception) -> Module:
        module = Module(message, "Fails.")

        @module.setup
        def setup() -> None:
            """Fail."""
            raise error

        return module

    kernel = make_kernel(a=failing("a", HeinzelError("no access")), b=failing("b", RuntimeError("boom")))
    prompter = ScriptedPrompter(["", ""])
    results = Runner(kernel, prompter).run(steps(kernel), offer_skip=True)
    assert results[0] == StepResult(module="a", required=False, outcome=Outcome.FAILED, detail="no access")
    assert results[1].outcome is Outcome.FAILED
    assert results[1].detail.startswith("RuntimeError: boom (a bug in the step")
    assert exit_code(results) == 1


def test_a_script_running_out_of_answers_fails_the_test_not_the_step(kernel):
    with pytest.raises(ScriptExhausted):
        Runner(kernel, ScriptedPrompter([""])).run(steps(kernel), offer_skip=True)


def test_summary_lists_every_step():
    console = Console(file=io.StringIO(), width=80)
    console.print(
        summary(
            [
                StepResult(module="clock", required=False, outcome=Outcome.SKIPPED),
                StepResult(module="vault", required=True, outcome=Outcome.DONE),
            ]
        )
    )
    text = console.file.getvalue()
    assert "clock" in text
    assert "skipped" in text
    assert "required" in text


def test_the_console_prompter_reads_lines_and_says_text_as_written(monkeypatch):
    out = io.StringIO()
    prompter = ConsolePrompter(Console(file=out, width=80))
    monkeypatch.setattr("sys.stdin", io.StringIO("Ada\n"))
    assert prompter.interactive
    assert prompter.read("Name [x]: ") == "Ada"
    prompter.say("[bold]as written[/bold]\x1b[31m")
    assert "[bold]as written[/bold]" in out.getvalue()
    assert "\x1b" not in out.getvalue()
    with pytest.raises(KeyboardInterrupt):
        prompter.read("More: ")


def test_the_console_prompter_hides_secrets(monkeypatch):
    prompter = ConsolePrompter(Console(file=io.StringIO()))
    monkeypatch.setattr(_setup.getpass, "getpass", lambda prompt: f"hidden for {prompt}")
    assert prompter.read("Token: ", secret=True) == "hidden for Token: "


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("stdin", "stdout", "expected"),
    [(Terminal, Terminal, True), (Terminal, io.StringIO, False), (io.StringIO, Terminal, False)],
)
def test_setup_needs_a_terminal_on_stdin_and_stdout(monkeypatch, stdin, stdout, expected):
    """`hz setup > out.txt` would wait for answers to questions nobody sees, so it refuses like a pipe on stdin."""
    monkeypatch.setattr("sys.stdin", stdin())
    monkeypatch.setattr("sys.stdout", stdout())
    assert _setup.has_terminal() is expected


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (KeyError("zones"), "KeyError: 'zones' (a bug in the step"),
        (PermissionError("config.toml is read-only"), "config.toml is read-only"),
    ],
)
def test_lookup_and_file_errors_of_a_step_are_failures_not_crashes(make_kernel, error, detail):
    module = Module("broken", "Fails.")

    @module.setup
    def setup() -> None:
        """Fail."""
        raise error

    kernel = make_kernel(broken=module)
    results = Runner(kernel, ScriptedPrompter([""])).run(steps(kernel), offer_skip=True)
    assert results[0].outcome is Outcome.FAILED
    assert results[0].detail.startswith(detail)


def test_invalid_settings_of_the_module_name_the_reset_command(monkeypatch, tmp_path):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    invalid = Config(files=(), data={"greet": {"greeting": ["not", "text"]}})
    kernel = kernel_of(invalid, greet=(greet_module(), PLUGIN))
    result = Runner(kernel, ScriptedPrompter()).run(steps(kernel), offer_skip=True)[-1]
    assert result.outcome is Outcome.FAILED
    assert "its configuration is invalid: GreetSettings greeting:" in result.detail
    assert result.detail.endswith("`hz system config unset greet.greeting` resets it, then run setup again")


def test_invalid_settings_of_another_module_name_no_reset_command(make_kernel):
    other = Module("other", "Has settings.")
    other.settings(GreetSettings)
    needy = Module("needy", "Uses them.")

    @needy.setup
    def setup(settings: GreetSettings) -> None:
        """Use them."""

    kernel = make_kernel(other=other, needy=needy, config=Config(files=(), data={"other": {"greeting": [1]}}))
    result = Runner(kernel, ScriptedPrompter([""])).run(steps(kernel), offer_skip=True)[0]
    assert result.outcome is Outcome.FAILED
    assert "resets it" not in result.detail


def test_the_console_prompter_refuses_a_secret_it_would_echo(monkeypatch):
    def echoing(prompt: str) -> str:
        import warnings

        warnings.warn("Can not control echo on the terminal.", _setup.getpass.GetPassWarning, stacklevel=1)
        return "visible"

    monkeypatch.setattr(_setup.getpass, "getpass", echoing)
    with pytest.raises(HeinzelError, match="can't hide what you type"):
        ConsolePrompter(Console(file=io.StringIO())).read("Token: ", secret=True)


# Tools before steps (ADR 0112) ---------------------------------------------------------------------------------------

LINUX_HELP = ManualInstall("Install it with your package manager.", "https://example.test/install")
BREW = "/opt/homebrew/bin/brew"


def tooled_module(*, when: str = "setup") -> Module:
    module = greet_module()
    module.prerequisite(
        "tool", title="Tool", purpose="Greets in style.", linux=LINUX_HELP, macos=Homebrew("tool"), when=when
    )
    return module


def tool_kernel(machine: Machine, module: Module | None = None, origin: Origin = PLUGIN) -> Kernel:
    kernel = kernel_of(greet=(module or tooled_module(), origin))
    kernel.bind(PrerequisiteService, ToolPrerequisites(kernel, Progress(), machine.host()))
    return kernel


def greet_plan(kernel: Kernel):
    return [(record, let) for record, let in steps(kernel) if record.qualname == "greet"]


def test_tools_that_work_leave_the_step_exactly_as_it_was(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    machine = Machine(present={"/usr/bin/tool": "tool 1.0"})
    kernel = tool_kernel(machine)
    prompter = ScriptedPrompter(["Moin"])

    [result] = Runner(kernel, prompter).run(greet_plan(kernel), offer_skip=False)

    assert result.outcome is Outcome.DONE
    assert prompter.transcript == ["", "greet (optional): Choose a greeting.", "Greeting [Hello]: Moin"]


def test_a_missing_tool_is_installed_after_a_yes_and_before_the_owners_own_step(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    machine = Machine(present={BREW: ""})
    machine.provides["tool"] = {"/opt/homebrew/bin/tool": "tool 1.2"}
    kernel = tool_kernel(machine)
    prompter = ScriptedPrompter(["y", "Moin"])

    [result] = Runner(kernel, prompter).run(greet_plan(kernel), offer_skip=False)

    assert result.outcome is Outcome.DONE
    assert machine.files["/opt/homebrew/bin/tool"] == "tool 1.2"
    asked = [line for line in prompter.transcript if line.endswith(("[Y/n] y", ": Moin"))]
    assert asked == ["Install tool with Homebrew now? [Y/n] y", "Greeting [Hello]: Moin"]
    assert "Tool (tool) is not ready for greet: Greets in style." in prompter.transcript
    assert 'greeting = "Moin"' in (tmp_path / "config.toml").read_text(encoding="utf-8")


def test_declining_the_install_leaves_the_step_unrun_and_failed_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    machine = Machine(present={BREW: ""})
    kernel = tool_kernel(machine)
    prompter = ScriptedPrompter(["n"])

    [result] = Runner(kernel, prompter).run(greet_plan(kernel), offer_skip=False)

    assert result.outcome is Outcome.FAILED
    assert result.detail.startswith("Its tools are not ready, so the step did not run: tool (")
    assert not (tmp_path / "config.toml").exists()
    assert not [c for c in machine.commands if c[:2] == [BREW, "install"]]
    assert exit_code([result]) == 1


def test_on_linux_the_owners_instructions_are_shown_and_nothing_is_asked_or_installed(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    machine = Machine("linux")
    kernel = tool_kernel(machine)
    prompter = ScriptedPrompter([])

    [result] = Runner(kernel, prompter).run(greet_plan(kernel), offer_skip=False)

    assert result.outcome is Outcome.FAILED
    assert "  Install it with your package manager. https://example.test/install" in prompter.transcript
    assert machine.commands == []


def test_without_a_person_nothing_is_installed_and_the_step_waits(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    machine = Machine(present={BREW: ""})
    kernel = tool_kernel(machine)
    prompter = Prompter()
    prompter.presets = {"greet.greeting": "Moin"}

    [result] = Runner(kernel, prompter, from_file=False).run(greet_plan(kernel), offer_skip=False)

    assert result.outcome is Outcome.FAILED
    assert [c for c in machine.commands if c[0] == BREW] == []


def test_a_failed_install_after_a_yes_is_shown_and_the_step_does_not_run(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    machine = Machine(present={BREW: ""})
    machine.brew_code = 1
    kernel = tool_kernel(machine)
    prompter = ScriptedPrompter(["y"])

    [result] = Runner(kernel, prompter).run(greet_plan(kernel), offer_skip=False)

    assert result.outcome is Outcome.FAILED
    assert any("exited with status 1" in line for line in prompter.transcript)
    assert not (tmp_path / "config.toml").exists()


class Listening(Prompter):
    """Nobody to ask, but what the run says is kept."""

    def __init__(self) -> None:
        self.said: list[str] = []

    def say(self, text: str) -> None:
        self.said.append(text)


def test_without_a_person_the_command_that_would_install_is_printed(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    kernel = tool_kernel(Machine(present={BREW: ""}))
    prompter = Listening()

    Runner(kernel, prompter, from_file=False).run(greet_plan(kernel), offer_skip=False)

    assert (
        "  Nothing was installed. To install: hz system prerequisite install --owner greet --confirm" in prompter.said
    )


def test_tools_that_cannot_be_checked_leave_a_pending_row_not_a_crash():
    kernel = tools_kernel(Machine("linux"))

    def broken(*args: object, **kwargs: object) -> None:
        msg = "probe exploded"
        raise OSError(msg)

    kernel.resolve(PrerequisiteService).check = broken  # type: ignore[method-assign]

    [result] = Runner(kernel, ScriptedPrompter([])).run([], offer_skip=True, owners=[tools_owner(kernel)])

    assert (result.outcome, result.prerequisites) == (Outcome.PENDING, True)
    assert result.detail == "its tools could not be checked: probe exploded"


def test_a_skipped_optional_step_installs_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    machine = Machine(present={BREW: ""})
    kernel = tool_kernel(machine)

    [result] = Runner(kernel, ScriptedPrompter(["n"])).run(greet_plan(kernel), offer_skip=True)

    assert result.outcome is Outcome.SKIPPED
    assert machine.commands == []


def test_a_feature_tool_is_requested_only_when_the_selection_needs_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    module = greet_module()
    module.prerequisite(
        "tool", purpose="Greets.", linux=LINUX_HELP, when="feature", needed=lambda: False, condition="Chosen."
    )
    kernel = tool_kernel(Machine("linux"), module)

    [result] = Runner(kernel, ScriptedPrompter(["Hi"])).run(greet_plan(kernel), offer_skip=False)

    assert result.outcome is Outcome.DONE


def test_a_kernel_without_the_prerequisite_service_runs_steps_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    records = [
        ModuleRecord(qualname="greet", module=tooled_module(), origin=PLUGIN),
        *(record for record in discover(plugins=False)[0] if record.qualname not in {"org", "system.prerequisite"}),
    ]
    kernel = Kernel(records=records, config=Config.empty())
    kernel.bind(Credentials, Credentials(MemoryBackend(), []))

    [result] = Runner(kernel, ScriptedPrompter(["Hi"])).run(greet_plan(kernel), offer_skip=False)

    assert result.outcome is Outcome.DONE


def owner_only() -> Module:
    module = Module("tools", "Needs a tool, asks nothing.")
    module.prerequisite("tool", title="Tool", purpose="Helps.", linux=LINUX_HELP, macos=Homebrew("tool"))
    return module


def tools_kernel(machine: Machine) -> Kernel:
    kernel = kernel_of(tools=(owner_only(), PLUGIN))
    kernel.bind(PrerequisiteService, ToolPrerequisites(kernel, Progress(), machine.host()))
    return kernel


def tools_owner(kernel: Kernel) -> ModuleRecord:
    return kernel.module("tools")


def test_owners_without_a_step_are_the_modules_that_declare_tools_and_have_none():
    kernel = kernel_of(greet=(tooled_module(), PLUGIN), tools=(owner_only(), PLUGIN))

    owners = prerequisite_owners(kernel, steps(kernel))

    assert [record.qualname for record in owners] == ["system", "system.notify", "system.runner", "tools"]


def test_an_owner_without_a_step_whose_tools_work_says_nothing():
    kernel = tools_kernel(Machine(present={"/usr/bin/tool": "tool 1.0"}))

    assert Runner(kernel, ScriptedPrompter([])).run([], offer_skip=True, owners=[tools_owner(kernel)]) == []


def test_an_owner_without_a_step_with_a_missing_tool_is_pending_not_failed():
    machine = Machine("linux")
    kernel = tools_kernel(machine)

    results = Runner(kernel, ScriptedPrompter([])).run([], offer_skip=True, owners=[tools_owner(kernel)])

    [result] = results
    assert (result.module, result.outcome, result.prerequisites) == ("tools", Outcome.PENDING, True)
    assert "tool: Install it with your package manager." in result.detail
    assert exit_code(results) == 0


def test_an_owner_without_a_step_is_prepared_after_a_yes():
    machine = Machine(present={BREW: ""})
    machine.provides["tool"] = {"/opt/homebrew/bin/tool": "tool 1.0"}
    kernel = tools_kernel(machine)

    [result] = Runner(kernel, ScriptedPrompter(["y"])).run([], offer_skip=True, owners=[tools_owner(kernel)])

    assert (result.outcome, result.prerequisites) == (Outcome.DONE, True)


def test_ctrl_c_while_preparing_an_owner_reaches_no_step(kernel):
    machine = Machine(present={BREW: ""})
    tooled = tools_kernel(machine)

    results = Runner(tooled, ScriptedPrompter([INTERRUPT])).run(
        steps(kernel), offer_skip=True, owners=[tools_owner(tooled)]
    )

    assert [r.outcome for r in results] == [Outcome.INTERRUPTED, *[Outcome.NOT_REACHED] * 3]
    assert machine.commands == []


def test_the_summary_names_the_tools_rows():
    table = summary([StepResult(module="tools", required=False, outcome=Outcome.PENDING, prerequisites=True)])

    assert [str(cell) for cell in table.columns[1].cells] == ["tools"]
    assert [str(cell) for cell in table.columns[2].cells] == ["pending"]
