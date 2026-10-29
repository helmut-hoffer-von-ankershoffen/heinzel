"""The resource meters consume one typed cached sample through CLI, MCP and the API."""

import json

import pytest
from typer.testing import CliRunner

from heinzel.heads.cli import build

pytestmark = pytest.mark.integration


def test_doctor_reports_the_server_hostname_in_json_and_text(make_kernel, monkeypatch):
    monkeypatch.setattr("heinzel.modules.system.doctor.gethostname", lambda: "workshop")
    kernel = make_kernel(builtins=True)
    app = build(kernel)
    report = CliRunner().invoke(app, ["system", "doctor", "--json"])
    checks = json.loads(report.stdout)["checks"]["system.doctor"]
    assert {"name": "hostname", "status": "up", "detail": "workshop", "fix": ""} in checks
    report = CliRunner().invoke(app, ["system", "doctor"])
    assert "workshop" in report.stdout
