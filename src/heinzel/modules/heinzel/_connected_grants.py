"""Internal exact connected-read grants; no head operation, model tool, or issuer is registered here."""

import copy
import json
import secrets
from datetime import UTC, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from heinzel import (
    Config,
    ConnectedReadLease,
    ConnectedReadMethod,
    ConnectedReadReview,
    ConnectedReadService,
    CurrentUser,
    HeinzelError,
    Kernel,
    ModuleRecord,
    SessionEvent,
    SessionWriter,
    Settings,
)
from heinzel.kernel import (
    CAPABILITIES,
    PRIVATE_WRITE,
    CapabilitySettings,
    SourceGuardedConnectedService,
    capability_read_schema,
)
from heinzel.kernel._approval import (
    ApprovalConfirmation,
    ApprovalEvidence,
    ApprovalIntent,
    ApprovalRecord,
    approval_proof_hash,
    arguments_hash,
    consume_approval,
)
from heinzel.modules.heinzel._capability_review import (
    describe_capability_read,
    rolling_window_parameter,
    validate_capability_read_arguments,
)
from heinzel.modules.heinzel._session_bodies import immutable_body
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import RequestId, SessionId
from heinzel.modules.heinzel._source_suggestions import latest_source_suggestion

if TYPE_CHECKING:
    from heinzel.modules.heinzel._connected_read_set_approvals import ConnectedReadSetApprovals
    from heinzel.modules.heinzel._connected_read_set_inputs import ReadSetCall

_SESSION = TypeAdapter(SessionId)
_REQUEST = TypeAdapter(RequestId)
_READ_PERMISSION = "private:read"
_MAX_RESULT_BYTES = 16_000
_MAX_SUPPORTED_READS = 64
_HEADS = frozenset({"cli", "api-bearer", "web-session", "web-device"})


def _open_lease(
    service: ConnectedReadService, session: str, operation: str, fixed: dict[str, JsonValue]
) -> ConnectedReadLease:
    try:
        return service.open_lease(session, operation, fixed)
    except Exception:  # noqa: BLE001 - plugin errors may contain credentials
        msg = "The connected read provider could not be opened"
        raise HeinzelError(msg) from None


def _review_lease(lease: ConnectedReadLease) -> ConnectedReadReview:
    try:
        return ConnectedReadReview.validate_detached(lease.review())
    except Exception:  # noqa: BLE001 - plugin errors may contain credentials
        msg = "The connected read provider could not be reviewed"
        raise HeinzelError(msg) from None


def _close_review_lease(lease: ConnectedReadLease) -> None:
    try:
        lease.close()
    except Exception:  # noqa: BLE001 - plugin errors may contain credentials
        msg = "The connected read provider cleanup could not be confirmed"
        raise HeinzelError(msg) from None


def _bounded_set_read(lease: ConnectedReadLease) -> JsonValue:
    try:
        result = TypeAdapter(JsonValue).validate_python(lease.read({}), strict=True)
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False).encode()
        if len(encoded) > _MAX_RESULT_BYTES:
            raise ValueError  # noqa: TRY301
        result = TypeAdapter(JsonValue).validate_json(encoded, strict=True)
    except Exception:  # noqa: BLE001 - provider and input diagnostics are private
        msg = "The connected read began but did not produce a usable result"
        raise ConnectedReadFailedError(msg) from None
    else:
        return result


class ConnectedReadFailedError(HeinzelError):
    """The provider read began but did not produce a safe usable result."""


class ConnectedGrantProposal(BaseModel):
    """One private exact capability/source/account/window proposal; no access until trusted approval."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: RequestId = Field(description="The exact connected-read proposal request.")
    revision: int = Field(description="The owner-scoped proposal revision.")
    capability: str = Field(description="The selected capability requiring human review.")
    method: str = Field(description="The selected read-only capability method.")
    binding_sha256: str = Field(description="Digest of selected source registration and read schema.")
    review: ConnectedReadReview = Field(description="Exact private provider, account, scope and fixed-argument terms.")


class RollingWindowPolicy(BaseModel):
    """Explicit elapsed-time window around a single trusted UTC instant."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    parameter: str = Field(min_length=1, max_length=128, description="The unique direct Window argument.")
    lookback_seconds: int = Field(strict=True, ge=0, le=2678400, description="Elapsed seconds before each read.")
    lookahead_seconds: int = Field(strict=True, ge=0, le=2678400, description="Elapsed seconds after each read.")
    timezone: str = Field(min_length=1, max_length=128, description="IANA display timezone; elapsed bounds use UTC.")

    @model_validator(mode="after")
    def validate_window(self) -> "RollingWindowPolicy":
        """Require a positive span no longer than 31 days and a valid display timezone.

        Raises:
            ValueError: If the span or timezone is invalid.
        """
        if not 0 < self.lookback_seconds + self.lookahead_seconds <= 31 * 86400:
            msg = "The rolling window span must be between one second and 31 days"
            raise ValueError(msg)
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            msg = "The rolling window timezone is invalid"
            raise ValueError(msg) from None
        return self


class RollingConnectedGrantProposal(ConnectedGrantProposal):
    """A distinct reviewed rolling-window grant, never an implicit upgrade of a fixed grant."""

    kind: Literal["rolling-window"] = Field(description="Explicit rolling window grant variant.")
    window_policy: RollingWindowPolicy = Field(description="Exact separately reviewed rolling window policy.")


ConnectedGrant = ConnectedGrantProposal | RollingConnectedGrantProposal
_GRANT = TypeAdapter(ConnectedGrant)


class ConnectedReadOption(BaseModel):
    """Host-selected registered capability description; this conveys no read authority."""

    capability: str = Field(description="Selected capability name.")
    method: str = Field(description="Selected read method.")
    provider: str = Field(description="Selected adapter name.")
    source: str = Field(description="Registered module and distribution identity, not publisher verification.")
    input_schema: dict[str, JsonValue] = Field(description="Exact read method argument schema.")
    window_parameter: str | None = Field(
        default=None, description="Unique direct Window parameter eligible for explicit rolling policy, if any."
    )


class ConnectedReadState(BaseModel):
    """Latest owner-scoped proposal and recorded status, without querying the provider."""

    session_id: SessionId = Field(description="Owner-bound personal session.")
    proposal: ConnectedGrant | None = Field(description="Latest recorded proposal, if any.")
    status: Literal["none", "proposed", "approved", "revoked", "stale"] = Field(
        description="Recorded state; source and account are revalidated before each read."
    )
    descriptor: ConnectedReadOption | None = Field(description="Currently selected matching schema, if available.")


class ConnectedReadGrants:
    """Owner-bound internal grant store; caller must construct a fresh kernel from trusted current settings.

    This service is unregistered: no model, MCP tool or API head can issue its proof. The issuing seam is intended
    only for a trusted human confirmation after the exact stored review is shown.
    """

    def __init__(self, crud: SessionCrud, current: CurrentUser, kernel: Kernel) -> None:
        self.crud, self.current, self.kernel = crud, current, kernel
        self.owner = crud.repository.principal_id
        if kernel.resolve(CurrentUser) is not current:
            msg = "The connected read owner service changed"
            raise HeinzelError(msg)

    @staticmethod
    def _window_parameter(capability: str, method: str) -> str | None:
        try:
            return rolling_window_parameter(capability, method)
        except HeinzelError:
            return None

    def options(self, session: str) -> list[ConnectedReadOption]:
        """List currently registered owner-selected read methods without opening a provider."""
        self._human()
        self.crud.repository.control_read(session)
        options: list[ConnectedReadOption] = []
        for capability, spec in CAPABILITIES.items():
            for method, mode in spec.methods.items():
                if mode != "read":
                    continue
                try:
                    self._check_current_config(capability)
                    self._selected(capability, method)
                    desc = describe_capability_read(self.kernel, capability, method)
                    options.append(
                        ConnectedReadOption(
                            capability=capability,
                            method=method,
                            provider=desc.adapter,
                            source=f"{desc.owner_module} ({desc.distribution} {desc.version})",
                            input_schema=desc.schema,
                            window_parameter=self._window_parameter(capability, method),
                        )
                    )
                except HeinzelError:
                    continue
        return options

    def state(self, session: str) -> ConnectedReadState:
        """Project recorded owner state without fetching account data or inventing provider freshness."""
        self._human()
        events = self.crud.repository.control_read(session).events
        proposed = [event for event in events if event.kind == "connected.proposed"]
        if not proposed:
            return ConnectedReadState(session_id=session, proposal=None, status="none", descriptor=None)
        proposal = self._body(session, proposed[-1])
        status: Literal["none", "proposed", "approved", "revoked", "stale"] = "proposed"
        if any(e.kind == "connected.revoked" and e.key == proposal.request_id for e in events):
            status = "revoked"
        elif any(
            e.kind == "connected.approved" and self._body(session, e).request_id == proposal.request_id for e in events
        ):
            try:
                self.approved(session)
                status = "approved"
            except HeinzelError:
                status = "stale"
        descriptor = None
        try:
            self._check_current_config(proposal.capability)
            desc = describe_capability_read(self.kernel, proposal.capability, proposal.method)
            self._selected(proposal.capability, proposal.method)
            if desc.binding_sha256 == proposal.binding_sha256:
                descriptor = ConnectedReadOption(
                    capability=proposal.capability,
                    method=proposal.method,
                    provider=desc.adapter,
                    source=f"{desc.owner_module} ({desc.distribution} {desc.version})",
                    input_schema=desc.schema,
                    window_parameter=self._window_parameter(proposal.capability, proposal.method),
                )
        except HeinzelError:
            pass
        if descriptor is None and status in {"proposed", "approved"}:
            status = "stale"
        return ConnectedReadState(session_id=session, proposal=proposal, status=status, descriptor=descriptor)

    def _human(self) -> None:
        context = self.current.context()
        if (
            context.authentication not in _HEADS
            or PRIVATE_WRITE not in context.permissions
            or context.principal != self.current.owner()
            or context.principal.id != self.owner
            or self.crud.repository.principal_id != self.owner
        ):
            msg = "The connected read review requires its authenticated owner"
            raise HeinzelError(msg)

    def _selected(self, capability: str, method: str) -> tuple[ModuleRecord, ConnectedReadService]:
        try:
            record, _provider = self.kernel.selected_capability_provider(capability)
            # Default boot binds source before resolving any provider service factory.
            source_guard = self.kernel.source_discovery
            service: ConnectedReadService
            if source_guard is not None:
                service = SourceGuardedConnectedService(
                    source_guard, source_guard.binding(self.kernel, capability, ConnectedReadService)
                )
            else:
                record, service = self.kernel.selected_capability_service(ConnectedReadService, capability)
            claims = service.supported_reads()
            if type(claims) is not tuple or len(claims) > _MAX_SUPPORTED_READS:
                raise ValueError  # noqa: TRY301
            seen: set[tuple[str, str]] = set()
            for item in claims:
                if type(item) is not ConnectedReadMethod:
                    raise ValueError  # noqa: TRY301
                claim = ConnectedReadMethod.model_validate(item.model_dump(mode="json", warnings=False))
                key = (claim.capability, claim.method)
                spec = CAPABILITIES.get(claim.capability)
                if key in seen or spec is None or spec.methods.get(claim.method) != "read":
                    raise ValueError  # noqa: TRY301
                capability_read_schema(claim.capability, claim.method)
                seen.add(key)
            if (capability, method) not in seen:
                raise ValueError  # noqa: TRY301
            selected, _provider = self.kernel.selected_capability_provider(capability)
            if selected is not record:
                raise ValueError  # noqa: TRY301
        except Exception:  # noqa: BLE001 - provider diagnostics and malformed claims are private
            msg = "The selected connected read service or method is unavailable"
            raise HeinzelError(msg) from None
        else:
            return record, service

    def _check_current_config(self, capability: str) -> None:
        """Reject a changed owner-selected binding; this does not refresh installed plugin discovery.

        Raises:
            HeinzelError: If the binding changed.
        """
        config = Config.load(Path.home())
        fresh = config.settings(CapabilitySettings, "capabilities")
        original = self.kernel.capability_settings
        if (
            fresh.bound(capability) != original.bound(capability)
            or fresh.configured(capability) != original.configured(capability)
            or fresh.defaulted(capability) != original.defaulted(capability)
            or config.settings(Settings, "hz").disabled_modules != self.kernel.settings.disabled_modules
        ):
            msg = "The connected read capability binding changed; reload it before continuing"
            raise HeinzelError(msg)

    def _observe(
        self, session: str, capability: str, method: str, fixed: dict[str, JsonValue]
    ) -> tuple[str, ConnectedReadReview]:
        self._check_current_config(capability)
        fixed = validate_capability_read_arguments(capability, method, fixed)
        fixed_hash = arguments_hash(fixed)
        descriptor = describe_capability_read(self.kernel, capability, method)
        record, service = self._selected(capability, method)
        if record.qualname != descriptor.owner_module:
            msg = "The selected connected read changed"
            raise HeinzelError(msg)
        lease = _open_lease(service, session, f"capability-method:{capability}.{method}", copy.deepcopy(fixed))
        try:
            review = _review_lease(lease)
            schema_digest = arguments_hash(descriptor.schema)
            if (
                review.owner != self.owner
                or review.session != session
                or review.operation != f"capability-method:{capability}.{method}"
                or review.schema_sha256 != schema_digest
                or arguments_hash(review.fixed_arguments) != fixed_hash
            ):
                msg = "The connected read source did not match its host review"
                raise HeinzelError(msg)
            return descriptor.binding_sha256, review.model_copy(deep=True)
        finally:
            _close_review_lease(lease)

    def _body(self, session: str, event: SessionEvent) -> ConnectedGrant:
        return _GRANT.validate_python(self.crud.read_body(session, event))

    def _intent(self, session: str, proposal: ConnectedGrant) -> ApprovalIntent:
        return ApprovalIntent(
            owner=self.owner,
            audience="personal-connected-read",
            session=session,
            action="approve-connected-read",
            resource=proposal.review.operation,
            arguments_hash=arguments_hash(proposal.model_dump(mode="json")),
            contract_revision=0,
            grant_revision=proposal.revision,
            policy_revision=0,
        )

    def propose(
        self, session: str, request: str, capability: str, method: str, fixed: dict[str, JsonValue]
    ) -> ConnectedGrantProposal:
        """Persist the exact owner-requested proposal, never execute the read.

        Raises:
            HeinzelError: If owner, source, or request identity is invalid.
        """
        self._human()
        _SESSION.validate_python(session)
        _REQUEST.validate_python(request)
        # Resolve the caller-owned session before any provider lease can export credentials or call an API.
        self.crud.repository.control_read(session)
        binding, review = self._observe(session, capability, method, fixed)
        with self.crud.repository.main_writer(session):
            return self._record_proposal(session, request, capability, method, binding, review, {})

    def _record_proposal(  # noqa: PLR0913, PLR0917 - exact reviewed terms
        self,
        session: str,
        request: str,
        capability: str,
        method: str,
        binding: str,
        review: ConnectedReadReview,
        provenance: dict[str, JsonValue],
    ) -> ConnectedGrantProposal:
        with self.crud.repository.control_writer(session) as writer:
            events = self.crud.repository.control_read(session).events
            existing = next((e for e in events if e.kind == "connected.proposed" and e.key == request), None)
            if existing:
                proposal = self._body(session, existing)
                candidate = ConnectedGrantProposal(
                    request_id=request,
                    revision=proposal.revision,
                    capability=capability,
                    method=method,
                    binding_sha256=binding,
                    review=review,
                )
                if arguments_hash(proposal.model_dump(mode="json")) != arguments_hash(
                    candidate.model_dump(mode="json")
                ):
                    msg = "This request identifies a different connected read"
                    raise HeinzelError(msg)
                return proposal
            revisions = [self._body(session, e).revision for e in events if e.kind == "connected.proposed"]
            proposal = ConnectedGrantProposal(
                request_id=request,
                revision=max(revisions, default=0) + 1,
                capability=capability,
                method=method,
                binding_sha256=binding,
                review=review,
            )
            body = immutable_body(writer, "connected-read-grant", proposal.model_dump(mode="json"), control=True)
            writer.append("connected.proposed", key=request, data={**body.model_dump(mode="json"), **provenance})
        return proposal

    def propose_suggestion(
        self, session: str, request: str, suggestion_request: str, suggestion_revision: int
    ) -> ConnectedGrantProposal:
        """Prepare only the current exact chat suggestion under the owner's main-writer fence.

        Raises:
            HeinzelError: If owner, suggestion, revision or prepare request is invalid.
        """
        self._human()
        _SESSION.validate_python(session)
        _REQUEST.validate_python(request)
        _REQUEST.validate_python(suggestion_request)
        if type(suggestion_revision) is not int or suggestion_revision < 1:
            msg = "The source suggestion revision is invalid"
            raise HeinzelError(msg)
        with self.crud.repository.main_writer(session):
            current = latest_source_suggestion(self.crud, session)
            if current is None or current.request_id != suggestion_request or current.revision != suggestion_revision:
                msg = "The source suggestion changed; review the latest suggestion"
                raise HeinzelError(msg)
            fixed = validate_capability_read_arguments(current.capability, current.method, current.fixed_arguments)
            events = self.crud.repository.control_read(session).events
            existing = [e for e in events if e.kind == "connected.proposed" and e.key == request]
            if existing:
                if (
                    len(existing) != 1
                    or existing[0].data.get("suggestion_request") != suggestion_request
                    or existing[0].data.get("suggestion_revision") != suggestion_revision
                ):
                    msg = "This request identifies a different connected read"
                    raise HeinzelError(msg)
                proposal = self._body(session, existing[0])
                if (
                    proposal.capability != current.capability
                    or proposal.method != current.method
                    or arguments_hash(proposal.review.fixed_arguments) != arguments_hash(fixed)
                ):
                    msg = "This request identifies a different connected read"
                    raise HeinzelError(msg)
                return proposal
            binding, review = self._observe(session, current.capability, current.method, fixed)
            return self._record_proposal(
                session,
                request,
                current.capability,
                current.method,
                binding,
                review,
                {"suggestion_request": suggestion_request, "suggestion_revision": suggestion_revision},
            )

    def _rolling_arguments(
        self, capability: str, method: str, fixed: dict[str, JsonValue], policy: RollingWindowPolicy
    ) -> dict[str, JsonValue]:
        parameter = rolling_window_parameter(capability, method)
        if parameter != policy.parameter or parameter in fixed:
            msg = "The rolling window must replace the unique Window argument"
            raise HeinzelError(msg)
        now = self.crud.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            msg = "The host clock must be timezone aware"
            raise HeinzelError(msg)
        now = now.astimezone(UTC)
        start = (now - timedelta(seconds=policy.lookback_seconds)).isoformat().replace("+00:00", "Z")
        end = (now + timedelta(seconds=policy.lookahead_seconds)).isoformat().replace("+00:00", "Z")
        return {**copy.deepcopy(fixed), parameter: {"since": start, "until": end}}

    def _arguments(self, grant: ConnectedGrant) -> dict[str, JsonValue]:
        fixed = copy.deepcopy(grant.review.fixed_arguments)
        if isinstance(grant, RollingConnectedGrantProposal):
            if grant.window_policy.parameter not in fixed:
                msg = "The saved rolling window is invalid"
                raise HeinzelError(msg)
            del fixed[grant.window_policy.parameter]
            return self._rolling_arguments(grant.capability, grant.method, fixed, grant.window_policy)
        return fixed

    def _same_review(self, grant: ConnectedGrant, review: ConnectedReadReview) -> bool:
        observed = review.model_dump(mode="json")
        expected = grant.review.model_dump(mode="json")
        if isinstance(grant, RollingConnectedGrantProposal):
            # _observe already compared the derived full arguments exactly before this substitution.
            observed["fixed_arguments"] = expected["fixed_arguments"]
        return arguments_hash(observed) == arguments_hash(expected)

    def observe_set_candidate(
        self, session: str, capability: str, method: str, fixed: dict[str, JsonValue]
    ) -> tuple[str, ConnectedReadReview]:
        """Observe one owner-selected fixed read without creating a legacy proposal or granting access.

        Raises:
            HeinzelError: If the owner, session, selected source or arguments cannot be verified.
        """
        self._human()
        _SESSION.validate_python(session)
        self.crud.repository.control_read(session)
        return self._observe(session, capability, method, fixed)

    def verify_proposed_terms(self, session: str, grant: ConnectedGrant) -> None:
        """Re-observe proposed terms; this issues no approval and never invokes a read.

        Raises:
            HeinzelError: If the selected source, account or proposed terms differ.
        """
        self._human()
        _SESSION.validate_python(session)
        self.crud.repository.control_read(session)
        binding, review = self._observe(session, grant.capability, grant.method, self._arguments(grant))
        if binding != grant.binding_sha256 or not self._same_review(grant, review):
            msg = "The proposed read-set source or account changed; prepare it again"
            raise HeinzelError(msg)

    def propose_rolling(  # noqa: PLR0913, PLR0917 - exact reviewed terms
        self,
        session: str,
        request: str,
        capability: str,
        method: str,
        fixed: dict[str, JsonValue],
        policy: RollingWindowPolicy,
    ) -> RollingConnectedGrantProposal:
        """Stage a separately reviewed bounded rolling window; never approve or read data.

        Raises:
            HeinzelError: If owner, policy, source or stable request identity cannot be verified.
        """
        self._human()
        _SESSION.validate_python(session)
        _REQUEST.validate_python(request)
        self.crud.repository.control_read(session)
        args = self._rolling_arguments(capability, method, fixed, policy)
        binding, review = self._observe(session, capability, method, args)
        with self.crud.repository.main_writer(session), self.crud.repository.control_writer(session) as writer:
            events = self.crud.repository.control_read(session).events
            existing = [event for event in events if event.kind == "connected.proposed" and event.key == request]
            if existing:
                if len(existing) != 1:
                    msg = "This rolling request is inconsistent"
                    raise HeinzelError(msg)
                previous = self._body(session, existing[0])
                if (
                    not isinstance(previous, RollingConnectedGrantProposal)
                    or previous.capability != capability
                    or previous.method != method
                    or previous.binding_sha256 != binding
                    or arguments_hash(previous.window_policy.model_dump(mode="json"))
                    != arguments_hash(policy.model_dump(mode="json"))
                    or not self._same_review(previous, review)
                ):
                    msg = "This request identifies different rolling read terms"
                    raise HeinzelError(msg)
                old_fixed = copy.deepcopy(previous.review.fixed_arguments)
                del old_fixed[policy.parameter]
                if arguments_hash(old_fixed) != arguments_hash(fixed):
                    msg = "This request identifies different rolling read arguments"
                    raise HeinzelError(msg)
                return previous
            revisions = [self._body(session, event).revision for event in events if event.kind == "connected.proposed"]
            proposal = RollingConnectedGrantProposal(
                request_id=request,
                revision=max(revisions, default=0) + 1,
                capability=capability,
                method=method,
                binding_sha256=binding,
                review=review,
                kind="rolling-window",
                window_policy=policy,
            )
            body = immutable_body(writer, "connected-read-grant", proposal.model_dump(mode="json"), control=True)
            writer.append("connected.proposed", key=request, data=body.model_dump(mode="json"))
            return proposal

    def _proposal(self, session: str, request: str) -> ConnectedGrant:
        events = self.crud.repository.control_read(session).events
        proposals = [e for e in events if e.kind == "connected.proposed"]
        matches = [e for e in proposals if e.key == request]
        if len(matches) != 1 or not proposals or proposals[-1] != matches[0]:
            msg = "The connected read proposal changed; review it again"
            raise HeinzelError(msg)
        proposal = self._body(session, matches[0])
        binding, review = self._observe(session, proposal.capability, proposal.method, self._arguments(proposal))
        if binding != proposal.binding_sha256 or not self._same_review(proposal, review):
            msg = "The connected read source or account changed; review it again"
            raise HeinzelError(msg)
        return proposal

    def issue(self, session: str, request: str, provenance: str) -> str:
        """Issue internal proof only after trusted human confirmation of the exact current review."""
        self._human()
        proof = secrets.token_urlsafe(32)
        digest = approval_proof_hash(proof)
        with self.crud.repository.control_writer(session) as writer:
            proposal = self._proposal(session, request)
            now = self.crud.clock()
            record = ApprovalRecord(
                intent=self._intent(session, proposal),
                proof_hash=digest,
                nonce=secrets.token_hex(32),
                provenance=provenance[:128],
                decision="approve",
                issued_at=now,
                expires_at=now + timedelta(minutes=5),
            )
            writer.append("approval.issued", key=digest, data=record.model_dump(mode="json"))
        return proof

    def review(self, session: str, request: str) -> ConnectedGrant:
        """Reobserve the exact current proposal for a trusted human review."""
        self._human()
        return self._proposal(session, request)

    def approve(
        self, session: str, request: str, review: dict[str, JsonValue], proof: str, confirmation: ApprovalConfirmation
    ) -> ConnectedGrant:
        """Consume exact human-confirmed evidence without running a provider read.

        Raises:
            HeinzelError: If no trusted confirmation covers the exact reviewed intent.
        """
        self._human()
        args: dict[str, JsonValue] = {
            "session_id": session,
            "request_id": request,
            "review": review,
            "approval_proof": proof,
        }
        if not confirmation.matches("heinzel", "approve-connected-read", args):
            msg = "Approval is invalid or unavailable"
            raise HeinzelError(msg)
        proposal = self._proposal(session, request)
        if arguments_hash(proposal.model_dump(mode="json")) != arguments_hash(review):
            msg = "The reviewed connected read changed"
            raise HeinzelError(msg)

        def current(events: list[SessionEvent]) -> ApprovalIntent:
            if any(
                event.kind == "connected.approved" and self._body(session, event).request_id == request
                for event in events
            ):
                msg = "This connected read has already been approved"
                raise HeinzelError(msg)
            return self._intent(session, self._proposal(session, request))

        def effect(
            writer: SessionWriter, _events: list[SessionEvent], _evidence: ApprovalEvidence
        ) -> dict[str, JsonValue]:
            body = immutable_body(writer, "approved-connected-read", proposal.model_dump(mode="json"), control=True)
            return body.model_dump(mode="json")

        with self.crud.repository.main_writer(session):
            consume_approval(
                self.crud.repository,
                session,
                proof,
                self._intent(session, proposal),
                current,
                self.crud.clock,
                event_kind="connected.approved",
                effect_data=effect,
            )
        return proposal

    def approved(self, session: str) -> ConnectedGrant:
        """Project only the latest unrevoked grant with exact trusted issuance evidence.

        Raises:
            HeinzelError: If no current grant exists or its saved evidence is inconsistent.
        """
        events = self.crud.repository.control_read(session).events
        approvals = [e for e in events if e.kind == "connected.approved"]
        if not approvals:
            msg = "No connected read has been approved"
            raise HeinzelError(msg)
        event = approvals[-1]
        grant = self._body(session, event)
        proposed = [e for e in events if e.kind == "connected.proposed" and e.key == grant.request_id]
        issued = [e for e in events if e.kind == "approval.issued" and e.key == event.data.get("proof_hash")]
        if len(proposed) != 1 or len(issued) != 1 or not proposed[0].seq < issued[0].seq < event.seq:
            msg = "The connected read approval evidence is invalid"
            raise HeinzelError(msg)
        try:
            record = ApprovalRecord.model_validate_json(json.dumps(issued[0].data))
        except ValueError:
            msg = "The connected read approval evidence is invalid"
            raise HeinzelError(msg) from None
        latest = [e for e in events if e.kind == "connected.proposed"]
        if (
            arguments_hash(self._body(session, proposed[0]).model_dump(mode="json"))
            != arguments_hash(grant.model_dump(mode="json"))
            or not latest
            or latest[-1].key != grant.request_id
            or record.model_dump(mode="json").get("decision") != "approve"
            or not record.issued_at <= event.ts < record.expires_at
            or issued[0].ts > event.ts
            or record.intent != self._intent(session, grant)
            or record.proof_hash != issued[0].key
            or record.nonce != event.data.get("consumed_nonce")
            or record.provenance != event.data.get("provenance")
            or record.intent.model_dump(mode="json") != event.data.get("intent")
            or sum(e.data.get("consumed_nonce") == record.nonce for e in events) != 1
            or any(e.kind == "approval.revoked" and e.key == record.nonce for e in events)
            or any(e.kind == "connected.revoked" and e.key == grant.request_id for e in events)
        ):
            msg = "The connected read approval evidence is invalid or revoked"
            raise HeinzelError(msg)
        return grant

    def verify_approved(self, session: str, expected: ConnectedGrant) -> None:
        """Recheck the exact current grant and selected account before an authorized host launch.

        Raises:
            HeinzelError: If the grant, provider, account, or reviewed terms changed.
        """
        grant = self.approved(session)
        if arguments_hash(grant.model_dump(mode="json")) != arguments_hash(expected.model_dump(mode="json")):
            msg = "The connected read grant changed"
            raise HeinzelError(msg)
        binding, review = self._observe(session, grant.capability, grant.method, self._arguments(grant))
        if binding != grant.binding_sha256 or not self._same_review(grant, review):
            msg = "The connected read source or account changed"
            raise HeinzelError(msg)

    def tool_schema(self, session: str) -> dict[str, JsonValue] | None:
        """Expose only a currently valid grant; an unavailable read does not prevent ordinary chat."""
        events = self.crud.repository.control_read(session).events
        if not any(event.kind == "connected.approved" for event in events):
            return None
        try:
            grant = self.approved(session)
            self._check_current_config(grant.capability)
            descriptor = describe_capability_read(self.kernel, grant.capability, grant.method)
            self._selected(grant.capability, grant.method)
            if descriptor.binding_sha256 != grant.binding_sha256:
                return None
        except HeinzelError:
            return None
        return {
            "name": "connected_read",
            "description": f"Read the approved {grant.capability}.{grant.method} with host-controlled arguments.",
            "schema": {"type": "object", "properties": {}, "additionalProperties": False},
        }

    def revoke(self, session: str, request: str) -> None:
        """Revoke an exact active grant under the same writer used by turns.

        Raises:
            HeinzelError: If the owner or current grant does not match.
        """
        self._human()
        with self.crud.repository.main_writer(session), self.crud.repository.control_writer(session) as writer:
            if self.approved(session).request_id != request:
                msg = "The connected read grant changed"
                raise HeinzelError(msg)
            writer.append("connected.revoked", key=request, data={})

    def read(self, session: str, turn_writer: SessionWriter) -> JsonValue:
        """Read through a fresh exact matching lease while holding the owning turn's main writer.

        Raises:
            HeinzelError: If owner, current approval, source, account or constraints cannot be verified.
            ConnectedReadFailedError: If an invoked read or cleanup does not finish successfully.
            ValueError: Invalid result data is converted to a safe failure.
        """
        context = self.current.context()
        if (
            context.authentication not in _HEADS | {"daemon"}
            or context.principal != self.current.owner()
            or context.principal.id != self.owner
            or self.crud.repository.principal_id != self.owner
            or _READ_PERMISSION not in context.permissions
            or not self.crud.repository.is_main_writer(session, turn_writer)
        ):
            msg = "The connected read does not belong to this authorized turn"
            raise HeinzelError(msg)
        grant = self.approved(session)
        self._check_current_config(grant.capability)
        descriptor = describe_capability_read(self.kernel, grant.capability, grant.method)
        record, service = self._selected(grant.capability, grant.method)
        if descriptor.binding_sha256 != grant.binding_sha256 or descriptor.owner_module != record.qualname:
            msg = "The connected read provider changed"
            raise HeinzelError(msg)
        fixed = self._arguments(grant)
        fixed_hash = arguments_hash(fixed)
        lease = _open_lease(service, session, grant.review.operation, copy.deepcopy(fixed))
        invoked = False
        try:
            live_review = _review_lease(lease)
            if (
                arguments_hash(live_review.fixed_arguments) != fixed_hash
                or not self._same_review(grant, live_review)
                or arguments_hash(self.approved(session).model_dump(mode="json"))
                != arguments_hash(grant.model_dump(mode="json"))
            ):
                msg = "The connected read approval or account changed"
                raise HeinzelError(msg)
            self._check_current_config(grant.capability)
            try:
                invoked = True
                result = TypeAdapter(JsonValue).validate_python(lease.read({}), strict=True)
                if len(json.dumps(result, ensure_ascii=True, allow_nan=False).encode()) > _MAX_RESULT_BYTES:
                    raise ValueError  # noqa: TRY301
            except Exception:  # noqa: BLE001 - never return provider or input diagnostics
                msg = "The connected read began but did not produce a usable result"
                raise ConnectedReadFailedError(msg) from None
            self._verify_read_lease(lease, grant, fixed_hash)
        finally:
            try:
                lease.close()
            except Exception:  # noqa: BLE001 - cleanup diagnostics may contain private material
                msg = "The connected read cleanup could not be confirmed"
                if invoked:
                    raise ConnectedReadFailedError(msg) from None
                raise HeinzelError(msg) from None
        self._verify_after_read(session, turn_writer, grant)
        return result

    def _verify_read_lease(self, lease: ConnectedReadLease, grant: ConnectedGrant, fixed_hash: str) -> None:
        try:
            live_after = _review_lease(lease)
            if arguments_hash(live_after.fixed_arguments) != fixed_hash or not self._same_review(grant, live_after):
                raise ValueError  # noqa: TRY301 - safe failure below
        except Exception:  # noqa: BLE001 - read has occurred; discard provider diagnostics
            msg = "The connected read completed but its source or account could not be confirmed"
            raise ConnectedReadFailedError(msg) from None

    def _verify_after_read(self, session: str, turn_writer: SessionWriter, grant: ConnectedGrant) -> None:
        try:
            current = self.current.context()
            if (
                current.authentication not in _HEADS | {"daemon"}
                or current.principal != self.current.owner()
                or current.principal.id != self.owner
                or _READ_PERMISSION not in current.permissions
                or not self.crud.repository.is_main_writer(session, turn_writer)
            ):
                raise ValueError  # noqa: TRY301 - safe failure below
            self.verify_approved(session, grant)
            if arguments_hash(self.approved(session).model_dump(mode="json")) != arguments_hash(
                grant.model_dump(mode="json")
            ):
                raise ValueError  # noqa: TRY301 - safe failure below
        except Exception:  # noqa: BLE001 - never expose the value if its authority changed
            msg = "The connected read completed but its authority could not be revalidated"
            raise ConnectedReadFailedError(msg) from None

    def read_set_entry(
        self, session: str, writer: SessionWriter, call: "ReadSetCall", approvals: "ConnectedReadSetApprovals"
    ) -> tuple[JsonValue, int]:
        """Read one approved entry in a human-owned turn and record it before returning data.

        This internal path registers no tool and does not authorize daemon or notification use.

        Raises:
            HeinzelError: If the turn, set, selected account or source is unavailable.
            ConnectedReadFailedError: If a read, cleanup or durable receipt fails.
        """
        from heinzel.modules.heinzel._connected_read_set_inputs import (  # noqa: PLC0415 - avoid approval import cycle
            ReadSetReceiptBinding,
            record_set_input,
            set_input_key,
            start_set_call,
            verify_set_call,
        )

        context = self.current.context()
        if (
            approvals.reviews.grants is not self
            or approvals.crud is not self.crud
            or context.authentication not in _HEADS
            or context.principal != self.current.owner()
            or context.principal.id != self.owner
            or _READ_PERMISSION not in context.permissions
            or not self.crud.repository.is_main_writer(session, writer)
        ):
            msg = "The read-set input does not belong to this authorized turn"
            raise HeinzelError(msg)
        call = verify_set_call(self.crud, session, call)
        expected = approvals.approved(session)
        matches = [entry for entry in expected.proposal.entries if entry.handle == call.handle]
        if len(matches) != 1:
            msg = "The read-set entry is unavailable"
            raise HeinzelError(msg)
        grant = approvals.reviews.entry_grant(matches[0])
        key, key_id = set_input_key(self.current, self.crud, session)
        start_set_call(self.crud, session, call)
        self._check_current_config(grant.capability)
        descriptor = describe_capability_read(self.kernel, grant.capability, grant.method)
        record, service = self._selected(grant.capability, grant.method)
        if descriptor.binding_sha256 != grant.binding_sha256 or descriptor.owner_module != record.qualname:
            msg = "The connected read provider changed"
            raise HeinzelError(msg)
        fixed = self._arguments(grant)
        lease = _open_lease(service, session, grant.review.operation, copy.deepcopy(fixed))
        invoked = False
        try:
            live = _review_lease(lease)
            if (
                arguments_hash(live.fixed_arguments) != arguments_hash(fixed)
                or not self._same_review(grant, live)
                or arguments_hash(approvals.approved(session).model_dump(mode="json"))
                != arguments_hash(expected.model_dump(mode="json"))
            ):
                msg = "The read-set approval or account changed"
                raise HeinzelError(msg)
            self._check_current_config(grant.capability)
            invoked = True
            result = _bounded_set_read(lease)
            self._verify_read_lease(lease, grant, arguments_hash(fixed))
        finally:
            try:
                lease.close()
            except Exception:  # noqa: BLE001 - cleanup diagnostics may contain private material
                msg = "The connected read cleanup could not be confirmed"
                if invoked:
                    raise ConnectedReadFailedError(msg) from None
                raise HeinzelError(msg) from None
        try:
            self.verify_proposed_terms(session, grant)
            binding = ReadSetReceiptBinding(call, expected, key, key_id)
            sequence = record_set_input(approvals, session, binding, result)
        except Exception:  # noqa: BLE001 - never expose a value whose receipt could not be recorded
            msg = "The connected read completed but its input snapshot could not be recorded"
            raise ConnectedReadFailedError(msg) from None
        return result, sequence
