"""Internal schema preview for an exact approved read set; no tool registration or dispatch."""

from pydantic import JsonValue

from heinzel import HeinzelError, SessionWriter
from heinzel.kernel._approval import arguments_hash
from heinzel.modules.heinzel._connected_read_set_approvals import ConnectedReadSetApprovals

_HUMAN_HEADS = frozenset({"cli", "api-bearer", "web-session", "web-device"})


def read_set_tool_preview(
    approvals: ConnectedReadSetApprovals, session: str, writer: SessionWriter
) -> dict[str, JsonValue] | None:
    """Describe approved handles under an active human owner writer without executing or registering a tool.

    Fresh provider review may observe the selected account; no provider read, proof or effect is issued here.
    The result is only a preview: callers must revalidate at dispatch, and daemon use is not authorized.
    """
    current = approvals.sets.current
    repo = approvals.crud.repository
    try:
        context = current.context()
        if (
            context.authentication not in _HUMAN_HEADS
            or context.principal != current.owner()
            or context.principal.id != repo.principal_id
            or "private:read" not in context.permissions
            or not repo.is_main_writer(session, writer)
        ):
            return None
        approved = approvals.approved(session)
        observed, _intent = approvals.reviews.review_intent_locked(
            session, writer, approved.request_id, approved.revision
        )
        if arguments_hash(approved.model_dump(mode="json")) != arguments_hash(observed.model_dump(mode="json")):
            return None
    except (HeinzelError, ValueError):
        return None
    handles: list[JsonValue] = [entry.handle for entry in approved.proposal.entries]
    return {
        "name": "connected_read_set",
        "description": "Read one separately approved input with host-controlled arguments.",
        "schema": {
            "type": "object",
            "properties": {"handle": {"type": "string", "enum": handles}},
            "required": ["handle"],
            "additionalProperties": False,
        },
    }
