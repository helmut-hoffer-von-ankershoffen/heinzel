"""Delete a retired Heinzel for good: intent first, then each store, then a content-free tombstone (ADR 0148)."""

import logging
import sys
import unicodedata
from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel, Field

from heinzel import ApprovalChannel, HeinzelError, Kernel, Notifications, SessionEvent
from heinzel.kernel import PersonalChatRemoval
from heinzel.modules.heinzel._channel_binding import ChannelBinding
from heinzel.modules.heinzel._history_fts import forget_indexed_history
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionId, SessionList
from heinzel.modules.heinzel._session_repository import SESSION_NAMESPACES

logger = logging.getLogger(__name__)


class HeinzelDeleted(BaseModel):
    """The content-free record that stays after a Heinzel is deleted for good."""

    session_id: SessionId = Field(description="The identity of the deleted Heinzel; nothing else about it is kept.")
    state: Literal["deleted", "forgetting"] = Field(
        default="deleted",
        description="`deleted`: gone, no undo. `forgetting`: gone from hz; a channel plugin still has to forget its "
        "copies, retried on every list.",
    )
    deleted_at: datetime | None = Field(default=None, description="When the deletion finished; none while forgetting.")
    waiting_for: list[str] = Field(
        default_factory=list[str], description="Channel plugins that still have to forget this Heinzel's requests."
    )


class PostedApprovals:
    """Closes and forgets the approval requests a Heinzel posted on channels, through the captured binding."""

    def __init__(self, kernel: Kernel) -> None:
        """Bind channels by name from this kernel's source-bound channel services."""
        self.kernel = kernel

    def check(self, channel: str) -> None:
        """Refuse before anything is removed when the plugin holding posts is not loaded or is turned off.

        Raises:
            HeinzelError: If the channel is not one loaded, switched-on approval channel.
        """
        try:
            ChannelBinding(self.kernel, channel, "approval")
        except HeinzelError:
            msg = (
                f"The {channel} plugin keeps this Heinzel's approval requests and is not loaded or is turned off; "
                "turn it on, then delete again"
            )
            raise HeinzelError(msg) from None

    def forget(self, channel: str, reference: str) -> None:
        """Close the posted request if the network allows, then let the plugin delete its own copy locally.

        Raises:
            HeinzelError: If the plugin cannot forget now; the deletion retries it later.
        """
        binding = ChannelBinding(self.kernel, channel, "approval")
        try:
            binding.call(lambda service: cast("ApprovalChannel", service).settle(reference, "superseded"))
        except Exception:  # noqa: BLE001 - settling is best effort; an offline channel must not block a deletion
            logger.warning("closing a %s request of a deleted Heinzel failed; it is forgotten anyway", channel)
        binding.call(lambda service: cast("ApprovalChannel", service).forget(reference))


def typed_name(prompt: str) -> str:
    """Read the name a person types at an interactive terminal; a pipe or a script cannot answer.

    Raises:
        HeinzelError: If standard input is not a terminal.
    """
    if not sys.stdin.isatty():
        msg = "Deleting for good needs a person at a terminal to type the name; run it in an interactive terminal"
        raise HeinzelError(msg)
    return input(prompt)


def _posts(crud: SessionCrud, session_id: str, events: list[SessionEvent]) -> list[dict[str, str]]:
    """The content-free channel references of every approval request this Heinzel posted."""
    approvals: set[str | None] = set()
    for event in events:
        if event.kind == "channel.proposed":
            body = crud.read_body(session_id, event)
            if isinstance(body, dict) and body.get("kind") == "approval":
                approvals.add(event.key)
    posts: list[dict[str, str]] = []
    for event in events:
        receipt = event.data.get("receipt")
        if event.kind != "channel.receipt" or event.key not in approvals or not isinstance(receipt, dict):
            continue
        channel, reference = receipt.get("channel"), receipt.get("reference")
        if isinstance(channel, str) and isinstance(reference, str):
            posts.append({"channel": channel, "reference": reference})
    return posts


def _same_name(typed: str, current: str) -> bool:
    return unicodedata.normalize("NFC", typed) == unicodedata.normalize("NFC", current)


class HeinzelDeletion:
    """Removes a retired Heinzel's journals, bodies, chats, notices and channel copies, resumably and idempotently."""

    def __init__(
        self,
        crud: SessionCrud,
        chats: PersonalChatRemoval | None,
        notifications: Notifications | None = None,
        posted: PostedApprovals | None = None,
    ) -> None:
        """Use the owner's session service and the optional runner, notification and channel services."""
        self.crud = crud
        self.chats = chats
        self.notifications = notifications
        self.posted = posted

    def delete(self, session_id: str, name: str) -> HeinzelDeleted:
        """Delete a retired Heinzel after its typed name; a retry finishes or repeats the same result.

        Raises:
            HeinzelError: For a wrong name, a Heinzel that is not retired, one finishing a turn or a document
                append, or a channel plugin holding its posts that is not loaded or is turned off.
        """
        repository = self.crud.repository
        if repository.deletion_state(session_id) == "live":
            # Lock order (ADR 0148 §5): main and channel-processing without waiting, so a running turn or channel
            # reply refuses; then control; the catalog is taken inside. Every check runs under these locks.
            with (
                repository.main_writer(session_id),
                repository.channel_writer(session_id),
                repository.control_writer(session_id),
            ):
                if not _same_name(name, self.crud.retired_name(session_id)):
                    msg = "The name does not match this Heinzel; type its name exactly to delete it"
                    raise HeinzelError(msg)
                if repository.unsettled_target_intents(session_id):
                    msg = "A document change of this Heinzel has no recorded outcome; settle it before deleting"
                    raise HeinzelError(msg)
                posts = _posts(self.crud, session_id, repository.control_read(session_id).events)
                for channel in sorted({post["channel"] for post in posts}):
                    if self.posted is None:
                        msg = "Channel approvals cannot be closed in this process; delete from the app instead"
                        raise HeinzelError(msg)
                    self.posted.check(channel)
                repository.mark_deleting(session_id, posts)
        return self.finish(session_id)

    def finish(self, session_id: str) -> HeinzelDeleted:
        """Complete a started deletion: hz's own content first, then each plugin's copies, then the tombstone.

        Every step is a no-op when already done, and VACUUM runs on every attempt. A plugin that cannot forget yet
        never holds hz's removal back: its references stay in the intent and the next list retries them.

        Raises:
            HeinzelError: If the deletion was never started or an hz store cannot be removed yet.
        """
        repository = self.crud.repository
        if repository.deletion_state(session_id) == "deleting":
            if self.chats is not None:
                self.chats.remove_personal(session_id)
            if self.notifications is not None:
                self.notifications.forget_heinzel(session_id)
            for namespace in SESSION_NAMESPACES:
                repository.remove_session_store(namespace, session_id)
            forget_indexed_history(self.crud, session_id)
            waiting = self._forget_posts(session_id)
            if waiting:
                return HeinzelDeleted(session_id=session_id, state="forgetting", waiting_for=waiting)
            repository.mark_deleted(session_id)
        deleted_at = repository.deleted_at(session_id)
        if deleted_at is None:
            msg = "This Heinzel is not being deleted"
            raise HeinzelError(msg)
        return HeinzelDeleted(session_id=session_id, deleted_at=deleted_at)

    def _forget_posts(self, session_id: str) -> list[str]:
        """Ask each plugin to forget; record each success. Returns the plugins still waiting."""
        repository = self.crud.repository
        waiting: set[str] = set()
        for post in repository.deletion_posts(session_id):
            if self.posted is None:
                waiting.add(post["channel"])
                continue
            try:
                self.posted.forget(post["channel"], post["reference"])
            except Exception:  # noqa: BLE001 - one plugin's failure must not hold the others or hz's removal back
                logger.warning("the %s plugin could not forget a deleted Heinzel's request yet", post["channel"])
                waiting.add(post["channel"])
                continue
            repository.mark_forgotten(session_id, post["channel"], post["reference"])
        return sorted(waiting)

    def listing(self) -> SessionList:
        """List the owner's Heinzels after finishing any deletion a crash interrupted."""
        self.finish_pending()
        return self.crud.listing()

    def finish_pending(self) -> None:
        """Finish deletions a crash interrupted; one that still fails is retried on the next call."""
        for session_id in self.crud.repository.pending_deletions():
            try:
                self.finish(session_id)
            except (HeinzelError, OSError):
                logger.warning("finishing the deletion of %s failed; the next list retries it", session_id)
