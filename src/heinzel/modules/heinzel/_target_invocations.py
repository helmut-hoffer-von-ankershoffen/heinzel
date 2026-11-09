"""Owning implementations for the trusted individual Heinzel dispatch adapter."""

from collections.abc import Callable

from pydantic import JsonValue

from heinzel.kernel import HeinzelError, HeinzelInput, HeinzelInvocations, HeinzelTarget
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest

_UNAVAILABLE = "The reviewed Heinzel target is unavailable or has changed"


class BuiltinHeinzelInvocations(HeinzelInvocations):
    """Use the existing durable inbox without any automatic model launch."""

    def __init__(self, sessions: SessionCrud) -> None:
        self._sessions = sessions

    def require_current(self, target: HeinzelTarget) -> None:
        if target.kind != "personal":
            raise HeinzelError(_UNAVAILABLE)
        self._sessions.require_inbox_target(target.identity)

    def execute(
        self, target: HeinzelTarget, request: HeinzelInput, check_active: Callable[[], None]
    ) -> dict[str, JsonValue]:
        if target.kind != "personal" or request.message is None:
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
