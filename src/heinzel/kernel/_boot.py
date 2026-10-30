"""Boot: load the configuration, discover every module and build the kernel. What heads call on start-up."""

import email.policy

from heinzel.kernel._config import KERNEL_SECTION, Config
from heinzel.kernel._discovery import discover
from heinzel.kernel._kernel import Kernel
from heinzel.kernel._settings import Settings
from heinzel.kernel._source_loading import SourceDiscovery

_installed_sources = SourceDiscovery(installed=True)
# Fixed host bootstrap for MIME parsing, independent of any plugin declaration.
_host_runtime = (email.policy,)


def boot(settings: Settings | None = None, *, source_discovery: "SourceDiscovery | None" = None) -> Kernel:
    """Discover every module and return a ready kernel."""
    source_discovery = source_discovery or _installed_sources
    config = Config.load()
    settings = settings or config.settings(Settings, KERNEL_SECTION)
    records, errors = discover(plugins=settings.plugins_enabled, source_discovery=source_discovery)
    return Kernel(settings, records, errors, config, source_discovery=source_discovery)
