"""A device-scoped, read-only snapshot, separate from the web API's bearer acceptance.

PersonalSessionRepository stores each principal under the installation's trusted state root, independent of
project configuration. The random workspace id identifies this server state, not the owner or network origin.

Version 1 returns a UTC `generated_at`, `fresh_for_seconds` (300), `workspace_id`, capped `awake` and
`waiting` counts and at most 40 roster items. `waiting` counts non-retired Heinzels with pending owner asks.
Names are capped at 128 characters and per-Heinzel ask counts at 9999. Clients must reject a different workspace
and treat the snapshot as stale after its freshness interval.

Request ?version=2 to receive the workspace host label, full eligible roster count and total open asks,
bounded to 9999, with explicit nullable scheduling information and appearance data for the returned rows.
The weak ETag identifies the authorized projection for one credential, independent of the observation clock.
"""

import hmac
import json
from datetime import UTC, datetime
from socket import gethostname

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from heinzel.heads.web._pairing import add_pairing
from heinzel.heads.web._widget_fence_routes import add_widget_fence, paired_token
from heinzel.kernel import CallIdentity, CurrentUser, HeinzelError, Kernel, OperationLet
from heinzel.kernel._identity import LOCAL_READ_PERMISSIONS
from heinzel.modules.system.client import Devices

NO_STORE = {"Cache-Control": "no-store"}


def _snapshot_operation(kernel: Kernel) -> OperationLet | None:
    """Select only the built-in, read-only session operation for the widget identity."""
    return next(
        (
            op
            for record, op in kernel.lets(OperationLet)
            if record.qualname == "heinzel" and record.origin.builtin and op.name == "sessions" and op.read_only
        ),
        None,
    )


def _workspace_label() -> str | None:
    try:
        label = gethostname().strip()[:128]
    except OSError:
        return None
    return label if label and label.isprintable() else None


def _conditional_tag(token: str, representation: str) -> str:
    # Each credential sees an independent opaque value, including when a workspace is empty.
    digest = hmac.digest(token.encode(), representation.encode(), "sha256").hex()
    return f'W/"{digest}"'


def _snapshot_response(
    kernel: Kernel, operation: OperationLet, request: Request, workspace: str, token: str
) -> Response:
    active = [item for item in kernel.invoke(operation).sessions if item.state != "retired"]
    roster: list[dict[str, object]] = [
        {
            "id": item.session_id,
            "name": item.name[:128],
            "state": item.state,
            "activity": item.activity,
            "asks": min(item.waiting_count, 9999),
        }
        for item in active[:40]
    ]
    projection: dict[str, object] = {
        "workspace_id": workspace,
        "version": 1,
        "fresh_for_seconds": 300,
        "awake": min(9999, sum(s.state == "awake" for s in active)),
        "waiting": min(9999, sum(s.waiting_count > 0 for s in active)),
        "roster": roster,
    }
    headers = dict(NO_STORE)
    version = request.query_params.get("version", "1")
    if version == "2":
        projection.update(
            version=2,
            workspace_display_name=_workspace_label(),
            roster_count=min(9999, len(active)),
            open_asks=min(9999, sum(item.waiting_count for item in active)),
        )
        for row, item in zip(roster, active[:40], strict=True):
            artwork = item.appearance.model_dump(include={"age", "beard", "hat", "shirt", "build"})
            artwork["accessories"] = list(dict.fromkeys(item.appearance.accessories))
            row.update(
                updated_at=item.updated_at.isoformat(),
                last_approved_at=item.last_approved_at.isoformat() if item.last_approved_at else None,
                next_wake_at=None,
                artwork=artwork,
            )
        tag = _conditional_tag(token, json.dumps(projection, sort_keys=True, separators=(",", ":")))
        headers["ETag"] = tag
        candidates = {value.strip().removeprefix("W/") for value in request.headers.get("if-none-match", "").split(",")}
        if "*" in candidates or tag.removeprefix("W/") in candidates:
            headers["X-Hz-Widget-Observed-At"] = datetime.now(UTC).isoformat()
            return Response(status_code=304, headers=headers)
    elif version != "1":
        return JSONResponse({"error": "Unsupported widget snapshot version."}, status_code=400, headers=NO_STORE)
    return JSONResponse({**projection, "generated_at": datetime.now(UTC).isoformat()}, headers=headers)


def add_widget(app: FastAPI, endpoints: FastAPI, devices: Devices, kernel: Kernel) -> None:
    """Install device-only issuance and a separately authenticated snapshot route."""
    add_pairing(endpoints, devices)
    user = kernel.resolve(CurrentUser)
    add_widget_fence(endpoints, devices, user)

    @endpoints.post("/_web/widget-credential", include_in_schema=False)
    def issue(request: Request) -> JSONResponse:
        if getattr(request.state, "auth", None) != "device":
            return JSONResponse({"error": "A paired device is required."}, status_code=403, headers=NO_STORE)
        issued = devices.widget_issue(request.state.device, user.principal().id, paired_token(request))
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
        revoked = devices.widget_revoke(request.state.device, paired_token(request))
        return JSONResponse({"revoked": revoked}, status_code=200 if revoked else 409, headers=NO_STORE)

    @app.get("/api/_web/widget-snapshot", include_in_schema=False)
    def snapshot(request: Request) -> Response:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        workspace = request.headers.get("x-hz-workspace-id", "")
        try:
            owner = user.owner()
            device = devices.widget_check(token, owner.id, workspace) if scheme == "Bearer" else None
            if not device:
                return JSONResponse({"error": "Invalid widget credential."}, status_code=401, headers=NO_STORE)
            identity = CallIdentity(owner, "web-device", device, "hz-widget", LOCAL_READ_PERMISSIONS)
            with user.using(identity):
                operation = _snapshot_operation(kernel)
                if operation is None:
                    return JSONResponse({"error": "Widget snapshot unavailable."}, status_code=503, headers=NO_STORE)
                return _snapshot_response(kernel, operation, request, workspace, token)
        except (HeinzelError, OSError, ValueError):
            return JSONResponse({"error": "Widget snapshot unavailable."}, status_code=503, headers=NO_STORE)
