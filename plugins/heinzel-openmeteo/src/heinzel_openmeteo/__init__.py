"""Weather from Open-Meteo, with no account and no key: the weather capability.

A Heinzelmännchen plugin, discovered through the `heinzel.modules` entry point in pyproject.toml. It provides the
adapter `openmeteo` for the capability `weather` in read mode (ADR 0028), and is bound automatically while it is the
only weather adapter installed (ADR 0055). The core's `hz weather` asks for the capability, never for this plugin.
"""

from heinzel import Check, ConfigEditor, Prompter
from heinzel_openmeteo._adapter import DAYS, OpenMeteo
from heinzel_openmeteo._client import FORECAST_HOST, GEOCODING_HOST, HOSTS, Client
from heinzel_openmeteo._models import OpenMeteoAbout
from heinzel_openmeteo._module import module
from heinzel_openmeteo._service import OpenMeteoService

module.service(Client)
module.service(OpenMeteoService)
# The places you ask about are sent to these hosts, and nothing else.
module.provides("weather", modes={"read"}, hosts=HOSTS)(OpenMeteo)


@module.setup(required=False)
def setup(prompter: Prompter, editor: ConfigEditor) -> None:
    """Pick the places you want the weather for; `hz weather` shows the first one."""
    answer = prompter.text("Places, yours first, separated by ';' (a city and country, or lat,lon)", default="")
    places = [place.strip() for place in answer.split(";") if place.strip()]
    if not places:
        prompter.say("No place given; nothing was saved. Name one any time with `hz weather --location 'Lisbon, PT'`.")
        return
    path = editor.write("weather.locations", places)
    prompter.say(
        f"Saved to {path}. Units follow your locale; change them with `hz system config set weather.units imperial`."
    )


@module.operation
def info(service: OpenMeteoService) -> OpenMeteoAbout:
    """Show this plugin's version, the Heinzelmännchen it runs on and where its weather comes from."""
    return service.about()


@module.info
def system_info(service: OpenMeteoService) -> OpenMeteoAbout:
    """Contribute this plugin's details to `hz system info`."""
    return service.about()


@module.health(live=True)
def live(service: OpenMeteoService) -> list[Check]:
    """Ask Open-Meteo for the weather at fixed coordinates; run only by `hz system connector test openmeteo`."""
    return service.live()


__all__ = [
    "DAYS",
    "FORECAST_HOST",
    "GEOCODING_HOST",
    "HOSTS",
    "Client",
    "OpenMeteo",
    "OpenMeteoAbout",
    "OpenMeteoService",
    "module",
]
