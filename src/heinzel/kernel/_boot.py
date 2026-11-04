"""Boot: load the configuration, discover every module and build the kernel. What heads call on start-up."""

import email.policy
from pathlib import Path

from heinzel.kernel._config import KERNEL_SECTION, Config
from heinzel.kernel._discovery import discover
from heinzel.kernel._kernel import Kernel
from heinzel.kernel._settings import Settings
from heinzel.kernel._source_loading import SourceDiscovery

_installed_sources = SourceDiscovery(installed=True)
# Fixed host bootstrap for MIME parsing, independent of any plugin declaration.
_host_runtime = (email.policy,)


def boot(
    settings: Settings | None = None,
    *,
    source_discovery: "SourceDiscovery | None" = None,
    builtin_only: bool = False,
    cwd: Path | None = None,
) -> Kernel:
    """Discover modules and register a kernel without constructing their services.

    Hosts may explicitly exclude all plugin entry points and select a configuration scope for local maintenance.
    The ordinary default still discovers enabled plugins and loads configuration from the current directory.
    """
    source_discovery = source_discovery or _installed_sources
    config = Config.load(cwd)
    settings = settings or config.settings(Settings, KERNEL_SECTION)
    records, errors = discover(plugins=not builtin_only and settings.plugins_enabled, source_discovery=source_discovery)
    return Kernel(settings, records, errors, config, source_discovery=source_discovery)
