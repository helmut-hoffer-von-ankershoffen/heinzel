"""A device-scoped, read-only snapshot, separate from the web API's bearer acceptance.

PersonalSessionRepository stores each principal under the installation's trusted state root, independent of
project configuration. The random workspace id identifies this server state, not the owner or network origin.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from heinzel.heads.web._devices import Devices
from heinzel.heads.web._pairing import add_pairing
from heinzel.kernel import CallIdentity, CurrentUser, HeinzelError, Kernel
from heinzel.kernel._identity import LOCAL_READ_PERMISSIONS
from heinzel.modules.heinzel._session_crud import SessionCrud

NO_STORE = {"Cache-Control": "no-store"}


def add_widget(app: FastAPI, endpoints: FastAPI, devices: Devices, kernel: Kernel) -> None:
    """Install device-only issuance and a separately authenticated snapshot route."""
    add_pairing(endpoints, devices)
    user = kernel.resolve(CurrentUser)

    @endpoints.post("/_web/widget-credential", include_in_schema=False)
    def issue(request: Request) -> JSONResponse:
        if getattr(request.state, "auth", None) != "device":
            return JSONResponse({"error": "A paired device is required."}, status_code=403, headers=NO_STORE)
        issued = devices.widget_issue(request.state.device, user.principal().id)
        if issued is None:
            return JSONResponse({"error": "The device is no longer paired."}, status_code=403, headers=NO_STORE)
        token, workspace, expires = issued
        return JSONResponse(
            {"token": token, "workspace_id": workspace, "expires_at": expires.isoformat()}, headers=NO_STORE
        )

    @endpoints.delete("/_web/widget-credential", include_in_schema=False)
    def revoke(request: Request) -> JSONResponse:
        if getattr(request.state, "auth", None) != "device":
            return JSONResponse({"error": "A paired device is required."}, status_code=403, headers=NO_STORE)
        devices.widget_revoke(request.state.device)
        return JSONResponse({"revoked": True}, headers=NO_STORE)

    @app.get("/api/_web/widget-snapshot", include_in_schema=False)
    def snapshot(request: Request) -> JSONResponse:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        workspace = request.headers.get("x-hz-workspace-id", "")
        try:
            owner = user.owner()
            device = devices.widget_check(token, owner.id, workspace) if scheme == "Bearer" else None
            if not device:
                return JSONResponse({"error": "Invalid widget credential."}, status_code=401, headers=NO_STORE)
            identity = CallIdentity(owner, "web-device", device, "hz-widget", LOCAL_READ_PERMISSIONS)
            with user.using(identity):
                sessions = kernel.resolve(SessionCrud).listing().sessions
            roster = [
                {
                    "id": item.session_id,
                    "name": item.name[:128],
                    "state": item.state,
                    "activity": item.activity,
                    "asks": min(item.waiting_count, 9999),
                }
                for item in sessions
                if item.state != "retired"
            ][:40]
            return JSONResponse(
                {
                    "workspace_id": workspace,
                    "awake": min(9999, sum(s.state == "awake" for s in sessions)),
                    "waiting": min(9999, sum(s.state == "waiting" for s in sessions)),
                    "roster": roster,
                },
                headers=NO_STORE,
            )
        except (HeinzelError, OSError, ValueError):
            return JSONResponse({"error": "Widget snapshot unavailable."}, status_code=503, headers=NO_STORE)
