"""Keep the authenticated web workspace available through the user's own service manager."""

from pathlib import Path
from typing import Annotated

from pydantic import Field

from heinzel import Check, Config, Kernel, Module, Settings, Status, __version__
from heinzel import HeinzelError as HeinzelError
from heinzel.kernel import HeadRefresh, Installation, PersistentWeb, WebHeadRefresh, WebOverview
from heinzel.modules.system.lifecycle._runtime import runtime_checks
from heinzel.modules.system.lifecycle._service import WebChange, WebRestart, WebService, WebStatus
from heinzel.modules.system.lifecycle._strays import retire_strays
from heinzel.modules.ui._overview import WebOverviewService

module = Module("ui", "Open the web workspace or manage its optional persistent service.")
module.service(WebService, WebService.for_user)
module.service(WebOverview, WebOverviewService)


def _persistent_web(kernel: Kernel) -> PersistentWeb:
    def open_local() -> str | None:
        settings = Config.load(Path.home()).settings(Settings, "hz")
        try:
            (settings.state_dir / "ui/service.json").lstat()
        except FileNotFoundError:
            return None
        return kernel.resolve(WebService).open_local()

    def attach(port: int, bridge: str | None) -> str:
        return kernel.resolve(WebService).attach(port, bridge=bridge)

    def retire() -> tuple[list[int], list[int]]:
        installation = kernel.resolve(Installation)
        # Only the owned uv tool: a dev checkout's servers are its developer's business.
        return retire_strays(installation.prefix) if installation.is_tool else ([], [])

    return PersistentWeb(open_local=open_local, attach=attach, retire_strays=retire)


module.service(PersistentWeb, _persistent_web)


def _web_head_refresh(kernel: Kernel) -> WebHeadRefresh:
    # Resolve before the updater replaces its own installation. Resolving afterwards can boot a mixture of
    # already-imported old modules and new files on disk, which cannot reliably import either generation.
    try:
        settings = Config.load(Path.home()).settings(Settings, "hz")
    except Exception as exc:  # noqa: BLE001 - a failed head must not prevent the core update
        result = HeadRefresh(head="web", status="failed", detail=f"The web service can't be inspected ({exc}).")
        return WebHeadRefresh(refresh=lambda _target: result)
    record = settings.state_dir / "ui/service.json"
    try:
        record.lstat()
    except FileNotFoundError:

        def absent(_target: str) -> HeadRefresh:
            try:
                record.lstat()
            except FileNotFoundError:
                return HeadRefresh(head="web", status="not-installed")
            except OSError as exc:
                return HeadRefresh(head="web", status="failed", detail=f"The web service record can't be read ({exc}).")
            return HeadRefresh(
                head="web",
                status="pending",
                detail="The web service was installed during the update. Run `hz ui status`.",
            )

        return WebHeadRefresh(refresh=absent)
    except Exception as exc:  # noqa: BLE001 - a failed head must not prevent the core update
        result = HeadRefresh(head="web", status="failed", detail=f"The web service record can't be read ({exc}).")
        return WebHeadRefresh(refresh=lambda _target: result)
    try:
        service = kernel.resolve(WebService)
    except Exception as exc:  # noqa: BLE001 - a failed head must not prevent the core update
        result = HeadRefresh(
            head="web", status="failed", detail=f"The web service can't be inspected ({exc}). Run `hz ui status`."
        )
        return WebHeadRefresh(refresh=lambda _target: result)

    # A launcher service resolved from the previous generation can refresh but cannot attest recovery.
    if (recovery_ready := getattr(service, "recovery_ready", None)) is None:
        return WebHeadRefresh(refresh=service.refresh)
    return WebHeadRefresh(refresh=service.refresh, recovery_ready=recovery_ready)


module.service(WebHeadRefresh, _web_head_refresh)


@module.health
def runtime(kernel: Kernel) -> list[Check]:
    """One owned web backend per user: it serves the installed version, and no app-spawned server is left."""
    try:
        service = kernel.resolve(WebService)
        installation = kernel.resolve(Installation)
    except HeinzelError as exc:
        return [Check(name="runtime: one backend", status=Status.UP, detail=f"no owned web service here ({exc})")]
    prefix = installation.prefix if installation.is_tool else None
    return runtime_checks(service.status, prefix, __version__)


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
