"""`hz ui status` lists the desktop apps that connected, as the web server recorded them."""

from pathlib import Path

import pytest

from heinzel import Settings
from heinzel.modules import ui
from heinzel.modules.system.client import ConnectedClients, client_header, now_utc, parse_client
from heinzel.modules.system.lifecycle._service import WebStatus

pytestmark = pytest.mark.unit


class Service:
    def status(self) -> WebStatus:
        return WebStatus(installed=True, loaded=True, ready=True, version="0.9.54")


def test_status_adds_the_recorded_clients_newest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    state = Settings().state_dir
    ConnectedClients(state / "web").record(
        parse_client({"x-heinzel-client": client_header("native", "0.9.54", "0.9.54", "Air")}, now_utc())
    )
    result = ui.status(Service())  # type: ignore[arg-type]
    assert result.ready is True
    assert [(c.flavor, c.machine, c.backend_version) for c in result.clients] == [("native", "Air", "0.9.54")]


def test_status_without_recorded_clients_lists_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert ui.status(Service()).clients == []  # type: ignore[arg-type]
