"""Keep the authenticated web workspace available through the user's own service manager."""

from pathlib import Path
from typing import Annotated

from pydantic import Field

from heinzel import Config, HeinzelError, Kernel, Module, Settings
from heinzel.kernel import HeadRefresh, PersistentWeb, WebHeadRefresh
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
        return kernel.resolve(WebService).open_local()

    return PersistentWeb(open_local=open_local)


module.service(PersistentWeb, _persistent_web)


def _web_head_refresh(kernel: Kernel) -> WebHeadRefresh:
    def refresh(target: str) -> HeadRefresh:
        settings = Config.load(Path.home()).settings(Settings, "hz")
        try:
            (settings.state_dir / "ui/service.json").lstat()
        except FileNotFoundError:
            return HeadRefresh(head="web", status="not-installed")
        except OSError as exc:
            return HeadRefresh(head="web", status="failed", detail=f"The web service record can't be read ({exc}).")
        try:
            service = kernel.resolve(WebService)
        except (OSError, HeinzelError) as exc:
            return HeadRefresh(
                head="web", status="failed", detail=f"The web service can't be inspected ({exc}). Run `hz ui status`."
            )
        return service.refresh(target)

    return WebHeadRefresh(refresh=refresh)


module.service(WebHeadRefresh, _web_head_refresh)


@module.operation(read_only=False, heads={"cli"}, audience="everyday")
def install(
    service: WebService,
    port: Annotated[int, Field(description="Fixed loopback port for the web workspace.", ge=1, le=65535)] = 8737,
    dry_run: Annotated[bool, Field(description="Show the exact service definition without changing anything.")] = False,
) -> WebChange:
    """Install web availability after login, separately from scheduled Heinzels."""
    return service.install(port, dry_run=dry_run)


@module.operation(heads={"cli"}, audience="everyday")
def status(service: WebService) -> WebStatus:
    """Show exact OS ownership and the live web server's readiness and version."""
    return service.status()


@module.operation(read_only=False, heads={"cli"}, audience="everyday")
def restart(service: WebService) -> WebRestart:
    """Restart the owned web service with its existing port and authenticated session."""
    return service.restart()


@module.operation(read_only=False, heads={"cli"}, audience="everyday")
def uninstall(service: WebService) -> WebChange:
    """Remove only the owned web login service, preserving your data and paired devices."""
    return service.uninstall()
