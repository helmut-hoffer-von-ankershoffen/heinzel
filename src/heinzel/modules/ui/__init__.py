"""Keep the authenticated web workspace available through the user's own service manager."""

from pathlib import Path
from typing import Annotated

from pydantic import Field

from heinzel import Config, HeinzelError, Kernel, Module, Settings
from heinzel.kernel import PersistentWeb, feature_enabled
from heinzel.modules.ui._service import WebChange, WebRestart, WebService, WebStatus

module = Module("ui", "Open the web workspace or manage its optional persistent service.")
module.service(WebService, WebService.for_user)


def _persistent_web(kernel: Kernel) -> PersistentWeb:
    def open_local() -> str | None:
        settings = Config.load(Path.home()).settings(Settings, "hz")
        try:
            (settings.state_dir / "ui/service.json").lstat()
        except FileNotFoundError:
            return None
        if not feature_enabled(settings, "persistent_web"):
            msg = "Persistent web preview is disabled; inspect `hz ui status` or remove it with `hz ui uninstall`."
            raise HeinzelError(msg)
        return kernel.resolve(WebService).open_local()

    return PersistentWeb(open_local=open_local)


module.service(PersistentWeb, _persistent_web)


def _require_preview(settings: Settings) -> None:
    if not feature_enabled(settings, "persistent_web"):
        msg = "Persistent web availability is a disabled preview. Enable persistent_web in hz.features to validate it."
        raise HeinzelError(msg)


@module.operation(read_only=False, heads={"cli"}, audience="everyday")
def install(
    service: WebService,
    settings: Settings,
    port: Annotated[int, Field(description="Fixed loopback port for the web workspace.", ge=1, le=65535)] = 8737,
    dry_run: Annotated[bool, Field(description="Show the exact service definition without changing anything.")] = False,
) -> WebChange:
    """Install preview web availability after login, separately from scheduled background work.

    Raises:
        HeinzelError: If the preview flag is disabled.
    """
    _require_preview(settings)
    return service.install(port, dry_run=dry_run)


@module.operation(heads={"cli"}, audience="everyday")
def status(service: WebService) -> WebStatus:
    """Show exact OS ownership and the live web server's readiness and version."""
    return service.status()


@module.operation(read_only=False, heads={"cli"}, audience="everyday")
def restart(service: WebService, settings: Settings) -> WebRestart:
    """Restart the preview web service with its existing port and authenticated session."""
    _require_preview(settings)
    return service.restart()


@module.operation(read_only=False, heads={"cli"}, audience="everyday")
def uninstall(service: WebService) -> WebChange:
    """Remove only the owned web login service, preserving your data and paired devices."""
    return service.uninstall()
