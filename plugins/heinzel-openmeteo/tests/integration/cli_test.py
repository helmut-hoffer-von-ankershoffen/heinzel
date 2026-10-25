"""`hz weather` and the plugin's own commands through the CLI head, asking the recorded client. Offline."""

import json
import tomllib

import pytest
from typer.testing import CliRunner

from heinzel import Config, HeinzelError
from heinzel.heads.cli import build
from heinzel.kernel import user_config_dir

pytestmark = pytest.mark.integration


def hz(kernel, *args: str, json_output: bool = True):
    result = CliRunner().invoke(build(kernel), [*args, *(["--json"] if json_output else [])])
    return result.exit_code, (
        json.loads(result.output) if json_output and result.output.startswith(("{", "[")) else result.output
    )


def settings(**weather: object) -> Config:
    return Config(files=(), data={"weather": weather})


def test_the_weather_works_with_no_key_and_no_binding_once_the_plugin_is_installed(boot):
    code, outlook = hz(boot(settings(locations=["Lisbon, PT"], units="metric")), "weather")
    assert code == 0, outlook
    assert (outlook["location"], outlook["adapter"], outlook["units"]) == (
        "Lisbon, Lisbon District, Portugal",
        "openmeteo",
        "metric",
    )
    assert (outlook["summary"], outlook["temperature"]) == ("mainly clear", 12.4)
    assert [d["summary"] for d in outlook["days"]] == ["partly cloudy", "fog", "rain"]  # three days by default


def test_a_place_on_the_command_line_beats_the_settings_and_imperial_units_convert(boot, client):
    code, outlook = hz(
        boot(settings(locations=["Berlin, DE"], units="imperial")), "weather", "--location", "Lisbon", "--days", "5"
    )
    assert code == 0, outlook
    assert client.requests[0][2]["name"] == "Lisbon"
    assert (outlook["temperature"], outlook["units"], len(outlook["days"])) == (54.3, "imperial", 5)
    assert (outlook["days"][2]["high"], outlook["days"][2]["precipitation"]) == (74.7, 0.13)


def test_the_text_view_has_the_place_and_a_table_of_days(boot):
    code, text = hz(boot(settings(locations=["Lisbon, PT"], units="metric")), "weather", json_output=False)
    assert code == 0, text
    assert "Lisbon, Lisbon District, Portugal: 12°C, mainly clear" in text.replace("\n", " ")
    assert "Next days" in text
    assert "rain" in text


def test_without_a_location_it_says_how_to_set_one(boot):
    code, output = hz(boot(), "weather", json_output=False)
    assert code != 0
    assert "weather.locations" in output


def test_the_adapter_is_bound_automatically_and_reaches_only_open_meteo(boot):
    code, listing = hz(boot(), "system", "capability", "list")
    assert code == 0, listing
    text = json.dumps(listing)
    assert "openmeteo" in text
    assert "api.open-meteo.com" in text
    assert "geocoding-api.open-meteo.com" in text


def test_doctor_is_not_degraded_by_the_weather_needing_an_adapter(boot):
    code, report = hz(boot(settings(locations=["Lisbon, PT"], units="metric")), "system", "doctor")
    assert code == 0, report
    [check] = [c for c in report["checks"]["weather"] if c["name"] == "locations"]
    assert (check["status"], check["detail"]) == ("up", "Lisbon, PT in metric units")


def test_the_live_test_asks_for_fixed_coordinates_and_not_for_your_place(boot, client):
    code, result = hz(boot(settings(locations=["Lisbon, PT"])), "system", "connector", "test", "openmeteo")
    assert code == 0, result
    assert result["passed"] is True
    assert [(host, params["latitude"]) for host, _, params in client.requests] == [("api.open-meteo.com", "52.5200")]


def test_info_names_the_hosts(boot):
    code, about = hz(boot(), "openmeteo", "info")
    assert code == 0, about
    assert (about["plugin"], about["hosts"]) == (
        "heinzel-openmeteo",
        ["api.open-meteo.com", "geocoding-api.open-meteo.com"],
    )


def test_setup_saves_the_places_for_the_weather_module(hz, hz_prompter):
    hz_prompter.answers += ["Lisbon, PT; 52.52,13.41 ;"]
    result = hz("setup", "openmeteo", json=False)
    assert result.exit_code == 0, result.output
    saved = tomllib.loads((user_config_dir() / "config.toml").read_text(encoding="utf-8"))
    assert saved == {"weather": {"locations": ["Lisbon, PT", "52.52,13.41"]}}


def test_setup_with_no_place_saves_nothing(hz, hz_prompter):
    hz_prompter.answers += [" ; "]
    result = hz("setup", "openmeteo", json=False)
    assert result.exit_code == 0, result.output
    assert not (user_config_dir() / "config.toml").exists()
    assert "nothing was saved" in "\n".join(hz_prompter.transcript)


def test_a_failing_live_test_names_the_host_and_not_a_place(boot, client, monkeypatch):
    def refuse(host, path, params):
        msg = f"Open-Meteo ({host}) can't be reached (URLError)."
        raise HeinzelError(msg)

    kernel = boot(settings(locations=["Lisbon, PT"]))
    monkeypatch.setattr(client, "get", refuse)
    code, result = hz(kernel, "system", "connector", "test", "openmeteo")
    assert code == 0, result
    assert result["passed"] is False
    assert "api.open-meteo.com" in json.dumps(result["checks"])
    assert "Lisbon" not in json.dumps(result["checks"])
