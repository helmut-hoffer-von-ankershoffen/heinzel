"""Exact one-time notification decisions; no scheduling or external communication."""

import contextlib
import hashlib
import json
import secrets
from datetime import timedelta
from typing import Literal

from pydantic import JsonValue

from heinzel import CurrentUser, HeinzelError, NoticeKind, Notifications, SessionEvent
from heinzel.kernel._approval import (
    ApprovalConfirmation,
    ApprovalIntent,
    ApprovalRecord,
    approval_proof_hash,
    arguments_hash,
    consume_approval,
)
from heinzel.modules.heinzel._notification_proposals import NotificationProposal, NotificationProposals
from heinzel.modules.heinzel._session_crud import SessionCrud


class NotificationDelivery:
    """A trusted confirmation can queue one exact draft, subject to local notification policy."""

    def __init__(self, crud: SessionCrud, current: CurrentUser, notifications: Notifications) -> None:
        self.crud, self.current, self.notifications = crud, current, notifications

    def _owner(self) -> None:
        context = self.current.context()
        if (
            context.principal != self.current.owner()
            or context.principal.id != self.crud.repository.principal_id
            or context.authentication not in {"cli", "api-bearer", "web-session", "web-device"}
            or "private:write" not in context.permissions
        ):
            msg = "Notification approval requires its authenticated owner"
            raise HeinzelError(msg)

    def _intent(self, proposal: NotificationProposal) -> ApprovalIntent:
        return ApprovalIntent(
            owner=self.crud.repository.principal_id,
            audience="personal-notification",
            session=proposal.session_id,
            action="queue-agent-notification",
            resource=proposal.request_id,
            arguments_hash=arguments_hash(proposal.model_dump(mode="json")),
            contract_revision=0,
            grant_revision=0,
            policy_revision=0,
        )

    def _proposal(self, session: str, request: str) -> NotificationProposal:
        self._owner()
        return NotificationProposals(self.crud).get(session, request, self.current)

    def issue(self, session: str, request: str, provenance: str) -> str:
        """Issue internal proof for one exact stored draft; this does not queue anything.

        Raises:
            HeinzelError: If an earlier confirmation is present or owner verification fails.
        """
        proposal = self._proposal(session, request)
        proof = secrets.token_urlsafe(32)
        with self.crud.repository.control_writer(session) as writer:
            if any(
                event.kind == "notification.approved" and event.data.get("request_id") == request
                for event in self.crud.repository.control_read(session).events
            ):
                msg = "This notification decision already exists; inspect its state"
                raise HeinzelError(msg)
            now = self.crud.clock()
            record = ApprovalRecord(
                intent=self._intent(proposal),
                proof_hash=approval_proof_hash(proof),
                nonce=secrets.token_hex(32),
                provenance=provenance[:128],
                decision="approve",
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )
            writer.append("approval.issued", key=record.proof_hash, data=record.model_dump(mode="json"))
        return proof

    def confirm(
        self,
        session: str,
        request: str,
        review: dict[str, JsonValue],
        proof: str,
        confirmation: ApprovalConfirmation,
    ) -> Literal["recorded", "uncertain"]:
        """Queue once after exact trusted confirmation; interrupted outcomes are never retried automatically.

        Raises:
            HeinzelError: If the reviewed decision or owner cannot be verified.
        """
        proposal = self._proposal(session, request)
        args: dict[str, JsonValue] = {
            "session_id": session,
            "request_id": request,
            "review": review,
            "approval_proof": proof,
        }
        if not confirmation.matches("heinzel", "queue-agent-notification", args) or arguments_hash(
            review
        ) != arguments_hash(proposal.model_dump(mode="json")):
            msg = "Notification approval is invalid or unavailable"
            raise HeinzelError(msg)
        repo = self.crud.repository
        with repo.main_writer(session):

            def current(_events: list[SessionEvent]) -> ApprovalIntent:
                if any(
                    event.kind == "notification.approved" and event.data.get("request_id") == request
                    for event in repo.control_read(session).events
                ):
                    msg = "This notification decision already exists"
                    raise HeinzelError(msg)
                return self._intent(self._proposal(session, request))

            consumption = consume_approval(
                repo,
                session,
                proof,
                self._intent(proposal),
                current,
                self.crud.clock,
                event_kind="notification.approved",
                effect_data=lambda _w, _e, _p: {"request_id": request},
            )
            if consumption.disposition != "recorded":
                msg = "This notification decision was already consumed; inspect its state"
                raise HeinzelError(msg)
            key = self._key(session, request)
            with repo.control_writer(session) as writer:
                writer.append("notification.attempted", key=request, data={"request_id": request})
            try:
                recorded = self.notifications.emit_once(
                    NoticeKind.HEINZEL,
                    proposal.title,
                    proposal.body,
                    route=f"/heinzel?chat={session}",
                    command="hz heinzel sessions",
                    key=key,
                )
            except Exception:  # noqa: BLE001 - no provider diagnostics or private content escape
                recorded = False
            status: Literal["recorded", "uncertain"] = "recorded" if recorded else "uncertain"
            with repo.control_writer(session) as writer:
                writer.append(f"notification.{status}", key=request, data={"request_id": request})
            if recorded:
                self._compact(key)
            return status

    def _compact(self, key: str) -> None:
        # The authoritative journal is already recorded; failed cleanup leaves a harmless durable receipt.
        with contextlib.suppress(Exception):
            self.notifications.forget_completed({key})

    def _key(self, session: str, request: str) -> str:
        identity = f"{self.crud.repository.principal_id}\0{session}\0{request}"
        return "personal-notification:" + hashlib.sha256(identity.encode()).hexdigest()

    def recover(self, session: str, request: str, *, write_receipt: bool = True) -> Literal["recorded", "uncertain"]:
        """Inspect an interrupted queue attempt without invoking delivery again.

        Raises:
            HeinzelError: If the exact owner-bound approval or attempt is invalid.
        """
        proposal = self._proposal(session, request)
        repo = self.crud.repository
        with repo.main_writer(session):
            events = repo.control_read(session).events
            approved = [e for e in events if e.kind == "notification.approved" and e.data.get("request_id") == request]
            attempts = [e for e in events if e.kind == "notification.attempted" and e.key == request]
            if len(approved) != 1 or len(attempts) != 1 or attempts[0].seq <= approved[0].seq:
                msg = "The notification attempt has no valid decision"
                raise HeinzelError(msg)
            event = approved[0]
            issued = [e for e in events if e.kind == "approval.issued" and e.key == event.data.get("proof_hash")]
            if len(issued) != 1:
                msg = "The notification approval evidence is invalid"
                raise HeinzelError(msg)
            try:
                record = ApprovalRecord.model_validate_json(json.dumps(issued[0].data))
            except ValueError:
                msg = "The notification approval evidence is invalid"
                raise HeinzelError(msg) from None
            if (
                issued[0].seq >= event.seq
                or record.intent != self._intent(proposal)
                or record.proof_hash != issued[0].key
                or record.nonce != event.data.get("consumed_nonce")
                or record.intent.model_dump(mode="json") != event.data.get("intent")
                or record.provenance != event.data.get("provenance")
                or not record.issued_at <= event.ts < record.expires_at
                or issued[0].ts > event.ts
                or sum(e.data.get("consumed_nonce") == record.nonce for e in events) != 1
                or any(e.kind == "approval.revoked" and e.key == record.nonce for e in events)
            ):
                msg = "The notification approval evidence is invalid"
                raise HeinzelError(msg)
            recorded = [e for e in events if e.kind == "notification.recorded" and e.key == request]
            if len(recorded) > 1 or (recorded and recorded[0].seq <= attempts[0].seq):
                msg = "The notification outcome record is inconsistent"
                raise HeinzelError(msg)
            if recorded:
                return "recorded"
            try:
                known = self.notifications.has_once(self._key(session, request))
            except Exception:  # noqa: BLE001 - do not expose provider diagnostics
                known = None
            if known is not True:
                return "uncertain"
            if write_receipt:
                with repo.control_writer(session) as writer:
                    writer.append("notification.recorded", key=request, data={"request_id": request})
                self._compact(self._key(session, request))
            return "recorded"
