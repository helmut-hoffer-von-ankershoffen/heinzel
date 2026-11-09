"""Owning implementations for the trusted individual Heinzel dispatch adapter."""

from collections.abc import Callable

from pydantic import JsonValue

from heinzel.kernel import HeinzelError, HeinzelInput, HeinzelInvocations, HeinzelTarget
from heinzel.modules.heinzel._service import HeinzelService
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest

_UNAVAILABLE = "The reviewed Heinzel target is unavailable or has changed"


class BuiltinHeinzelInvocations(HeinzelInvocations):
    """Use the existing durable inbox and pinned manual draft paths."""

    def __init__(self, contracts: HeinzelService, sessions: SessionCrud) -> None:
        self._contracts = contracts
        self._sessions = sessions

    def require_current(self, target: HeinzelTarget) -> None:
        if target.kind == "personal":
            self._sessions.snapshot(target.identity, limit=1)
        elif self._contracts.find(target.identity).sha256 != target.source_digest:
            raise HeinzelError(_UNAVAILABLE)

    def execute(
        self, target: HeinzelTarget, request: HeinzelInput, check_active: Callable[[], None]
    ) -> dict[str, JsonValue]:
        if target.kind == "personal":
            if request.message is None:
                raise HeinzelError(_UNAVAILABLE)
            accepted = self._sessions.accept(
                target.identity,
                MessageRequest(request_id=request.request_id, text=request.message),
                check_active=check_active,
            )
            return {
                "request_id": accepted.request_id,
                "session_id": accepted.session_id,
                "disposition": accepted.disposition,
                "inbox_position": accepted.inbox_position,
            }
        if request.message is not None or target.source_digest is None:
            raise HeinzelError(_UNAVAILABLE)
        result = self._contracts.run_reviewed(target.identity, target.source_digest, check_active=check_active)
        return {"contract": result.contract, "draft": str(result.draft), "sha256": result.sha256}
