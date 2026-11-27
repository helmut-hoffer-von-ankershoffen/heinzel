"""Restore an interrupted installation from the sealed supervisor runtime."""

import sys
from datetime import UTC, datetime
from pathlib import Path

from heinzel import HeinzelError, Progress
from heinzel.kernel import Installation, host_maintenance_lock, installation_mutation_paths, private_lock
from heinzel.modules.system.daemon._abandoned_mutation import (
    Evidence,
    host_boot,
    owner_ended,
    parse_marker,
    recover_abandoned_mutation,
)
from heinzel.modules.system.daemon._recovery_files import path_absent, recovery_file
from heinzel.modules.system.updater._announced import ToolSnapshot
from heinzel.modules.system.updater._recovery_staging import retire_staged_recovery


def _restore(installation: Installation, state: Path, content: str) -> None:
    daemon = state / "daemon"
    snapshot, mutation = state / "auto-update/before-update", daemon / "mutation-pending.json"
    blocked = {*installation_mutation_paths(installation.prefix, state), daemon / "handover-pending.json"}
    blocked.difference_update((snapshot, mutation, installation.prefix / ".heinzel-startup-pending"))
    if any(not path_absent(path) for path in blocked):
        msg = "Another lifecycle operation is pending."
        raise HeinzelError(msg)
    marker = recovery_file(mutation)
    owner = parse_marker(marker.content)
    evidence = Evidence(*host_boot())
    if owner is None or owner.pid is None or owner_ended(owner, marker, evidence) is None:
        msg = "The automatic updater's owner has not provably ended."
        raise HeinzelError(msg)
    if not path_absent(snapshot):
        descriptor = ToolSnapshot(installation.prefix, snapshot, snapshot.with_name("after-update"))
        if descriptor.recovery_content() != content:
            msg = "The worker does not match the interrupted update's verified snapshot."
            raise HeinzelError(msg)
    ToolSnapshot.recover_interrupted(installation, state / "auto-update")
    if not installation.repaired_startup_state_matches():
        msg = "The installation's continuity could not be recovered."
        raise HeinzelError(msg)
    if recover_abandoned_mutation(state, installation, lambda: evidence, lambda: datetime.now(UTC)) is None:
        msg = "The interrupted automatic update could not be archived."
        raise HeinzelError(msg)


def main() -> int:
    """Recover using the same writers as the web scheduler, installer and daemon."""
    prefix, state = map(Path, sys.argv[1:3])
    content = sys.argv[3]
    installation = Installation(Progress(), prefix=prefix, uv="unused")
    daemon = state / "daemon"
    try:
        with (
            private_lock(state / "web" / "auto-update-apply.lock"),
            private_lock(state / "update.lock"),
            private_lock(host_maintenance_lock(prefix)),
            private_lock(daemon / "lifecycle.lock"),
            private_lock(daemon / "tick.lock"),
        ):
            _restore(installation, state, content)
            retire_staged_recovery(state)
    except (OSError, ValueError, HeinzelError):
        return 75
    return 0
