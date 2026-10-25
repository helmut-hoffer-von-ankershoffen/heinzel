"""`hz weather` through the CLI and the heads, served by a test-only adapter. Offline."""

import json
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import Config, CurrentWeather, DailyForecast, HeinzelError, Module, WeatherPlace, WeatherReport
from heinzel.heads import api, mcp, web
from heinzel.heads._exposure import exposed
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

TOKEN = "s3ssion-token-for-tests"
BASE = "http://127.0.0.1:8000"


class Almanac:
    """A test-only weather adapter: sunshine everywhere but at the pole."""

    def report(self, place: str) -> WeatherReport:
        if place == "Nowhere":
            msg = "The almanac knows no place called 'Nowhere'."
            raise HeinzelError(msg)
        return WeatherReport(
            place=WeatherPlace(name=place, latitude=1, longitude=2),
            current=CurrentWeather(
                observed_at=datetime(2026, 10, 25, 12, tzinfo=UTC),
                summary="sunny",
                temperature_c=21.0,
                feels_like_c=22.0,
                wind_kmh=3.0,
                precipitation_mm=0.0,
            ),
            days=[
                DailyForecast(
                    day=date(2026, 10, 25 + i),
                    summary="sunny",
                    high_c=24,
                    low_c=12,
                    rain_chance=0.1,
                    precipitation_mm=0,
                )
                for i in range(5)
            ],
        )


def almanac() -> Module:
    module = Module("almanac", "Provides weather for tests.")
    module.provides("weather", modes={"read"}, hosts=["almanac.invalid"])(Almanac)
    return module


def kernel(make_kernel, **config: dict):
    return make_kernel(builtins=True, almanac=almanac(), config=Config(files=(), data=config))


def hz(app, *args: str):
    result = CliRunner().invoke(app, [*args, "--json"])
    return result.exit_code, json.loads(result.output) if result.output.startswith(("{", "[")) else result.output


def test_any_adapter_serves_the_command_the_core_owns(make_kernel):
    app = build(
        kernel(make_kernel, weather={"locations": ["Oslo"], "units": "metric"}, capabilities={"weather": "almanac"})
    )
    code, outlook = hz(app, "weather", "--days", "2")
    assert code == 0, outlook
    assert (outlook["location"], outlook["adapter"], outlook["temperature"], len(outlook["days"])) == (
        "Oslo",
        "almanac",
        21.0,
        2,
    )


def test_an_adapters_error_is_shown_as_it_is(make_kernel):
    app = build(kernel(make_kernel, weather={"locations": ["Oslo"]}, capabilities={"weather": "almanac"}))
    code, output = hz(app, "weather", "--location", "Nowhere")
    assert code != 0
    assert "knows no place called 'Nowhere'" in output


def test_the_module_is_listed_and_the_adapters_host_is_declared(make_kernel):
    app = build(kernel(make_kernel, capabilities={"weather": "almanac"}))
    code, listing = hz(app, "system", "capability", "list")
    assert code == 0, listing
    assert "almanac.invalid" in json.dumps(listing)


def test_doctor_stays_up_while_no_adapter_is_bound_and_asks_for_a_location_once_one_is(make_kernel):
    code, report = hz(build(make_kernel(builtins=True)), "system", "doctor")
    assert code == 0, report
    [check] = [c for c in report["checks"]["weather"] if c["name"] == "locations"]
    assert check["status"] == "up"
    app = build(kernel(make_kernel, capabilities={"weather": "almanac"}))
    _, report = hz(app, "system", "doctor")
    [check] = [c for c in report["checks"]["weather"] if c["name"] == "locations"]
    assert check["status"] == "degraded"
    assert "weather.locations" in check["fix"]


def test_days_are_bounded_at_the_command_line(make_kernel):
    app = build(kernel(make_kernel, weather={"locations": ["Oslo"]}, capabilities={"weather": "almanac"}))
    assert hz(app, "weather", "--days", "0")[0] != 0
    assert hz(app, "weather", "--days", "8")[0] != 0


def test_the_web_head_has_it_behind_the_token_and_the_rest_head_does_not(make_kernel):
    k = kernel(make_kernel, weather={"locations": ["Oslo"]}, capabilities={"weather": "almanac"})
    assert "weather" in {item.name for item in exposed(k, web.HEAD)}
    assert "weather" in {item.name for item in exposed(k, mcp.HEAD)}
    assert "weather" not in {item.name for item in exposed(k, api.HEAD)}
    client = TestClient(web.build(k, port=8000, token=TOKEN, static=None), base_url=BASE)
    assert client.get("/api/weather").status_code == 401
    answer = client.get("/api/weather", headers={"Authorization": f"Bearer {TOKEN}"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["summary"] == "sunny"


def test_the_weather_shows_in_the_footer_and_sidebar_and_links_to_its_page(make_kernel):
    app = build(
        kernel(make_kernel, weather={"locations": ["Oslo"], "units": "metric"}, capabilities={"weather": "almanac"})
    )
    code, entries = hz(app, "system", "ui", "status")
    assert code == 0, entries
    [entry] = [e for e in entries if e["id"] == "weather:weather-now"]
    assert entry["problem"] is None, entry
    assert entry["slots"] == {"footer": True, "sidebar": True}
    assert entry["item"]["text"] == "21°C sunny"
    assert entry["item"]["effect"] == {"kind": "navigate", "path": "/weather"}


def test_the_status_item_asks_for_a_location_while_none_is_set_and_is_absent_without_an_adapter(make_kernel):
    app = build(kernel(make_kernel, capabilities={"weather": "almanac"}))
    _, entries = hz(app, "system", "ui", "status")
    [entry] = [e for e in entries if e["id"] == "weather:weather-now"]
    assert entry["item"]["text"] == "Set a location"
    assert entry["item"]["effect"]["path"] == "/?settings=weather"
    bare = build(make_kernel(builtins=True))
    _, entries = hz(bare, "system", "ui", "status")
    assert next(e for e in entries if e["id"] == "weather:weather-now")["item"] is None
