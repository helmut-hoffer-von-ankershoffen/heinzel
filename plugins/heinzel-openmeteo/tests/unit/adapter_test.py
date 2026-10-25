"""The Open-Meteo adapter against recorded answers: places, units, gaps. Offline."""

from datetime import date

import pytest
from heinzel_openmeteo import FORECAST_HOST, GEOCODING_HOST, OpenMeteo

from heinzel import HeinzelError, WeatherReport
from heinzel.testing import assert_adapter

pytestmark = pytest.mark.unit


def test_it_implements_the_weather_capability_in_read_mode():
    assert_adapter(OpenMeteo, "weather", {"read"})


def test_a_name_is_geocoded_and_the_first_match_is_asked_for_its_forecast(client):
    report = OpenMeteo(client).report("Lisbon")
    assert report.place.name == "Lisbon, Lisbon District, Portugal"
    assert [(host, params.get("name")) for host, _, params in client.requests] == [
        (GEOCODING_HOST, "Lisbon"),
        (FORECAST_HOST, None),
    ]
    forecast = client.requests[1][2]
    assert (forecast["latitude"], forecast["longitude"]) == ("38.7251", "-9.1498")
    assert forecast["forecast_days"] == "7"
    assert {forecast["wind_speed_unit"], forecast["temperature_unit"]} == {"kmh", "celsius"}


def test_what_follows_the_comma_picks_the_match_by_country_code_country_or_region(client):
    for qualifier, where in (("US", "Ohio"), ("united states", "Ohio"), ("North Dakota", "North Dakota")):
        assert OpenMeteo(client).report(f"Lisbon, {qualifier}").place.name.split(", ")[1] == where
    assert OpenMeteo(client).report("Lisbon, pt").place.name.endswith("Portugal")


def test_coordinates_are_used_as_they_are_without_geocoding(client):
    report = OpenMeteo(client).report(" 38.72 , -9.14 ")
    assert [host for host, _, _ in client.requests] == [FORECAST_HOST]
    assert (report.place.name, report.place.latitude, report.place.longitude) == ("38.72, -9.14", 38.72, -9.14)


@pytest.mark.parametrize("place", ["", "   ", ",PT", "Atlantis", "Lisbon, XX"])
def test_an_unknown_or_empty_place_says_what_to_give(client, place):
    client.geocoding = {"generationtime_ms": 0.1} if place == "Atlantis" else client.geocoding
    with pytest.raises(HeinzelError, match=r"Name a place|knows no place"):
        OpenMeteo(client).report(place)
    assert all(host == GEOCODING_HOST for host, _, _ in client.requests)


def test_out_of_range_coordinates_are_a_name_nobody_knows(client):
    client.geocoding = {}
    with pytest.raises(HeinzelError, match="knows no place"):
        OpenMeteo(client).report("95,200")


def test_the_report_is_metric_in_the_places_time_zone_and_in_words(client):
    report = OpenMeteo(client).report("Lisbon")
    assert isinstance(report, WeatherReport)
    now = report.current
    assert (now.summary, now.temperature_c, now.feels_like_c, now.humidity_percent) == ("mainly clear", 12.4, 11.5, 82)
    assert (now.wind_kmh, now.precipitation_mm) == (4.6, 0.0)
    assert (now.observed_at.isoformat(), str(now.observed_at.tzinfo)) == ("2026-10-25T00:15:00+01:00", "Europe/Lisbon")
    assert len(report.days) == 5
    rainy = report.days[2]
    assert (rainy.day, rainy.summary, rainy.rain_chance, rainy.precipitation_mm) == (
        date(2026, 10, 27),
        "rain",
        0.8,
        3.2,
    )
    assert (report.days[1].summary, report.days[1].high_c, report.days[1].low_c) == ("fog", 22.6, 11.9)


def test_gaps_in_the_forecast_are_kept_honest(client):
    daily = client.forecast_answer["daily"]
    daily["precipitation_probability_max"][0] = None
    daily["temperature_2m_max"][1] = None  # no high: the day cannot be shown
    daily["weather_code"][3] = 1234
    del daily["precipitation_sum"][3:]
    days = OpenMeteo(client).report("Lisbon").days
    assert [d.day.day for d in days] == [25, 27, 28, 29]
    assert days[0].rain_chance is None
    assert [d.summary for d in days][2] == "unknown"
    assert days[2].precipitation_mm == 0.0


def test_text_from_the_geocoder_is_cleaned(client):
    client.geocoding["results"][0]["name"] = "Lis\N{RIGHT-TO-LEFT OVERRIDE}bon\x07"
    assert OpenMeteo(client).report("Lisbon").place.name.startswith("Lisbon,")


def test_an_unknown_time_zone_is_reported_without_the_url(client):
    client.forecast_answer["timezone"] = "Mars/Olympus"
    with pytest.raises(HeinzelError, match="time zone"):
        OpenMeteo(client).report("Lisbon")


def test_a_date_that_is_not_one_is_an_error_not_a_traceback(client):
    client.forecast_answer["current"]["time"] = "tomorrow-ish"
    with pytest.raises(HeinzelError, match="a date this plugin doesn't know"):
        OpenMeteo(client).report("Lisbon")


def test_odd_numbers_are_clamped(client):
    daily = client.forecast_answer["daily"]
    daily["precipitation_probability_max"][0] = 150
    daily["precipitation_probability_max"][1] = -5
    daily["precipitation_sum"][0] = -1.0
    days = OpenMeteo(client).report("Lisbon").days
    assert (days[0].rain_chance, days[1].rain_chance, days[0].precipitation_mm) == (1.0, 0.0, 0.0)
