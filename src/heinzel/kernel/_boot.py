"""Boot: load the configuration, discover every module and build the kernel. What heads call on start-up."""

from heinzel.kernel._config import KERNEL_SECTION, Config
from heinzel.kernel._discovery import discover
from heinzel.kernel._kernel import Kernel
from heinzel.kernel._settings import Settings


def boot(settings: Settings | None = None) -> Kernel:
    """Discover every module and return a ready kernel."""
    config = Config.load()
    settings = settings or config.settings(Settings, KERNEL_SECTION)
    records, errors = discover(plugins=settings.plugins_enabled)
    return Kernel(settings, records, errors, config)
