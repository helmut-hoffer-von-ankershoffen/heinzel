"""The CLI head shows text that comes from configuration, plugins and contracts as written, never as Rich markup."""

import re
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from heinzel import Check, Config, HeinzelError, Module, Status
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

TRICKY = '[reverse]x and [capabilities] code_host = "github"'


class Report(BaseModel):
    note: str


def tricky() -> Module:
    module = Module("tricky", "Says things that look like markup.")

    @module.operation
    def fail() -> str:
        """Fail with a hint that quotes configuration."""
        raise HeinzelError(TRICKY)

    @module.operation
    def report() -> Report:
        """Return a model with a value that looks like markup."""
        return Report(note=TRICKY)

    @module.operation
    def crash() -> str:
        """Fail with a bug."""
        raise RuntimeError(TRICKY)

    @module.health
    def health() -> list[Check]:
        """A check whose text looks like markup."""
        return [Check(name="[reverse]name", status=Status.DEGRADED, detail=TRICKY, fix=TRICKY)]

    return module


def output(make_kernel, *args: str, config: Config | None = None) -> str:
    result = CliRunner().invoke(build(make_kernel(builtins=True, tricky=tricky(), config=config)), list(args))
    return result.output.replace("\n", "").replace("│", "").replace(" ", "")


def plain(text: str) -> str:
    return text.replace(" ", "")


def test_error_messages_are_plain_text(make_kernel):
    assert plain(TRICKY) in output(make_kernel, "tricky", "fail")
    assert plain(TRICKY) in output(make_kernel, "tricky", "crash")


def test_default_tables_are_plain_text(make_kernel):
    assert plain(TRICKY) in output(make_kernel, "tricky", "report")


def test_doctor_shows_check_text_as_written(make_kernel):
    text = output(make_kernel, "system", "doctor")
    assert "[reverse]name" in text
    assert "[capabil" in text  # folded across lines in a narrow column, but not swallowed as markup
    assert text.count("[reverse]") >= 2


ESCAPES = "\x1b[31mred\x1b[0m\x1b]8;;https://evil.invalid\x1b\\link\x1b]8;;\x1b\\\x1b]52;c;eA==\x07\x9b2J"
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def raw(make_kernel, *args: str, config: Config | None = None) -> str:
    result = CliRunner().invoke(build(make_kernel(builtins=True, tricky=tricky(), config=config)), list(args))
    return result.output


def test_config_show_writes_no_control_characters(make_kernel):
    config = Config(files=(), data={"system": {"runner": {"model": ESCAPES}}})
    output = raw(make_kernel, "system", "config", "show", config=config)
    assert "red" in output
    assert not CONTROL.search(output.replace("\n", ""))


def test_workflow_titles_and_diagnostics_write_no_control_characters(make_kernel, tmp_path):
    example = (Path(__file__).parents[1] / "fixtures" / "workflows" / "weekly-digest.prose.md").read_text()
    (tmp_path / "weekly-digest.prose.md").write_text(example.replace("Weekly app digest", ESCAPES))
    (tmp_path / "\x1b[31mred\x9b2J.prose.md").write_text("no frontmatter")
    config = Config(files=(), data={"heinzel": {"user_dir": str(tmp_path)}})
    for args in (("heinzel", "list"), ("heinzel", "check"), ("heinzel", "show", "weekly-digest")):
        output = raw(make_kernel, *args, config=config)
        assert not CONTROL.search(output.replace("\n", "")), args
    assert "HZC005" in raw(make_kernel, "heinzel", "check", config=config)


def test_error_messages_write_no_control_characters(make_kernel, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "TRICKY", ESCAPES)
    assert not CONTROL.search(raw(make_kernel, "tricky", "fail").replace("\n", ""))


def test_contract_names_are_plain_text(make_kernel, tmp_path):
    (tmp_path / "[reverse]x.prose.md").write_text("no frontmatter")
    config = Config(files=(), data={"heinzel": {"user_dir": str(tmp_path)}})
    assert "me:[reverse]x" in output(make_kernel, "heinzel", "list", config=config)
