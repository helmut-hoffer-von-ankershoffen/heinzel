"""Typed sharing state without peers, local API credentials or browser sign-in tokens."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from heinzel import ModuleSettings


class SharingState(BaseModel):
    """What is actually ready, and the next action when private sharing cannot be ready yet."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: Literal["unavailable", "login-required", "blocked", "off", "apply-required", "ready", "error"] = Field(
        description="Verified state, rather than the last requested toggle value."
    )
    installed: bool = Field(description="Whether an executable Tailscale CLI was found.")
    logged_in: bool = Field(description="Whether the CLI reports a running, signed-in node.")
    desired_enabled: bool = Field(default=False, description="The persisted requested state, distinct from readiness.")
    apply_settings: dict[str, JsonValue] = Field(
        default_factory=dict, description="Nonsecret settings requiring the shared apply flow."
    )
    mapped: bool = Field(default=False, description="Whether the exact hz HTTPS root mapping is present.")
    origin: str | None = Field(default=None, description="The exact HTTPS origin, without any sign-in credential.")
    target: str | None = Field(default=None, description="The intended loopback backend address.")
    message: str = Field(description="A concise explanation of readiness or the action required.")


class SharingIntent(BaseModel):
    """The owner's requested mapping and whether this plugin added its origin permission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool
    origin: str = Field(max_length=512)
    backend_port: int = Field(ge=1, le=65535)
    https_port: int = Field(ge=1, le=65535)
    added_origin: bool
    phase: Literal["unowned", "enabling", "owned", "disabling", "released"] = "unowned"


class TailscaleSettings(ModuleSettings):
    """Private HTTPS sharing choices and the last explicit requested state."""

    section_title = "Private web sharing"
    ignored_in_project = frozenset({"https_port", "intent"})

    https_port: int = Field(
        default=443,
        ge=1,
        le=65535,
        title="HTTPS sharing port",
        description="The private Tailscale HTTPS port. Existing services on it are preserved.",
    )
    intent: SharingIntent | None = Field(
        default=None,
        title="Sharing request",
        description="The last explicit sharing request and its owned mapping; status reads never execute it.",
        json_schema_extra={"x-hidden": True, "x-private": True},
    )
