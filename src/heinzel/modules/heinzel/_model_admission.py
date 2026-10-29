"""Fail-closed admission checks for a connected AI model before a durable personal turn.

This internal preflight cannot issue authority: AiAccess has no owner-bound model factory or hard spending contract.
No configured provider is admitted until those capabilities are implemented and verified.
"""

from decimal import Decimal
from typing import Never

from heinzel import HeinzelError
from heinzel.kernel._ai import AiAccess
from heinzel.modules.heinzel._session_protocol import SessionRepository


def require_connected_model(  # noqa: PLR0913 - trusted caller pins each distinct authority dimension
    repository: SessionRepository,
    access: AiAccess,
    *,
    session_id: str,
    owner: str,
    selected_access: str | None,
    approved_access: str,
    approved_model: str,
    budget_usd: Decimal | None,
) -> Never:
    """Check a proposed binding and refuse execution until a hard spend ceiling can be guaranteed.

    This is not a grant issuer and its inputs must come from trusted persisted policy, not the model or a client.
    The access status is offline and never sends a request. `status.models` is discovery information, not proof
    that a model has been approved or a credential belongs to this owner.

    Raises:
        HeinzelError: Always, including when owner, configured access, credentials, model or cost policy is invalid.
    """
    if repository.principal_id != owner:
        msg = "The model binding belongs to a different authenticated owner"
        raise HeinzelError(msg)
    repository.control_read(session_id)
    if not selected_access or selected_access != approved_access:
        msg = "The selected AI access does not match the approved model binding"
        raise HeinzelError(msg)
    status = access.status()
    if not status.installed or status.signed_in is not True:
        msg = "The selected AI access or its credential is unavailable"
        raise HeinzelError(msg)
    if not approved_model or approved_model not in status.models:
        msg = "The approved model is unavailable from the selected AI access"
        raise HeinzelError(msg)
    if budget_usd is None or not budget_usd.is_finite() or budget_usd <= 0:
        msg = "The turn needs an explicit finite spending limit"
        raise HeinzelError(msg)
    # The current AiAccess interface supplies neither credential ownership nor a provider-enforced
    # pre-request spend ceiling. Post-response accounting cannot stop the first call exceeding the budget.
    msg = "The connected model cannot prove owner-bound credentials and a pre-execution spending ceiling"
    raise HeinzelError(msg)
