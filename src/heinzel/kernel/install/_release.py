"""Compatibility path for the installation release services."""

from heinzel.services.install._release import (
    CACHE_FILENAME,
    EdgeRelease,
    PluginNews,
    PluginRelease,
    PluginReleases,
    ReleaseCache,
    ReleaseFacts,
    ReleaseSource,
)

__all__ = [
    "CACHE_FILENAME",
    "EdgeRelease",
    "PluginNews",
    "PluginRelease",
    "PluginReleases",
    "ReleaseCache",
    "ReleaseFacts",
    "ReleaseSource",
]
