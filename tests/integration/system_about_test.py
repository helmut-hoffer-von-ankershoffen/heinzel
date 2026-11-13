"""`hz system about` through the CLI head: the lines the About dialog copies, and the same report with `--json`."""

import json

import pytest
from typer.testing import CliRunner

from heinzel import __version__
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_about_prints_the_diagnostics_lines_and_the_same_report_as_json(make_kernel):
    app = build(make_kernel(builtins=True))
    text = runner.invoke(app, ["system", "about"])
    assert text.exit_code == 0, text.output
    as_json = runner.invoke(app, ["system", "about", "--json"])
    assert as_json.exit_code == 0, as_json.output
    report = json.loads(as_json.output)
    assert text.output.strip().splitlines() == report["diagnostics"]
    assert report["version"] == __version__
    assert report["daemon"]["state"] in {"running", "paused", "stopped", "not installed", "unknown"}
    assert report["installation"] in {"uv tool", "environment"}
    assert report["source"] in {"PyPI", "git", "path"}
    assert report["diagnostics"][0].startswith(f"hz {__version__}")
    assert report["diagnostics"][3].startswith("desktop app ")
    assert report["diagnostics"][4].startswith("daemon ")
    # No interpreter path, no install folder: the lines are meant for pasting into a bug report.
    assert all("/" not in line for line in report["diagnostics"][:5])
