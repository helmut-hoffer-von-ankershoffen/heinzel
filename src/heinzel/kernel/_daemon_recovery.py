"""Narrow explicit owner recovery of legacy daemon integration (ADR 0132)."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


@dataclass(frozen=True, slots=True)
class DaemonRecoverySelection:
    """The dedicated head's validated standard receipt, bound again by the daemon authority."""

    standard_receipt: Path
    content: bytes


class DaemonRecoveryPlan(BaseModel):
    """An immutable read-only observation; the digest is explicit removal consent."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    historical_profile: Literal["unknown"] = "unknown"
    facts: dict[str, JsonValue]


class DaemonRecoveryResult(BaseModel):
    """A historical terminal operation, not a claim about the current service."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["retired", "restored"]
    message: str


@dataclass(frozen=True, slots=True)
class DaemonLegacyRecovery:
    """Trusted candidate console only; ordinary lifecycle operations never invoke it."""

    plan: Callable[[], DaemonRecoveryPlan]
    retire: Callable[[str], DaemonRecoveryResult]
    restore_plan: Callable[[str], DaemonRecoveryPlan]
    restore: Callable[[str, str], DaemonRecoveryResult]


@dataclass(frozen=True, slots=True)
class DaemonPackageHandoverRecovery:
    """Candidate console for one verified post-trial automatic update interruption."""

    # Separately reviewed incident SHA-256s: mutation, request, automatic updater failure.
    plan: Callable[[tuple[str, str, str]], dict[str, JsonValue]]
    resume: Callable[[tuple[str, str, str], str], dict[str, JsonValue]]
