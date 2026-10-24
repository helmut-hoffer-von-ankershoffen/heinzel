"""Shared fixtures: Open-Meteo's answers recorded once (Lisbon; the third day made rainy), and a client that serves
them, so no test goes online. heinzel.testing (a pytest plugin, loaded automatically) brings the rest: one level per
test with its timeout, and an isolated HZ_* environment."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from heinzel_openmeteo import FORECAST_HOST, GEOCODING_HOST, Client

from heinzel import Config, Kernel, Settings
from heinzel.kernel import discover

FIXTURES = Path(__file__).parent / "fixtures"


def recorded(name: str) -> dict[str, Any]:
    """A recorded answer from `tests/fixtures`."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RecordedClient(Client):
    """The client with the network replaced by the recorded answers; remembers what it was asked."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.geocoding = recorded("geocoding-lisbon.json")
        self.forecast_answer = recorded("forecast-lisbon.json")

    def get(self, host: str, path: str, params: dict[str, str]) -> Any:
        self.requests.append((host, path, params))
        return {GEOCODING_HOST: self.geocoding, FORECAST_HOST: self.forecast_answer}[host]


@pytest.fixture
def client() -> RecordedClient:
    return RecordedClient()


@pytest.fixture
def boot(client: RecordedClient, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Kernel]:
    """Boot a kernel the way `hz` does (built-in modules plus every installed plugin), asking the recorded client."""
    monkeypatch.setattr(Client, "get", lambda _self, host, path, params: client.get(host, path, params))

    def make(config: Config | None = None) -> Kernel:
        records, errors = discover()
        return Kernel(Settings(), records, errors, config or Config.empty())

    return make
