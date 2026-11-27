"""Coarse authenticated API use for this workspace server's current lifetime."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from http import HTTPStatus

from fastapi import FastAPI, Request, Response

from heinzel.kernel import RecentClient, RecentUse, recent_clients


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
        ):
            evidence.observe(datetime.now(UTC))
        return response

    @app.get("/recent-clients", tags=["web"])
    def observations(response: Response) -> list[RecentClient]:
        response.headers["Cache-Control"] = "no-store"
        return recent_clients(evidence, datetime.now(UTC))
