"""Remove a selected managed runtime through the existing unleased authority, retaining profile work."""

from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt

from heinzel import HeinzelError
from heinzel.modules.system.lifecycle._models import Activation, Digest, Record, Transition
from heinzel.modules.system.lifecycle._store import LifecycleStore


class RuntimeUninstall(Record):
    """Observed runtime removal outcome; this never asserts that an app, OS service or uv tool was removed."""

    state: Literal["uninstalled", "blocked", "recovery-required", "cleanup-required"]
    transition: UUID
    generation: StrictInt = Field(ge=1)
    deployment: Digest
    data_retained: Literal[True] = True
    payloads: Literal["retained", "removed", "cleanup-required"] = "retained"
    deployments_removed: StrictInt | None = Field(default=None, ge=0)
    environments_removed: StrictInt | None = Field(default=None, ge=0)


def _failed_transition(store: LifecycleStore, before: Activation, plan: Transition) -> RuntimeUninstall:
    outcome: Literal["blocked", "recovery-required"] = "recovery-required"
    try:
        pending = store.pending()
        if pending and pending.id == plan.id and pending.phase == "prepared" and store.active() == before:
            store.cancel(plan.id)
            outcome = "blocked"
    except (OSError, HeinzelError):
        # An uncertain durable boundary stays visible; never erase another plan or invent a completed rollback.
        pass
    return RuntimeUninstall(state=outcome, transition=plan.id, generation=plan.generation, deployment=before.deployment)


def _selected_plan(store: LifecycleStore, expected: Activation, identifier: UUID | None) -> Transition:
    if identifier is None:
        return store.prepare(None)
    plan = store.pending()
    if (
        not plan
        or plan.id != identifier
        or plan.phase != "prepared"
        or plan.action != "uninstall"
        or plan.candidate is not None
        or plan.before != expected
    ):
        msg = "The selected uninstall plan is stale or belongs to another action; inspect it before proceeding."
        raise HeinzelError(msg)
    return plan


def uninstall_runtime(
    store: LifecycleStore, expected: Activation, *, identifier: UUID | None = None, remove_payloads: bool = False
) -> RuntimeUninstall:
    """Deactivate the exact selected runtime and optionally clean only verified payloads, under one held authority.

    Call only from the existing private control plane after the caller's normal action confirmation. The caller
    holds the lifecycle lock; this function never acquires an ordinary command lease or starts another scheduler.
    An exact prepared uninstall ID lets the native coordinator prepare before draining and finish the same plan.
    Writers and backend ownership must already be drained: a refusal preserves the active runtime. After a pointer
    change, uncertain completion remains recoverable rather than being called a rollback. Payload cleanup can be
    retried through the store's durable cleanup journal. Profile data, credentials, app files and OS services stay.

    Raises:
        HeinzelError: If exclusive authority, the exact expected activation or a settled starting state is absent.
        OSError: If the initial transition journal cannot be durably prepared.
    """
    if store.active() != expected:
        msg = "The selected runtime changed; inspect its current generation before uninstalling."
        raise HeinzelError(msg)
    # prepare enforces the already held authority; a second lock here would deadlock the control plane.
    plan = _selected_plan(store, expected, identifier)
    try:
        store.switch(plan.id)
        store.complete(plan.id)
    except (OSError, HeinzelError):
        return _failed_transition(store, expected, plan)
    if remove_payloads:
        try:
            deployments, environments = store.purge_uninstalled_runtime()
        except (OSError, ValueError, HeinzelError):
            return RuntimeUninstall(
                state="cleanup-required",
                transition=plan.id,
                generation=plan.generation,
                deployment=expected.deployment,
                payloads="cleanup-required",
            )
        return RuntimeUninstall(
            state="uninstalled",
            transition=plan.id,
            generation=plan.generation,
            deployment=expected.deployment,
            payloads="removed",
            deployments_removed=deployments,
            environments_removed=environments,
        )
    return RuntimeUninstall(
        state="uninstalled", transition=plan.id, generation=plan.generation, deployment=expected.deployment
    )
