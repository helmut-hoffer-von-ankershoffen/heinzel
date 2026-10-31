"""Prerequisite declarations (ADR 0112): what an owner may declare, and what a declaration refuses."""

import pytest

from heinzel import Homebrew, ManualInstall, Module, PrerequisiteCheck, PrerequisiteDefinition, PrerequisiteLet
from heinzel.kernel import PrerequisiteReport
from heinzel.kernel._kernel import ADAPTER_ONLY_LETS

pytestmark = pytest.mark.unit

LINUX = ManualInstall("Install it with your package manager.", "https://example.test/install")


def declare(module: Module | None = None, **changes: object) -> PrerequisiteLet:
    options: dict[str, object] = {"purpose": "Does a thing.", "linux": LINUX, "macos": Homebrew("example")}
    return (module or Module("example", "An example.")).prerequisite("tool", **{**options, **changes})  # type: ignore[arg-type]


def test_a_declaration_is_a_let_of_its_module_named_after_the_executable():
    module = Module("example", "An example.")
    let = declare(module, title="Tool", when="install")

    assert module.lets == (let,)
    assert (let.name, let.title, let.when, let.macos) == ("tool", "Tool", "install", Homebrew("example"))


def test_the_same_executable_is_declared_once_per_module():
    module = Module("example", "An example.")
    declare(module)

    with pytest.raises(ValueError, match="already has a PrerequisiteLet named 'tool'"):
        declare(module)


@pytest.mark.parametrize("name", ["", "/usr/bin/tool", "tool --version", "-tool", "a;b"])
def test_an_executable_is_a_bare_command_name(name: str):
    with pytest.raises(ValueError, match="command name"):
        Module("example", "An example.").prerequisite(name, purpose="Does a thing.", linux=LINUX)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"purpose": " "}, "purpose"),
        ({"when": "always"}, "context"),
        ({"version_args": ()}, "probes its version"),
        ({"version_args": ("--version", "")}, "probes its version"),
        ({"version_args": ("-c", "touch /tmp/owned")}, "probes its version"),
        ({"version_args": ("--version", "$(id)")}, "probes its version"),
        ({"title": "Tool\x1b[2J"}, "plain text"),
        ({"purpose": "Does a thing.\nAnd another."}, "plain text"),
        ({"condition": "Chosen\u202e."}, "plain text"),
        ({"minimum_version": "newest"}, "minimum version"),
        ({"needed": lambda: True}, "explanation"),
    ],
)
def test_an_unusable_declaration_is_refused_with_the_reason(changes: dict[str, object], message: str):
    with pytest.raises(ValueError, match=message):
        declare(**changes)


@pytest.mark.parametrize("probe", [("--version",), ("-version",), ("-V",), ("version",), ("version", "--short", "a=b")])
def test_a_version_probe_only_asks_for_a_version(probe: tuple[str, ...]):
    assert declare(version_args=probe).version_args == probe


@pytest.mark.parametrize(
    "link",
    ["https://example.test/a\nb", "https://example.test/a b", "https://example.test/\x1b[2J", "https://exämple.test/"],
)
def test_a_manual_link_is_one_ascii_printable_token(link: str):
    with pytest.raises(ValueError, match="HTTPS"):
        ManualInstall("Install it.", link)


@pytest.mark.parametrize("text", ["Install\nit.", "Install \x1b[31mit.", "Install\u200bit.", "Install\u2028it."])
def test_manual_instructions_are_one_line_without_invisible_characters(text: str):
    with pytest.raises(ValueError, match="plain text"):
        ManualInstall(text)


def test_a_conditional_declaration_keeps_its_wording_and_predicate():
    let = declare(needed=lambda: False, condition="Sharing is selected.", minimum_version="2.1")

    assert let.condition == "Sharing is selected."
    assert let.needed is not None
    assert let.minimum_version == "2.1"


@pytest.mark.parametrize("package", ["", "Git", "git;rm", "-flag", "a/b/c/d", "git "])
def test_a_homebrew_recipe_names_a_formula_or_cask(package: str):
    with pytest.raises(ValueError, match="formula or cask"):
        Homebrew(package)


def test_a_homebrew_recipe_may_name_its_tap():
    assert Homebrew("owner/tap/tool").package == "owner/tap/tool"
    assert Homebrew("claude-code", cask=True).cask


@pytest.mark.parametrize(
    "url",
    ["http://example.test", "https://user:pw@example.test", "https://", "file:///etc/passwd", "ftp://example.test"],
)
def test_manual_instructions_link_only_to_https_without_credentials(url: str):
    with pytest.raises(ValueError, match="HTTPS"):
        ManualInstall("Install it.", url)


def test_manual_instructions_must_say_something():
    with pytest.raises(ValueError, match="instructions"):
        ManualInstall("  ")


def test_manual_text_joins_instructions_and_link():
    assert ManualInstall(" Install it. ", "https://example.test/x").text == "Install it. https://example.test/x"
    assert ManualInstall("Install it.").text == "Install it."


def definition() -> PrerequisiteDefinition:
    return PrerequisiteDefinition(
        module="example", name="tool", title="Tool", purpose="Does a thing.", when="setup", macos=None, linux=LINUX
    )


def test_a_report_lists_what_still_needs_the_person():
    checks = [
        PrerequisiteCheck(requirement=definition(), status=status)
        for status in ("available", "installed", "skipped", "missing", "manual", "failed", "incompatible")
    ]

    report = PrerequisiteReport(ready=False, checks=checks)

    assert [check.status for check in report.unmet] == ["missing", "manual", "failed", "incompatible"]


def test_a_declaration_is_data_so_an_adapter_module_that_declares_one_stays_adapter_only():
    adapter = Module("adapter", "Serves a capability.", requires=["tracker:read"])
    adapter.prerequisite("tool", purpose="Does a thing.", linux=LINUX)
    operating = Module("operating", "Runs code.", requires=["tracker:read"])
    operating.prerequisite("tool", purpose="Does a thing.", linux=LINUX)

    @operating.operation
    def run() -> str:
        """Run."""
        return ""

    assert PrerequisiteLet in ADAPTER_ONLY_LETS
    assert all(isinstance(let, ADAPTER_ONLY_LETS) for let in adapter.lets)
    assert not all(isinstance(let, ADAPTER_ONLY_LETS) for let in operating.lets)
