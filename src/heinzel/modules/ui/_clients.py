"""Read recent request evidence through the owned server's authenticated API."""

from contextlib import closing
from http import HTTPStatus
from http.client import HTTPConnection, HTTPException
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, Field, TypeAdapter
from rich.text import Text

from heinzel import HeinzelError
from heinzel.modules.system.lifecycle._service import WebService, WebStatus


class ClientLabels(BaseModel):
    """Labels are claims even when a device credential was validated."""

    flavor: Literal["native", "electron", "unknown"] = "unknown"
    machine: str = Field(default="unknown", max_length=64)
    app_version: str = Field(default="unknown", max_length=64)
    backend_version: str = Field(default="unknown", max_length=64)


class ClientObservation(BaseModel):
    """Successful request evidence, never a connection inventory."""

    labels: ClientLabels = Field(default_factory=ClientLabels)
    label_provenance: Literal["self-reported"] = "self-reported"
    identity_provenance: Literal["device-credential", "session-observation", "unknown"] = "unknown"
    device_id: str | None = None
    platform: Literal["unknown"] = "unknown"
    last_seen: AwareDatetime | None = None
    age_seconds: int | None = Field(default=None, ge=0, strict=True)
    presence: Literal["fresh", "stale", "unknown"] = "unknown"


class UiStatus(WebStatus):
    """Existing service status plus an optional, independently fallible API read."""

    client_observations: list[ClientObservation] = Field(default_factory=list, max_length=50)
    client_observations_status: Literal["available", "unsupported", "unavailable"] = "unavailable"


def read_status(service: WebService) -> UiStatus:
    """Keep service diagnostics usable on old, stopped and inaccessible servers."""
    result = UiStatus(**service.status().model_dump())
    if not result.ready or result.port is None:
        return result
    try:
        # The owned control handoff retrieves the existing privileged process-session bearer.
        # It does not mint a credential or narrow its authority; no browser is opened.
        link = service.open_local()
        if link is None:
            return result
        parsed = urlsplit(link)
        if parsed.scheme != "http" or parsed.netloc != f"127.0.0.1:{result.port}":
            return result
        if not parsed.fragment.startswith("token="):
            return result
        with closing(HTTPConnection("127.0.0.1", result.port, timeout=2)) as client:
            client.request(
                "GET",
                "/api/client-observations",
                headers={"Authorization": f"Bearer {parsed.fragment[6:]}", "Cache-Control": "no-store"},
            )
            response = client.getresponse()
            if response.status in {404, 405}:
                result.client_observations_status = "unsupported"
            elif HTTPStatus.OK <= response.status < HTTPStatus.MULTIPLE_CHOICES:
                result.client_observations = TypeAdapter(
                    Annotated[list[ClientObservation], Field(max_length=50)]
                ).validate_json(response.read())
                result.client_observations_status = "available"
    except (HeinzelError, OSError, ValueError, HTTPException):
        # Never expose URLs, credentials or response bodies in diagnostics.
        pass
    return result


def render_status(result: UiStatus) -> Text:
    """Plain text preserves the service fields and labels the limits of request evidence."""
    fields = result.model_dump(exclude={"client_observations", "client_observations_status"})
    lines = [f"{key}: {value}" for key, value in fields.items()]
    lines.append(f"Desktop observations: {result.client_observations_status}")
    lines.append("Self-reported labels; successful API requests, not open/live connections.")
    lines.append("Fresh: within two hours; stale: within 30 days. History clears on restart.")
    lines.append(" Platform, transport and capabilities are unknown.")
    if result.client_observations_status == "available" and not result.client_observations:
        lines.append("No retained desktop observations; older or unlabelled clients may be absent.")
    for row in result.client_observations:
        labels = row.labels
        lines.append(
            f"{labels.flavor} | {labels.machine} | app {labels.app_version} | {row.presence} | "
            f"last observed use: {row.last_seen.isoformat() if row.last_seen else 'unknown'} | "
            f"identity: {row.identity_provenance}"
        )
    lines.append("Session observations group labels only; collisions and renames are possible.")
    return Text("\n".join(lines))
