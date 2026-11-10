"""Authenticated status and preparation. Only an independent local reviewer can apply a plan."""

import secrets
import time
from collections import OrderedDict
from contextlib import suppress
from threading import Lock
from typing import Annotated

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel

from heinzel.kernel import HeinzelError, Kernel, McpLifecycleAction, McpLifecycleObservation, McpLifecyclePlanning
from heinzel.kernel._logging import get_logger, step
from heinzel.kernel._mcp_handoff import MCP_HANDOFF_SECONDS, McpReviewSession, mcp_handoff_deadline

_MAX_PLANS = 32
_REFUSED = "The exact MCP plan is unavailable or changed. Prepare it again on the host."
log = get_logger("mcp")

type Registration = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,96}$")]
type PlanId = Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")]


class McpHandoffPlan(BaseModel, frozen=True):
    """A comparison commitment for the CLI. No approval or installed binding data is returned."""

    id: str
    action: McpLifecycleAction
    registration: str
    session: McpReviewSession
    handoff: str


class McpLifecycleAvailability(BaseModel):
    """Whether an installed host composed a planning service with this server."""

    planning_available: bool


def _prepare(
    kernel: Kernel, action: McpLifecycleAction, registration: str, *, deadline: int
) -> tuple[str, McpReviewSession]:
    if not kernel.provides(McpLifecyclePlanning):
        raise HeinzelError(_REFUSED)
    handoff, session = kernel.resolve(McpLifecyclePlanning).prepare(action, registration, deadline)
    if mcp_handoff_deadline(handoff) != deadline:
        raise HeinzelError(_REFUSED)
    return handoff, session


class _Handoffs:
    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel
        self.plans: OrderedDict[str, McpHandoffPlan] = OrderedDict()
        self.lock = Lock()

    def prepare(self, registration: Registration, action: McpLifecycleAction) -> McpHandoffPlan:
        """Prepare a plan for local review without approving or applying it.

        Raises:
            HTTPException: If the host cannot prepare the selected action.
        """
        try:
            with step(log, "mcp.ui.prepare_plan", operation="ui", step="prepare_plan", action=action):
                deadline = int(time.time()) + MCP_HANDOFF_SECONDS
                handoff, session = _prepare(self.kernel, action, registration, deadline=deadline)
                plan = McpHandoffPlan(
                    id=secrets.token_hex(16), action=action, registration=registration, session=session, handoff=handoff
                )
                with self.lock:
                    self.plans[plan.id] = plan
                    if len(self.plans) > _MAX_PLANS:
                        self.plans.popitem(last=False)
                return plan
        except (HeinzelError, OSError, ValueError):
            raise HTTPException(status_code=409, detail=_REFUSED) from None

    def resolve(self, id_: PlanId) -> McpHandoffPlan:
        """Look up a prepared plan by locator and verify it still describes the current host plan.

        Raises:
            HTTPException: If the plan is missing, expired or no longer describes the installed host.
        """
        with self.lock:
            plan = self.plans.get(id_)
        if plan is not None and self.kernel.provides(McpLifecyclePlanning):
            try:
                self.kernel.resolve(McpLifecyclePlanning).require_current(
                    plan.action, plan.registration, plan.handoff, plan.session
                )
            except (HeinzelError, OSError, ValueError):
                raise HTTPException(status_code=409, detail=_REFUSED) from None
            return plan
        raise HTTPException(status_code=409, detail=_REFUSED)


def add_mcp_lifecycle(app: FastAPI, kernel: Kernel) -> None:
    """Publish only observations and bounded handoffs under the web head's existing bearer middleware."""
    handoffs = _Handoffs(kernel)

    @app.get("/mcp/lifecycle", tags=["web"], operation_id="web_mcp_availability")
    def availability() -> McpLifecycleAvailability:
        """Read whether this UI server has a host lifecycle planner."""
        return McpLifecycleAvailability(planning_available=kernel.provides(McpLifecyclePlanning))

    @app.get("/mcp/lifecycle/{registration}", tags=["web"], operation_id="web_mcp_lifecycle")
    def status(registration: Registration) -> McpLifecycleObservation:
        """Read worker, manager, transport and readiness as separate observations."""
        if kernel.provides(McpLifecyclePlanning):
            with suppress(HeinzelError, OSError, ValueError):
                return kernel.resolve(McpLifecyclePlanning).status(registration)
        return McpLifecycleObservation()

    app.post("/mcp/lifecycle/{registration}/prepare", tags=["web"], operation_id="web_mcp_prepare")(handoffs.prepare)
    app.get("/mcp/handoffs/{id_}", tags=["web"], operation_id="web_mcp_handoff")(handoffs.resolve)
