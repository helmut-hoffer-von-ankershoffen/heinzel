"""Durable mutation gates sampled before a supervised cold start or restart (ADR 0117)."""

from pathlib import Path

from heinzel.kernel._host_maintenance import host_maintenance_gate


def installation_mutation_paths(prefix: Path, state: Path) -> tuple[Path, ...]:
    """Return unresolved package-mutation gates, excluding ordinary verified daemon handover requests."""
    return (
        host_maintenance_gate(prefix),
        state / "auto-update/before-update",
        state / "daemon/mutation-pending.json",
        state / "daemon/standard-install-pending.json",
        prefix / ".heinzel-startup-pending",
    )


def installation_mutation_pending(prefix: Path, state: Path) -> bool:
    """Refuse an observed pending entry, dangling link or unreadable gate; this sample is not an atomic fence."""
    for path in installation_mutation_paths(prefix, state):
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        return True
    return False
