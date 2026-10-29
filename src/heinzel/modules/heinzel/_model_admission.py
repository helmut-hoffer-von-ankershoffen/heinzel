"""Internal model admission for bounded, owner-bound direct-API research turns.

This is not registered as a product operation or a human approval boundary. Callers must supply trusted binding
state. Dollar-capped policies are refused until a hard pre-request cost ceiling can be enforced.
"""

from typing import Any, Protocol

from pydantic_ai.models import Model

from heinzel import CurrentUser, HeinzelError
from heinzel.modules.heinzel._engine import TurnLimits
from heinzel.modules.heinzel._session_protocol import SessionRepository


class ConnectedModelFactory(Protocol):
    """An installed model provider owns credential resolution and constructs its model for the current owner."""

    def turn_model(self, current: CurrentUser, expected_model: str) -> Any:  # noqa: ANN401 - provider-specific model
        """Build the exactly configured model after checking its credential and authenticated owner."""
        ...


def connected_model(  # noqa: PLR0913 - each policy dimension must be checked separately
    repository: SessionRepository,
    current: CurrentUser,
    factory: ConnectedModelFactory,
    *,
    session_id: str,
    owner: str,
    selected_access: str | None,
    approved_access: str,
    approved_model: str,
    limits: TurnLimits,
) -> Model:
    """Resolve a model for a request/token/time-bounded turn; never represent token limits as a dollar cap.

    Binding and grant values must be trusted, independently persisted authority, not client or model input. The
    returned model is for internal integration only. The current product session also advertises dollar caps, so
    a real product path cannot use this until it enforces those caps and associated cumulative session accounting.

    Raises:
        HeinzelError: If ownership, binding, limits or the provider's model are unavailable.
    """
    if repository.principal_id != owner or current.principal().id != owner:
        msg = "The model binding belongs to a different authenticated owner"
        raise HeinzelError(msg)
    repository.control_read(session_id)
    if not selected_access or selected_access != approved_access or selected_access != "pydantic-ai":
        msg = "The selected AI access does not match the approved model binding"
        raise HeinzelError(msg)
    if limits.cost_usd is not None:
        msg = "This model cannot enforce the requested hard dollar cap before execution"
        raise HeinzelError(msg)
    model = factory.turn_model(current, approved_model)
    if not isinstance(model, Model):
        msg = "The configured model factory returned an unsupported model"
        raise HeinzelError(msg)
    return model
