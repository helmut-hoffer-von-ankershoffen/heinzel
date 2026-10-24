"""Pairing endpoints of the web head (ADR 0046): the app asks for a device token with its session token, or forgets it.

Under `/api/_web/`, a path no module's operation can take (module names have no underscore in front), outside the
OpenAPI schema, behind the API's middleware like every call.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from heinzel.heads.web._devices import Devices

FORBIDDEN = 403


class Pairing(BaseModel):
    """What the app says about itself when it asks for a device token."""

    name: str = Field(default="", max_length=200, description="What it calls itself, e.g. `Chrome on macOS`.")


def add_pairing(app: FastAPI, devices: Devices) -> None:
    """Add `POST /_web/device` (pair, session token only) and `DELETE /_web/device` (forget this device)."""

    @app.post("/_web/device", include_in_schema=False)
    async def pair(request: Request, body: Pairing) -> JSONResponse:
        # Only the token `hz ui` opened this tab with may pair: a device token never makes more of itself.
        if getattr(request.state, "auth", None) != "session":
            problem = "Refused: pair with the link `hz ui` opened, not with a device token."
            return JSONResponse(status_code=FORBIDDEN, content={"error": problem})
        device, token = devices.pair(body.name)
        return JSONResponse({"id": device.id, "name": device.name, "token": token})

    @app.delete("/_web/device", include_in_schema=False)
    async def forget(request: Request) -> JSONResponse:
        # The device the request authenticated as, never one it names: a device forgets only itself.
        device = getattr(request.state, "device", None)
        gone = devices.revoke(device) if isinstance(device, str) else []
        return JSONResponse({"revoked": [d.id for d in gone]})
