"""Delete a retired Heinzel for good: intent first, then each store, then a content-free tombstone (ADR 0148)."""

import logging
import sys
import time
import unicodedata
from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel, Field

from heinzel import ApprovalChannel, Check, HeinzelError, Kernel, SessionEvent, Status
from heinzel.kernel import HeinzelNoticeRemoval, PersonalChatRemoval
from heinzel.kernel._logging import get_logger, log_event
from heinzel.modules.heinzel import _history_fts
from heinzel.modules.heinzel._channel_binding import ChannelBinding
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import SessionId, SessionList
from heinzel.modules.heinzel._session_repository import SESSION_NAMESPACES

_EVENTS = get_logger("heinzel.delete")


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

    def settle(self, channel: str, reference: str) -> None:
        """Close the posted request on the channel, best effort: offline or signed out only logs."""
        try:
            binding = ChannelBinding(self.kernel, channel, "approval")
            binding.call(lambda service: cast("ApprovalChannel", service).settle(reference, "superseded"))
        except Exception:  # noqa: BLE001 - settling is best effort; an offline channel must not block a deletion
            log_event(_EVENTS, "heinzel.delete.settle_failed", level=logging.WARNING, channel=channel)

    def forget(self, channel: str, request_id: str, reference: str | None) -> None:
        """Let the plugin delete its own copies of one request, locally.

        Raises:
            HeinzelError: If the plugin cannot forget now; the deletion retries it later.
        """
        binding = ChannelBinding(self.kernel, channel, "approval")
        binding.call(lambda service: cast("ApprovalChannel", service).forget(request_id, reference))


def typed_name(prompt: str) -> str:
    """Read the name a person types at an interactive terminal; a pipe or a script cannot answer.

    Raises:
        HeinzelError: If standard input is not a terminal.
    """
    if not sys.stdin.isatty():
        msg = "Deleting for good needs a person at a terminal to type the name; run it in an interactive terminal"
        raise HeinzelError(msg)
    return input(prompt)


def _proposed_channel(crud: SessionCrud, session_id: str, event: SessionEvent) -> tuple[str, str] | None:
    """The channel and kind of a channel proposal, or None when its body is no longer retained."""
    try:
        body = crud.read_body(session_id, event)
    except (FileNotFoundError, HeinzelError):
        return None
    if not isinstance(body, dict):
        return None
    channel, kind = body.get("channel"), body.get("kind")
    return (channel, kind) if isinstance(channel, str) and isinstance(kind, str) else None


def _posts(crud: SessionCrud, session_id: str, events: list[SessionEvent]) -> list[dict[str, str]]:
    """The content-free identity of every approval request this Heinzel proposed on a channel, posted or not.

    A plugin may keep a copy before the post returns, so a proposal without a receipt counts too. A proposal whose
    body is no longer retained counts when its receipt names the channel; `forget` is idempotent.
    """
    receipts: dict[str, dict[str, object]] = {}
    for event in events:
        recorded = event.data.get("receipt")
        if event.kind == "channel.receipt" and event.key and isinstance(recorded, dict):
            receipts[event.key] = cast("dict[str, object]", recorded)
    posts: list[dict[str, str]] = []
    for event in events:
        if event.kind != "channel.proposed" or not event.key:
            continue
        receipt = receipts.get(event.key, {})
        proposed = _proposed_channel(crud, session_id, event)
        if proposed is not None and proposed[1] != "approval":
            continue
        channel = proposed[0] if proposed is not None else receipt.get("channel")
        if not isinstance(channel, str):
            log_event(_EVENTS, "heinzel.delete.channel_unknown", level=logging.WARNING, request_id=event.key)
            continue
        post = {"channel": channel, "request_id": event.key}
        reference = receipt.get("reference")
        if isinstance(reference, str):
            post["reference"] = reference
        posts.append(post)
    return posts


def _forget_indexed_history(crud: SessionCrud, session_id: str) -> None:
    """Drop this process's in-memory search index of the Heinzel; another process's is unreadable once gone."""
    repository = crud.repository
    # The index module's own lock and eviction, reused unchanged.
    with _history_fts._LOCK:  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        _history_fts._invalidate(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
            repository.search_storage_id, repository.principal_id, session_id
        )


def _same_name(typed: str, current: str) -> bool:
    return unicodedata.normalize("NFC", typed) == unicodedata.normalize("NFC", current)


_FIRST_RETRY = 60.0
_LAST_RETRY = 3600.0
_RETRY: dict[str, tuple[float, float]] = {}
"""Per session: when the next background retry may run and the current delay; lists poll, plugins need not."""


class HeinzelDeletion:
    """Removes a retired Heinzel's journals, bodies, chats, notices and channel copies, resumably and idempotently."""

    def __init__(
        self,
        crud: SessionCrud,
        chats: PersonalChatRemoval | None,
        notifications: HeinzelNoticeRemoval | None = None,
        posted: PostedApprovals | None = None,
    ) -> None:
        """Use the owner's session service and the optional runner, notification and channel services."""
        self.crud = crud
        self.chats = chats
        self.notifications = notifications
        self.posted = posted

    def _missing_modules(self) -> list[str]:
        """Built-in modules that keep this Heinzel's content but are turned off, so they cannot remove it."""
        return [
            name
            for name, service in (("system.runner", self.chats), ("system.notify", self.notifications))
            if service is None
        ]

    def delete(self, session_id: str, name: str) -> HeinzelDeleted:
        """Delete a retired Heinzel after its typed name; a retry finishes or repeats the same result.

        Raises:
            HeinzelError: For a wrong name, a Heinzel that is not retired, one finishing a turn or a document
                append, a turned-off module that keeps its content, or a channel plugin holding its posts that is not
                loaded or is turned off.
        """
        repository = self.crud.repository
        if repository.deletion_state(session_id) == "live":
            if missing := self._missing_modules():
                msg = f"Turn on {' and '.join(missing)} first: it keeps this Heinzel's chats or notices"
                raise HeinzelError(msg)
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

        Every step is a no-op when already done, and VACUUM runs on every attempt. A module or plugin that cannot
        forget yet never holds the rest back, and the tombstone waits for all of them; the next list retries.

        Raises:
            HeinzelError: If the deletion was never started or an hz store cannot be removed yet.
        """
        repository = self.crud.repository
        if repository.deletion_state(session_id) == "deleting":
            waiting = self._missing_modules()
            if self.chats is not None:
                self.chats.remove_personal(session_id)
            if self.notifications is not None:
                self.notifications.forget_heinzel(session_id)
            for namespace in SESSION_NAMESPACES:
                repository.remove_session_store(namespace, session_id)
            _forget_indexed_history(self.crud, session_id)
            waiting += self._forget_posts(session_id)
            if waiting:
                return HeinzelDeleted(session_id=session_id, state="forgetting", waiting_for=waiting)
            repository.mark_deleted(session_id)
        deleted_at = repository.deleted_at(session_id)
        if deleted_at is None:
            msg = "This Heinzel is not being deleted"
            raise HeinzelError(msg)
        return HeinzelDeleted(session_id=session_id, deleted_at=deleted_at)

    def _forget_posts(self, session_id: str) -> list[str]:
        """Close each post once, ask each plugin to forget, record each success; the plugins still waiting."""
        repository = self.crud.repository
        waiting: set[str] = set()
        for post in repository.deletion_posts(session_id):
            channel, request_id, reference = post["channel"], post["request_id"], post.get("reference")
            if self.posted is None:
                waiting.add(channel)
                continue
            if reference is not None and not repository.was_settled(session_id, request_id):
                self.posted.settle(channel, reference)
                repository.mark_settled(session_id, request_id)
            try:
                self.posted.forget(channel, request_id, reference)
            except Exception:  # noqa: BLE001 - one plugin's failure must not hold the others or hz's removal back
                log_event(_EVENTS, "heinzel.delete.forget_waiting", level=logging.WARNING, channel=channel)
                waiting.add(channel)
                continue
            repository.mark_forgotten(session_id, channel, request_id)
        return sorted(waiting)

    def health(self) -> list[Check]:
        """One doctor finding per deletion that has not finished, naming what it waits for and the fix (ADR 0148).

        A deletion stuck in `forgetting` keeps copies on this machine, so it must surface again, not only once.
        """
        repository = self.crud.repository
        try:
            pending = repository.pending_deletions()
        except HeinzelError:
            return []  # no authenticated owner here: the owner's own doctor run reports it
        checks: list[Check] = []
        for session_id in pending:
            modules = self._missing_modules()
            plugins = sorted({post["channel"] for post in repository.deletion_posts(session_id)})
            if modules:
                fix = f"Turn on {' and '.join(modules)}; hz then finishes the deletion by itself"
            elif plugins:
                fix = f"Turn on the {', '.join(plugins)} plugin; hz retries by itself, or run `hz heinzel sessions`"
            else:
                fix = "Run `hz heinzel sessions` or open Heinzelmännchen to finish it"
            waiting = ", ".join([*modules, *plugins]) or "a retry"
            checks.append(
                Check(
                    name=f"heinzel deletion {session_id}",
                    status=Status.DEGRADED,
                    detail=f"Deleted Heinzel {session_id} is not fully gone yet; waiting for {waiting}",
                    fix=fix,
                    cause="A deletion has not finished",
                )
            )
        return checks

    def listing(self) -> SessionList:
        """List the owner's Heinzels after finishing any deletion a crash interrupted."""
        self.finish_pending()
        return self.crud.listing()

    def finish_pending(self, *, now: float | None = None) -> None:
        """Finish deletions a crash or a busy plugin left; each retries with backoff, from a minute to an hour."""
        moment = time.monotonic() if now is None else now
        for session_id in self.crud.repository.pending_deletions():
            due, delay = _RETRY.get(session_id, (0.0, 0.0))
            if moment < due:
                continue
            try:
                done = self.finish(session_id).state == "deleted"
            except (HeinzelError, OSError):
                log_event(_EVENTS, "heinzel.delete.resume_failed", level=logging.WARNING, session=session_id)
                done = False
            if done:
                _RETRY.pop(session_id, None)
            else:
                delay = min(max(delay * 2, _FIRST_RETRY), _LAST_RETRY)
                _RETRY[session_id] = (moment + delay, delay)
