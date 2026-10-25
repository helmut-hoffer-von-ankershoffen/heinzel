"""The module declaration, apart from `__init__.py` so the adapter and service can import it."""

from heinzel import Module

module = Module("openmeteo", "Weather from Open-Meteo, with no account and no key: the weather capability.")
