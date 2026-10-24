"""The CLI head shows text that comes from configuration, plugins and contracts as written, never as Rich markup."""

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


def test_contract_names_are_plain_text(make_kernel, tmp_path):
    (tmp_path / "[reverse]x.prose.md").write_text("no frontmatter")
    config = Config(files=(), data={"workflow": {"user_dir": str(tmp_path)}})
    assert "me:[reverse]x" in output(make_kernel, "workflow", "list", config=config)
