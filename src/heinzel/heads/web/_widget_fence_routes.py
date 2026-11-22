"""Authenticated bounded endpoints for recoverable widget credential rotation."""

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from heinzel.kernel import CurrentUser, HeinzelError
from heinzel.modules.system.client import Devices, FenceConflictError, FenceRequest

NO_STORE = {"Cache-Control": "no-store"}
MAX_BODY = 4096
FENCE_ID_PATTERN = r"^[0-9a-f]{32}$"


class RequestTooLargeError(ValueError):
    """A request exceeded the bounded metadata size."""


class FencedWidgetRequest(BaseModel):
    """Strict metadata; the raw widget bearer is never sent here."""

    model_config = ConfigDict(extra="forbid", strict=True)
    epoch: str = Field(pattern=FENCE_ID_PATTERN)
    revision: str = Field(pattern=FENCE_ID_PATTERN)
    attempt: str = Field(pattern=FENCE_ID_PATTERN)
    operation: Literal["issue", "revoke"]
    commitment: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def paired_token(request: Request) -> str:
    """Extract the bearer without logging or persisting it."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token if scheme == "Bearer" else ""


async def _read_body(request: Request) -> bytearray:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            raise RequestTooLargeError
        body.extend(chunk)
    return body


def _negotiate(request: Request, devices: Devices, user: CurrentUser) -> JSONResponse:
    if getattr(request.state, "auth", None) != "device":
        return JSONResponse({"error": "A paired device is required."}, status_code=403, headers=NO_STORE)
    try:
        fence = devices.widget_fence_negotiate(request.state.device, paired_token(request), user.principal().id)
        if fence is None:
            return JSONResponse({"error": "The device is no longer paired."}, status_code=403, headers=NO_STORE)
        return JSONResponse(
            {
                "epoch": fence.epoch,
                "revision": fence.revision,
                "workspace_id": fence.workspace,
                "last_attempt": fence.receipt.attempt if fence.receipt else None,
            },
            headers=NO_STORE,
        )
    except (HeinzelError, OSError, ValueError):
        return JSONResponse({"error": "Widget fence unavailable."}, status_code=503, headers=NO_STORE)


def _failure(error: Exception) -> JSONResponse:
    if isinstance(error, RequestTooLargeError):
        message, status = "Widget request too large.", 413
    elif isinstance(error, ValidationError):
        message, status = "Invalid widget request.", 400
    elif isinstance(error, FenceConflictError):
        message, status = "Widget operation conflict.", 409
    else:
        message, status = "Widget fence unavailable.", 503
    return JSONResponse({"error": message}, status_code=status, headers=NO_STORE)


async def _mutate(request: Request, devices: Devices, user: CurrentUser) -> JSONResponse:
    if getattr(request.state, "auth", None) != "device":
        return JSONResponse({"error": "A paired device is required."}, status_code=403, headers=NO_STORE)
    try:
        payload = FencedWidgetRequest.model_validate_json(await _read_body(request))
        operation = FenceRequest(**payload.model_dump())
        result = devices.widget_fence_mutate(
            request.state.device, paired_token(request), user.principal().id, operation
        )
        if result is None:
            return JSONResponse({"error": "The device is no longer paired."}, status_code=403, headers=NO_STORE)
        fence, receipt = result
        return JSONResponse(
            {
                "epoch": fence.epoch,
                "workspace_id": fence.workspace,
                "attempt": receipt.attempt,
                "operation": receipt.operation,
                "revision": receipt.revision,
                "expires_at": receipt.expires.isoformat() if receipt.expires else None,
            },
            headers=NO_STORE,
        )
    except (FenceConflictError, HeinzelError, OSError, ValueError) as error:
        return _failure(error)


def add_widget_fence(endpoints: FastAPI, devices: Devices, user: CurrentUser) -> None:
    """Install pairing-bound fencing negotiation and mutations."""

    @endpoints.post("/_web/widget-fence", include_in_schema=False)
    def negotiate(request: Request) -> JSONResponse:
        return _negotiate(request, devices, user)

    @endpoints.post("/_web/widget-fenced-credential", include_in_schema=False)
    async def fenced_credential(request: Request) -> JSONResponse:
        return await _mutate(request, devices, user)
