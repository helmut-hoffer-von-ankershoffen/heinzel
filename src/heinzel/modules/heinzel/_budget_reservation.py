"""Conservative owner-wide durable reservations for externally priced model requests.

A reservation is charged in full permanently, including on crash or uncertain provider outcome. This internal
ledger is not model admission: prices and request bounds must come from a separately verified trusted provider policy.
"""

import hashlib
import json
from decimal import ROUND_CEILING, Decimal, localcontext
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from heinzel import HeinzelError
from heinzel.modules.heinzel._session_protocol import SessionRepository

_MAX_REQUEST_ID = 128
_MAX_CAP = Decimal(1000000000000)
_MAX_RATE = Decimal(1000000)
_MAX_TURN_TOKENS = 100_000_000


class PriceEnvelope(BaseModel):
    """A trusted, externally verified maximum price and enforced request bounds for one configured model.

    Do not populate from a model, tool, request or provider status. A host policy must verify its source, validity,
    billing mode, all billable token classes, provider identity and per-request token limits before using it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str = Field(min_length=1, max_length=128)
    price_revision: str = Field(min_length=1, max_length=128)
    max_input_tokens: int = Field(ge=1, le=10_000_000)
    max_output_tokens: int = Field(ge=1, le=1_000_000)
    max_requests: int = Field(ge=1, le=100)
    input_usd_per_million: Decimal = Field(gt=0, le=_MAX_RATE, decimal_places=6, allow_inf_nan=False)
    output_usd_per_million: Decimal = Field(gt=0, le=_MAX_RATE, decimal_places=6, allow_inf_nan=False)

    def maximum(self) -> Decimal:
        """Reserve the worst case for every possible request, not expected or reported usage."""
        return (
            Decimal(self.max_requests)
            * (
                Decimal(self.max_input_tokens) * self.input_usd_per_million
                + Decimal(self.max_output_tokens) * self.output_usd_per_million
            )
            / Decimal(1_000_000)
        )


class _Reservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session: str
    request_id: str
    turn_id: str
    owner: str
    envelope: PriceEnvelope
    amount: Decimal = Field(gt=0, allow_inf_nan=False)


class ReservationResult(BaseModel):
    """A new reservation authorizes at most one attempt; a replay never authorizes another."""

    disposition: Literal["reserved", "existing"]
    amount: Decimal
    session_reserved: Decimal
    owner_reserved: Decimal


def initialize_budget(repository: SessionRepository, session_id: str) -> None:
    """Create an owner ledger only if no known prior model work needs reconciliation.

    This checks the durable personal session journals; it cannot discover older unrelated chat stores or model
    usage that bypassed those journals. Do not treat it as complete legacy migration or expose it as such.

    Raises:
        HeinzelError: If historical model work, existing uninitialized ledger entries or journal damage is found.
    """
    owner = repository.principal_id
    with repository.budget_writer(session_id) as writer:
        ledger = repository.budget_read(session_id).events
        initialized = bool(
            ledger and ledger[0].kind == "model.ledger-initialized" and ledger[0].data == {"owner": owner}
        )
        if ledger and not initialized:
            msg = "Existing budget usage requires reconciliation before initialization"
            raise HeinzelError(msg)
        accounted: set[tuple[str, str]] = set()
        for event in ledger[1:]:
            if event.kind != "model.reserved":
                msg = "The owner budget ledger contains an unknown record"
                raise HeinzelError(msg)
            try:
                record = _Reservation.model_validate(event.data)
            except ValueError:
                msg = "The owner budget ledger contains an invalid reservation"
                raise HeinzelError(msg) from None
            expected = hashlib.sha256(f"{record.session}\0{record.request_id}".encode()).hexdigest()
            if record.owner != owner or record.amount != record.envelope.maximum() or event.key != expected:
                msg = "The owner budget ledger contains an invalid reservation"
                raise HeinzelError(msg)
            accounted.add((record.session, record.turn_id))
        for head in repository.heads():
            if head.problem:
                msg = "A personal session needs repair before budget initialization"
                raise HeinzelError(msg)
            events = repository.control_read(head.session).events
            if any(
                event.kind in {"message.replied", "interactive.started"}
                and (event.turn is None or (head.session, event.turn) not in accounted)
                for event in events
            ):
                msg = "Prior model work requires usage reconciliation before budget initialization"
                raise HeinzelError(msg)
        if not initialized:
            writer.append("model.ledger-initialized", key="initialized", data={"owner": owner})


def reserve_model_spend(  # noqa: PLR0913, PLR0912, PLR0915, C901 - one atomic policy and ledger boundary
    repository: SessionRepository,
    session_id: str,
    request_id: str,
    envelope: PriceEnvelope,
    *,
    message_cap: Decimal,
    require_initialized: bool = False,
    turn_id: str | None = None,
    max_turn_tokens: int | None = None,
    max_turn_output_tokens: int | None = None,
    session_cap: Decimal,
    owner_cap: Decimal,
) -> ReservationResult:
    """Atomically reserve a worst-case model charge across all sessions belonging to the current owner.

    An existing reservation is never renewed, retried, or treated as proof of a completed request. No release is
    implemented: even a successful or uncertain request stays charged at the full reservation. These guarantees
    depend on independently enforcing the envelope in the actual provider and using authoritative price/cap inputs.

    Raises:
        HeinzelError: If inputs, ownership, ledger integrity, replay or caps fail.
    """
    owner = repository.principal_id
    if not request_id or len(request_id) > _MAX_REQUEST_ID:
        msg = "The reservation request identity is invalid"
        raise HeinzelError(msg)
    for cap in (message_cap, session_cap, owner_cap):
        if not cap.is_finite() or cap <= 0 or cap > _MAX_CAP:
            msg = "Budget caps must be finite and positive"
            raise HeinzelError(msg)
    turn_id = turn_id or request_id
    if max_turn_tokens is not None and (
        type(max_turn_tokens) is not int or not 1 <= max_turn_tokens <= _MAX_TURN_TOKENS
    ):
        msg = "The approved turn token cap is invalid"
        raise HeinzelError(msg)
    if max_turn_output_tokens is not None and (
        type(max_turn_output_tokens) is not int or not 1 <= max_turn_output_tokens <= _MAX_TURN_TOKENS
    ):
        msg = "The approved turn output token cap is invalid"
        raise HeinzelError(msg)
    if len(turn_id) > _MAX_REQUEST_ID:
        msg = "The reservation turn identity is invalid"
        raise HeinzelError(msg)
    amount = envelope.maximum()
    key = hashlib.sha256(f"{session_id}\0{request_id}".encode()).hexdigest()
    requested = _Reservation(
        session=session_id, request_id=request_id, turn_id=turn_id, owner=owner, envelope=envelope, amount=amount
    )
    with repository.budget_writer(session_id) as writer:
        events = repository.budget_read(session_id).events
        records: list[_Reservation] = []
        initialized = bool(
            events and events[0].kind == "model.ledger-initialized" and events[0].data == {"owner": owner}
        )
        if require_initialized and not initialized:
            msg = "The owner budget ledger requires initialization and usage reconciliation"
            raise HeinzelError(msg)
        prior = None
        try:
            for event in events:
                if event.kind == "model.ledger-initialized" and event == events[0] and initialized:
                    continue
                if event.kind != "model.reserved":
                    msg = "The owner budget ledger contains an unknown record"
                    raise HeinzelError(msg)
                record = _Reservation.model_validate(event.data)
                expected = hashlib.sha256(f"{record.session}\0{record.request_id}".encode()).hexdigest()
                if record.owner != owner or record.amount != record.envelope.maximum() or event.key != expected:
                    msg = "The owner budget ledger contains an invalid reservation"
                    raise HeinzelError(msg)
                records.append(record)
                if event.key == key:
                    if prior is not None:
                        msg = "The owner budget ledger contains duplicate reservations"
                        raise HeinzelError(msg)
                    prior = record
        except ValidationError:
            msg = "The owner budget ledger contains an invalid reservation"
            raise HeinzelError(msg) from None
        with localcontext() as context:
            context.prec = 80
            context.rounding = ROUND_CEILING
            owner_total = sum((record.amount for record in records), Decimal(0))
            session_total = sum((record.amount for record in records if record.session == session_id), Decimal(0))
            turn_total = sum(
                (record.amount for record in records if record.session == session_id and record.turn_id == turn_id),
                Decimal(0),
            )
            turn_with_new = turn_total + amount
            owner_with_new = owner_total + amount
            session_with_new = session_total + amount
        if prior is not None:
            if prior != requested:
                msg = "This reservation identity already names different model bounds"
                raise HeinzelError(msg)
            return ReservationResult(
                disposition="existing", amount=amount, session_reserved=session_total, owner_reserved=owner_total
            )
        reserved_tokens = sum(
            record.envelope.max_requests * (record.envelope.max_input_tokens + record.envelope.max_output_tokens)
            for record in records
            if record.session == session_id and record.turn_id == turn_id
        )
        requested_tokens = envelope.max_requests * (envelope.max_input_tokens + envelope.max_output_tokens)
        if max_turn_tokens is not None and reserved_tokens + requested_tokens > max_turn_tokens:
            msg = "The worst-case model reservation exceeds the approved turn token cap"
            raise HeinzelError(msg)
        reserved_output = sum(
            record.envelope.max_requests * record.envelope.max_output_tokens
            for record in records
            if record.session == session_id and record.turn_id == turn_id
        )
        if max_turn_output_tokens is not None and (
            reserved_output + envelope.max_requests * envelope.max_output_tokens > max_turn_output_tokens
        ):
            msg = "The worst-case model reservation exceeds the approved turn output token cap"
            raise HeinzelError(msg)
        if turn_with_new > message_cap or session_with_new > session_cap or owner_with_new > owner_cap:
            msg = "The worst-case model reservation exceeds an approved budget cap"
            raise HeinzelError(msg)
        data = json.loads(requested.model_dump_json())
        writer.append("model.reserved", key=key, data=data)
        return ReservationResult(
            disposition="reserved",
            amount=amount,
            session_reserved=session_with_new,
            owner_reserved=owner_with_new,
        )
