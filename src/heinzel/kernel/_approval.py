"""Scoped approval records and checks for journal-backed effects (ADR 0068).

`ApprovalIssuer` is an internal service used by explicit CLI/paired-web confirmation and authorization; issued
records are stored privately and never returned to the model or caller. The owning action consumes a proof against
its current intent while holding its own journal lock. This records an application-level user decision, not
hardware-backed proof of physical presence. CLI and paired web may issue a short-lived decision for a pending
schedule action; the MCP chat head consumes that decision from private owner state, without accepting or returning a
bearer. A direct stdio MCP schedule-control call may issue only after a separate challenge is typed on the local
controlling terminal; no proof or human answer is accepted through model-facing tool parameters.
"""

import hashlib
import hmac
import json
import re
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, ValidationError

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._identity import Principal
from heinzel.kernel._session import SessionEvent, SessionRead
from heinzel.kernel._session_store import SessionWriter

_PROOF = re.compile(r"[A-Za-z0-9_-]{43,128}\Z", re.ASCII)
_HASH = r"^[0-9a-f]{64}$"


class ApprovalIntent(BaseModel):
    """The exact action and revisions shown to the person at the trusted confirmation boundary."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    owner: str = Field(pattern=r"^u-[0-9a-f]{32}$")
    audience: str = Field(min_length=1, max_length=128)
    session: str = Field(min_length=1, max_length=128)
    action: str = Field(min_length=1, max_length=128)
    resource: str = Field(min_length=1, max_length=256)
    arguments_hash: str = Field(pattern=_HASH)
    contract_revision: int = Field(ge=0)
    grant_revision: int = Field(ge=0)
    policy_revision: int = Field(ge=0)


class ApprovalRecord(BaseModel):
    """Private issuer record; it is trusted storage data, never a caller-supplied authorization assertion."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    intent: ApprovalIntent
    proof_hash: str = Field(pattern=_HASH)
    nonce: str = Field(pattern=_HASH)
    provenance: str = Field(min_length=1, max_length=128)
    decision: Literal["approve"]
    issued_at: AwareDatetime
    expires_at: AwareDatetime


@dataclass(frozen=True, slots=True)
class ApprovalEvidence:
    """Evidence for an owning journal to consume, not a bearer or a serializable grant."""

    nonce: str
    proof_hash: str
    intent: ApprovalIntent
    provenance: str


class ApprovalIssuer:
    """Internal service a CLI or paired-web confirmation boundary uses to issue an exact, short-lived proof.

    This is deliberately a kernel service rather than an operation: no head or model tool can call it directly.
    An action module implements the intent check and durable record write; the CLI/web confirmation boundary or
    schedule-specific MCP authorization flow calls it only after the person accepts the stored proposal. MCP can
    consume a private handoff proof but cannot issue one.
    """

    def issue(self, _module: str, _operation: str, _arguments: dict[str, JsonValue], _provenance: str) -> str:
        """Issue a proof for an exact stored proposal or fail closed.

        Raises:
            HeinzelError: If this issuer does not own the action or its current intent cannot be approved.
        """
        msg = "Approval is invalid or unavailable"
        raise HeinzelError(msg)

    def review(self, _module: str, _operation: str, _arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Return an owner-scoped human-readable view of an exact intent without issuing a proof.

        Raises:
            HeinzelError: If the action is unsupported or its current intent cannot be reviewed.
        """
        msg = "Approval is invalid or unavailable"
        raise HeinzelError(msg)

    def applied(self, _module: str, _operation: str, _arguments: dict[str, JsonValue]) -> bool:
        """Whether the exact approved effect is present in its authoritative journal.

        This is observational only. It never issues a proof or repeats an operation.
        """
        return False


class ApprovalConfirmation:
    """A short-lived in-process marker for the exact call a trusted confirmation boundary is invoking.

    This is a kernel service, not an operation. It lets the CLI distinguish an internally issued proof passed by
    `chat.confirm` from a caller-supplied proof in process arguments without weakening the CLI's secret-input rule.
    """

    def __init__(self) -> None:
        self._bound: ContextVar[tuple[str, str, str] | None] = ContextVar("hz_approval_confirmation", default=None)

    @contextmanager
    def using(self, module: str, operation: str, arguments: dict[str, JsonValue]) -> Generator[None]:
        """Bind one exact call for the duration of a trusted in-process invoke.

        Raises:
            HeinzelError: If the arguments are not finite JSON.
        """
        try:
            digest = arguments_hash(arguments)
        except ValueError:
            msg = "Approval is invalid or unavailable"
            raise HeinzelError(msg) from None
        token: Token[tuple[str, str, str] | None] = self._bound.set((module, operation, digest))
        try:
            yield
        finally:
            self._bound.reset(token)

    def matches(self, module: str, operation: str, arguments: dict[str, JsonValue]) -> bool:
        """Whether the exact operation arguments match the active trusted confirmation context."""
        bound = self._bound.get()
        if bound is None or bound[:2] != (module, operation):
            return False
        try:
            digest = arguments_hash(arguments)
        except ValueError:
            return False
        return hmac.compare_digest(bound[2], digest)


def _check_json(value: object) -> None:
    if value is None or type(value) in {str, int, float, bool}:
        return
    if type(value) is list:
        for item in cast("list[object]", value):
            _check_json(item)
        return
    if type(value) is dict:
        entries = cast("dict[object, object]", value)
        if not all(type(key) is str for key in entries):
            msg = "Approval arguments must be finite JSON"
            raise ValueError(msg)
        for item in entries.values():
            _check_json(item)
        return
    msg = "Approval arguments must be finite JSON"
    raise ValueError(msg)


def arguments_hash(arguments: JsonValue) -> str:
    """Hash versioned canonical JSON, preserving JSON types and rejecting non-finite numbers.

    Raises:
        ValueError: If arguments are not finite JSON values.
    """
    try:
        _check_json(arguments)
        encoded = json.dumps(arguments, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        msg = "Approval arguments must be finite JSON"
        raise ValueError(msg) from exc
    return hashlib.sha256(b"hz-approval-arguments-v1\0" + encoded.encode("ascii")).hexdigest()


def verify_approval(  # noqa: PLR0913 - each argument is an independent trust input
    proof: str,
    record: ApprovalRecord,
    expected: ApprovalIntent,
    principal: Principal,
    now: datetime,
    *,
    revoked: bool,
) -> ApprovalEvidence | None:
    """Verify a stored record against current trusted state; never consume or assert human presence.

    The caller must hold the authoritative action writer lock and re-read the record, revocation and current
    revisions under that lock. It must atomically append nonce consumption and the exact intent in that same journal.
    A replay disposition, including after expiration, is resolved from the journal before a fresh authorization.
    """
    if not _PROOF.fullmatch(proof) or now.tzinfo is None or now.utcoffset() is None:
        return None
    proof_hash = hashlib.sha256(proof.encode("ascii")).hexdigest()
    if not hmac.compare_digest(proof_hash, record.proof_hash):
        return None
    if (
        revoked
        or record.intent.owner != principal.id
        or arguments_hash(record.intent.model_dump(mode="python")) != arguments_hash(expected.model_dump(mode="python"))
    ):
        return None
    if record.issued_at > now or now >= record.expires_at or record.expires_at <= record.issued_at:
        return None
    return ApprovalEvidence(record.nonce, record.proof_hash, record.intent, record.provenance)


class ApprovalJournalRepository(Protocol):
    """An owner-scoped control journal with one cross-process writer lock."""

    @property
    def principal_id(self) -> str:
        """The current authenticated owner, resolved on every call."""
        ...

    def control_writer(self, session_id: str) -> AbstractContextManager[SessionWriter]:
        """Acquire the authoritative journal writer."""
        ...

    def control_read(self, session_id: str) -> SessionRead:
        """Read the verified journal for the current owner."""
        ...


@dataclass(frozen=True, slots=True)
class ApprovalConsumption:
    """A durable intent's disposition; replay is not permission to perform the effect again."""

    disposition: Literal["recorded", "existing"]
    sequence: int


def consume_approval(  # noqa: PLR0912, PLR0913, PLR0917, C901 - one locked validation and append boundary
    repository: ApprovalJournalRepository,
    session_id: str,
    proof: str,
    requested: ApprovalIntent,
    current_intent: Callable[[list[SessionEvent]], ApprovalIntent],
    clock: Callable[[], datetime],
    *,
    event_kind: str = "approval.intent",
    effect_data: Callable[[SessionWriter, list[SessionEvent], ApprovalEvidence], dict[str, JsonValue]] | None = None,
    event_key: str | None = None,
    idempotency_hash: str | None = None,
) -> ApprovalConsumption:
    """Atomically consume a trusted issued proof with its exact intent; no external effect is performed.

    This is an internal primitive, not an exposed operation. The trusted chat confirmation boundary asks the
    action-specific `ApprovalIssuer` to append a private `approval.issued` record. `current_intent` must derive all
    authoritative revisions from state protected by this same lock, never from a model or transport. A changed
    request cannot reuse the nonce. Existing intents may be read after expiry, but `existing` never authorizes another
    effect. A journal-backed action can provide its event kind and effect data so authorization and the resulting
    state share one durable event. MCP tool calls cannot self-issue proofs: a direct stdio schedule-control call
    obtains terminal confirmation first, then issues and consumes its proof internally; the restricted chat consumer
    can use only a private proof previously issued by a trusted human boundary.

    Raises:
        HeinzelError: If the proof, stored decision or current authority is invalid.
    """
    invalid = "Approval is invalid or unavailable"
    if not _PROOF.fullmatch(proof):
        raise HeinzelError(invalid)
    digest = hashlib.sha256(proof.encode("ascii")).hexdigest()
    with repository.control_writer(session_id) as writer:
        events = repository.control_read(session_id).events
        matches = [event for event in events if event.kind == "approval.issued" and event.key == digest]
        if len(matches) != 1:
            raise HeinzelError(invalid)
        try:
            record = ApprovalRecord.model_validate_json(json.dumps(matches[0].data))
        except (ValueError, ValidationError):
            raise HeinzelError(invalid) from None
        if not hmac.compare_digest(digest, record.proof_hash):
            raise HeinzelError(invalid)
        if record.intent.owner != repository.principal_id or record.intent.session != session_id:
            raise HeinzelError(invalid)
        if arguments_hash(record.intent.model_dump(mode="python")) != arguments_hash(
            requested.model_dump(mode="python")
        ):
            raise HeinzelError(invalid)
        prior = [
            event
            for event in events
            if (event.kind == "approval.intent" and event.key == record.nonce)
            or event.data.get("consumed_nonce") == record.nonce
        ]
        if len(prior) > 1:
            raise HeinzelError(invalid)
        expected_data: dict[str, JsonValue] = {
            "proof_hash": record.proof_hash,
            "intent": record.intent.model_dump(mode="json"),
            "provenance": record.provenance,
        }
        if prior:
            saved_base = {key: prior[0].data.get(key) for key in expected_data}
            if prior[0].kind != event_kind or arguments_hash(saved_base) != arguments_hash(expected_data):
                raise HeinzelError(invalid)
            return ApprovalConsumption("existing", prior[0].seq)
        if event_key is not None:
            if idempotency_hash is None:
                raise HeinzelError(invalid)
            same_request = [event for event in events if event.key == event_key]
            if same_request:
                if len(same_request) != 1:
                    raise HeinzelError(invalid)
                prior_request = same_request[0]
                saved_base = {key: prior_request.data.get(key) for key in expected_data}
                if (
                    prior_request.kind != event_kind
                    or prior_request.data.get("request_hash") != idempotency_hash
                    or arguments_hash(saved_base) != arguments_hash(expected_data)
                ):
                    msg = "This request already identifies a different schedule control"
                    raise HeinzelError(msg)
                return ApprovalConsumption("existing", prior_request.seq)
        revoked = any(event.kind == "approval.revoked" and event.key == record.nonce for event in events)
        evidence = verify_approval(
            proof, record, current_intent(events), Principal(repository.principal_id), clock(), revoked=revoked
        )
        if evidence is None:
            raise HeinzelError(invalid)
        extra = effect_data(writer, events, evidence) if effect_data is not None else {}
        if set(extra) & (set(expected_data) | {"consumed_nonce"}):
            raise HeinzelError(invalid)
        event = writer.append(
            event_kind,
            key=event_key or evidence.nonce,
            data={**expected_data, **extra, "consumed_nonce": evidence.nonce},
        )
        return ApprovalConsumption("recorded", event.seq)
