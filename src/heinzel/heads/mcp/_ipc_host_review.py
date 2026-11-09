"""One exact MCP review over a supervisor-created, unnamed host control channel."""

import json
import socket
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

from heinzel.heads.mcp._ipc_approval import HostMcpConnectionApprovals
from heinzel.heads.mcp._ipc_descriptors import receive_exact
from heinzel.kernel import HeinzelError
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._logging import get_logger, step

if TYPE_CHECKING:
    from pydantic import JsonValue

_LIMIT = 16 * 1024
_REFUSED = "Local MCP host review was declined or unavailable"
log = get_logger("mcp")


@contextmanager
def host_review_channel(
    authority: HostMcpConnectionApprovals, instance_id: str
) -> Generator[tuple[socket.socket, Callable[[], None]]]:
    """Create one private descriptor for a trusted reviewer and the broker's matching handler.

    The supervisor passes the returned descriptor only to the native or explicit local CLI reviewer it owns.
    There is no listener, bearer or UID-based route to this capability. The locator only selects a review.

    Raises:
        HeinzelError: If the pending instance is unavailable or the review does not fit the control channel.

    Yields:
        The reviewer descriptor and a one-shot broker handler. The broker closes its end on completion.
    """
    review = next((item for item in authority.pending_reviews() if item.instance_id == instance_id), None)
    if review is None:
        raise HeinzelError(_REFUSED)
    plan = review.plan
    exact: dict[str, JsonValue] = {
        "instance": review.instance_id,
        "challenge": review.challenge,
        "peer_uid": review.peer_uid,
        "registration": plan.generation.registration_id,
        "generation": plan.generation.number,
        "grant": plan.grant_id,
        "grant_revision": plan.grant_revision,
        "grant_valid_until": plan.valid_until,
        "review_expires_at": review.expires_at,
        "terms": cast("JsonValue", json.loads(plan.terms_json)),
    }
    with exact_host_review_channel(exact, lambda: authority.approve(review)) as channel:
        yield channel


@contextmanager
def exact_host_review_channel(
    exact: "dict[str, JsonValue]", approve: Callable[[], None]
) -> Generator[tuple[socket.socket, Callable[[], None]]]:
    """Offer one exact action over a private descriptor from the trusted host.

    Raises:
        HeinzelError: If the offer exceeds the control bound or the exact answer is unavailable.

    Yields:
        The reviewer channel and a one-shot handler that delivers the decision to its bound action.
    """
    digest = arguments_hash(exact)
    offer = json.dumps({"review": exact, "digest": digest}, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
    if len(offer) > _LIMIT:
        raise HeinzelError(_REFUSED)
    broker, reviewer = socket.socketpair()
    with broker, reviewer:
        # The fixed-length answer contains no untrusted JSON or trailing data to interpret.
        accepted = f"approve {digest}\n".encode("ascii")

        def serve() -> None:
            with broker, step(log, "mcp.ipc.host_review", operation="ipc", step="host_review"):
                deadline = time.monotonic() + 60
                broker.settimeout(60)
                try:
                    broker.sendall(offer)
                    if receive_exact(broker, len(accepted), deadline) != accepted:
                        raise HeinzelError(_REFUSED)
                except OSError:
                    raise HeinzelError(_REFUSED) from None
                approve()

        yield reviewer, serve
