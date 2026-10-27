"""Closed local control for the one lifecycle store; no plugin discovery or installation-source override."""

from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from heinzel import HeinzelError
from heinzel.modules.system.lifecycle._models import Activation, Digest, Record, RuntimeProof
from heinzel.modules.system.lifecycle._store import LifecycleStore
from heinzel.modules.system.lifecycle._uninstall import uninstall_runtime


class Request(Record):
    """Native main's fixed private control request, never exposed as generic renderer IPC."""

    directory: str = Field(min_length=1, max_length=4096)
    action: Literal["prepare", "switch", "complete", "recover", "cancel", "uninstall", "cleanup"]
    candidate: Digest | None = None
    id: UUID | None = None
    proof: RuntimeProof | None = None
    expected: Activation | None = None
    remove_payloads: bool = Field(default=False, strict=True)
    first_install: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def coherent(self) -> "Request":
        """Reject ignored fields or an unbound action rather than interpreting ambiguous intent.

        Raises:
            ValueError: If the closed action and its fields do not agree.
        """
        fields = self.model_fields_set - {"directory", "action"}
        allowed: dict[str, set[str]] = {
            "prepare": {"candidate", "expected", "first_install"},
            "switch": {"id"},
            "complete": {"id", "proof"},
            "recover": set(),
            "cancel": {"id"},
            "uninstall": {"expected", "remove_payloads", "id"},
            "cleanup": set(),
        }
        if (
            not Path(self.directory).is_absolute()
            or ".." in Path(self.directory).parts
            or not fields <= allowed[self.action]
        ):
            msg = "Invalid lifecycle control intent"
            raise ValueError(msg)
        if self.action in {"switch", "complete", "cancel"} and self.id is None:
            msg = "A lifecycle action requires its exact transition identity"
            raise ValueError(msg)
        if self.action == "uninstall" and self.expected is None:
            msg = "Runtime uninstall requires its exact selected activation"
            raise ValueError(msg)
        if self.action == "prepare" and not {"candidate", "expected"} <= fields:
            msg = "A lifecycle plan requires an explicit target and its exact selected activation"
            raise ValueError(msg)
        if self.first_install and (self.candidate is None or self.expected is not None):
            msg = "A first installation requires a candidate and an explicitly absent activation"
            raise ValueError(msg)
        return self


def execute(request: Request) -> object:
    """Run one transition under the shared exclusive authority, with no duplicated native writer semantics.

    Raises:
        HeinzelError: If state, ownership or actual running proof does not authorize the transition.
    """
    store = LifecycleStore(Path(request.directory))
    with store.lock():
        match request.action:
            case "prepare":
                if store.active() != request.expected:
                    msg = "The selected runtime changed; inspect it before confirming another lifecycle plan."
                    raise HeinzelError(msg)
                return store.prepare(request.candidate, first_install=request.first_install).model_dump(
                    mode="json", by_alias=True
                )
            case "switch":
                active = store.switch(_identifier(request))
                return active.model_dump(mode="json", by_alias=True) if active else None
            case "complete" | "cancel":
                if request.action == "complete":
                    store.complete(_identifier(request), request.proof)
                else:
                    store.cancel(_identifier(request))
                return None
            case "uninstall":
                return uninstall_runtime(
                    store, _expected(request), identifier=request.id, remove_payloads=request.remove_payloads
                ).model_dump(mode="json")
            case "cleanup":
                deployments, environments = store.purge_uninstalled_runtime()
                return {"deployments_removed": deployments, "environments_removed": environments, "data_retained": True}
            case "recover":
                return store.recover()


def _identifier(request: Request) -> UUID:
    if request.id is None:
        msg = "A lifecycle transition identity is required."
        raise HeinzelError(msg)
    return request.id


def _expected(request: Request) -> Activation:
    if request.expected is None:
        msg = "A selected runtime activation is required."
        raise HeinzelError(msg)
    return request.expected
