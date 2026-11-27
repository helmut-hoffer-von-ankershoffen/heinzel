"""Coarse authenticated API use for this workspace server's current lifetime."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from http import HTTPStatus

from fastapi import FastAPI, Request, Response

from heinzel.kernel.recent_clients import (
    ClientObservation,
    RecentClient,
    RecentUse,
    parse_client_labels,
    recent_clients,
)


def add_recent_clients(app: FastAPI) -> None:
    """Attach behind the same bearer, host and origin checks as the workspace API."""
    evidence = RecentUse()

    @app.middleware("http")
    async def observe(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        # Authentication is established by the inner API middleware. Polling this
        # readout must never refresh the evidence it displays.
        if (
            getattr(request.state, "auth", None) in {"session", "device"}
            and HTTPStatus.OK <= response.status_code < HTTPStatus.MULTIPLE_CHOICES
            and request.url.path.rstrip("/") != "/api/recent-clients"
            and request.url.path.rstrip("/") != "/recent-clients"
            and request.url.path.rstrip("/") not in {"/client-observations", "/api/client-observations"}
        ):
            now = datetime.now(UTC)
            evidence.observe(now)
            if labels := parse_client_labels(request.headers):
                device_id = getattr(request.state, "device", None) if request.state.auth == "device" else None
                evidence.observe_labels(now, labels, device_id)
        return response

    @app.get("/recent-clients", tags=["web"])
    def observations(response: Response) -> list[RecentClient]:
        response.headers["Cache-Control"] = "no-store"
        return recent_clients(evidence, datetime.now(UTC))

    @app.get("/client-observations", tags=["web"])
    def labelled_observations(response: Response) -> list[ClientObservation]:
        response.headers["Cache-Control"] = "no-store"
        return evidence.observations(datetime.now(UTC))
