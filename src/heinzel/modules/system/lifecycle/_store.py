"""Private, locked lifecycle metadata; the generic Python authority owns every activation transition."""

import contextlib
import fcntl
import hashlib
import os
import shutil
import stat
import threading
from collections.abc import Generator, Mapping
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

from heinzel import HeinzelError
from heinzel.modules.system.lifecycle._models import (
    Activation,
    CleanupTarget,
    EnvironmentManifest,
    HighWater,
    RuntimeCleanup,
    RuntimeManifest,
    RuntimeProof,
    Transition,
)
from heinzel.modules.system.lifecycle._writers import writer_lease

MAX_METADATA = 8 * 1024 * 1024
MAX_RUNTIME_FILE = 256 * 1024 * 1024
SAFE_ERROR = "The owned desktop runtime is unavailable or changed. Repair this profile before starting it."
_CLEANUP_INTERRUPTED = "The runtime cleanup was interrupted safely; retry it to finish."


def private_directory(path: Path, *, create: bool = False) -> None:
    """Require an owner-controlled, unlinked directory for lifecycle metadata.

    Raises:
        HeinzelError: If the private filesystem boundary cannot be established.
    """
    try:
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        value = path.lstat()
        if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.getuid() or value.st_mode & 0o077:
            raise HeinzelError(SAFE_ERROR)
    except OSError:
        raise HeinzelError(SAFE_ERROR) from None


def regular_bytes(path: Path, *, executable: bool = False, limit: int = MAX_METADATA) -> bytes:
    """Read a bounded, owner-controlled regular file without following its final path component.

    Raises:
        HeinzelError: If ownership, type, mode or boundedness cannot be verified.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            value = os.fstat(descriptor)
            owners = {0, os.getuid()} if executable else {os.getuid()}
            mask = 0o022 if executable else 0o077
            if (
                not stat.S_ISREG(value.st_mode)
                or value.st_uid not in owners
                or value.st_mode & mask
                or value.st_nlink != 1
            ):
                raise HeinzelError(SAFE_ERROR)
            if value.st_size > limit:
                raise HeinzelError(SAFE_ERROR)
            with os.fdopen(os.dup(descriptor), "rb") as source:
                result = source.read(limit + 1)
            if len(result) > limit:
                raise HeinzelError(SAFE_ERROR)
            return result
        finally:
            os.close(descriptor)
    except OSError:
        raise HeinzelError(SAFE_ERROR) from None


def fsync_directory(path: Path) -> None:
    """Publish filesystem changes durably before reporting a transition."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_record(path: Path, value: BaseModel) -> None:
    """Write completed private JSON before an atomic rename, then sync its parent."""
    temporary = path.parent / f".lifecycle-{uuid4()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(os.dup(descriptor), "wb") as target:
            target.write(value.model_dump_json().encode() + b"\n")
            target.flush()
            os.fsync(target.fileno())
    finally:
        os.close(descriptor)
    try:
        temporary.replace(path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _inventory(directory: Path, inventory: Mapping[str, str], *, skip: str | None = None) -> None:
    remaining = set(inventory)
    for current, directories, files in os.walk(directory, followlinks=False):
        root = Path(current)
        private_directory(root)
        for child in directories:
            private_directory(root / child)
        for child in files:
            path = root / child
            name = path.relative_to(directory).as_posix()
            if name == skip:
                continue
            if (
                name not in remaining
                or hashlib.sha256(regular_bytes(path, limit=MAX_RUNTIME_FILE)).hexdigest() != inventory[name]
            ):
                raise HeinzelError(SAFE_ERROR)
            remaining.remove(name)
    if remaining:
        raise HeinzelError(SAFE_ERROR)


class LifecycleStore:
    """Authority over installed pointer/journal state; all transition methods require its exclusive lock."""

    def __init__(self, directory: Path) -> None:
        """Keep a trusted profile-derived root; constructor arguments never come from a model-visible path option."""
        self.directory = directory
        self._locked_by: tuple[int, int] | None = None

    @contextlib.contextmanager
    def lock(self) -> Generator[None]:
        """Serialize shared native/CLI/API plans and transitions without trusting a PID file.

        Yields:
            Nothing while this authority holds the owner-only lock.

        Raises:
            HeinzelError: If another lifecycle authority is already changing the profile or state is unsafe.
        """
        private_directory(self.directory, create=True)
        path = self.directory / "lifecycle.lock"
        descriptor: int | None = None
        acquired = False
        try:
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            value = os.fstat(descriptor)
            if (
                not stat.S_ISREG(value.st_mode)
                or value.st_uid != os.getuid()
                or value.st_mode & 0o077
                or value.st_nlink != 1
            ):
                raise HeinzelError(SAFE_ERROR)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            self._locked_by = os.getpid(), threading.get_ident()
            yield
        except OSError:
            msg = "Another desktop lifecycle change owns this profile, or its private state is unsafe."
            raise HeinzelError(msg) from None
        finally:
            if acquired:
                self._locked_by = None
            if descriptor is not None:
                os.close(descriptor)

    def _require_lock(self) -> None:
        if self._locked_by != (os.getpid(), threading.get_ident()):
            msg = "A desktop lifecycle mutation requires its exclusive profile authority."
            raise HeinzelError(msg)

    def _read[T: BaseModel](self, name: str, model: type[T]) -> T | None:
        path = self.directory / name
        try:
            path.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            raise HeinzelError(SAFE_ERROR) from None
        try:
            return model.model_validate_json(regular_bytes(path))
        except (ValueError, ValidationError):
            raise HeinzelError(SAFE_ERROR) from None

    def active(self) -> Activation | None:
        """Read installed metadata without asserting that the corresponding backend is running."""
        return self._read("active.json", Activation)

    def pending(self) -> Transition | None:
        """Read durable in-flight intent, including an interrupted pointer change."""
        return self._read("transition.json", Transition)

    def verify(self, deployment: str) -> tuple[Path, RuntimeManifest]:
        """Verify complete runtime bytes before executing a candidate or reactivating an installed runtime.

        Raises:
            HeinzelError: If the id, inventory, interpreter or any owned runtime file does not match.
        """
        try:
            if len(deployment) != hashlib.sha256().digest_size * 2 or any(
                c not in "0123456789abcdef" for c in deployment
            ):
                raise HeinzelError(SAFE_ERROR)
            directory = self.directory / "deployments" / deployment
            private_directory(directory)
            encoded = regular_bytes(directory / "runtime.json")
            if hashlib.sha256(encoded).hexdigest() != deployment:
                raise HeinzelError(SAFE_ERROR)
            manifest = RuntimeManifest.model_validate_json(encoded)
            if (
                hashlib.sha256(
                    regular_bytes(Path(manifest.python), executable=True, limit=MAX_RUNTIME_FILE)
                ).hexdigest()
                != manifest.python_sha256
            ):
                raise HeinzelError(SAFE_ERROR)
            _inventory(directory, manifest.files, skip="runtime.json")
            if "environment.json" in manifest.files:
                environment = EnvironmentManifest.model_validate_json(regular_bytes(directory / "environment.json"))
                root = Path(environment.directory)
                environments = self.directory / "environments"
                private_directory(environments)
                if root.parent != environments or UUID(root.name).hex != root.name:
                    raise HeinzelError(SAFE_ERROR)
                if Path(manifest.python) != root / "bin" / "python":
                    raise HeinzelError(SAFE_ERROR)
                _inventory(root, environment.files)
        except (OSError, ValueError, ValidationError):
            raise HeinzelError(SAFE_ERROR) from None
        else:
            return directory, manifest

    def _generation(self) -> int:
        value = self._read("generation.json", HighWater)
        return value.generation if value else 0

    @contextlib.contextmanager
    def _backend_stopped(self) -> Generator[None]:
        # Lock order is lifecycle authority, writer barrier, then backend ownership.
        private_directory(self.directory.parent)
        path = self.directory.parent / "backend.lock"
        try:
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                value = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(value.st_mode)
                    or value.st_uid != os.getuid()
                    or value.st_mode & 0o077
                    or value.st_nlink != 1
                ):
                    raise HeinzelError(SAFE_ERROR)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
            finally:
                os.close(descriptor)
        except OSError:
            msg = "Stop the owned desktop backend before switching or uninstalling its runtime."
            raise HeinzelError(msg) from None

    def prepare(self, candidate: str | None, *, first_install: bool = False) -> Transition:
        """Persist install/update/uninstall intent before changing the active pointer.

        Raises:
            HeinzelError: If the plan, exclusive authority or verified state does not agree.
        """
        self._require_lock()
        if self.pending() or self._read("cleanup.json", RuntimeCleanup):
            msg = "A desktop transition or runtime cleanup is already pending; settle it before planning another."
            raise HeinzelError(msg)
        before = self.active()
        if first_install and (candidate is None or before is not None or self._read("generation.json", HighWater)):
            msg = "This profile has managed-runtime history; repair or migrate it before choosing another runtime."
            raise HeinzelError(msg)
        if candidate is not None:
            self.verify(candidate)
        elif before is None:
            msg = "This desktop profile has no runtime to uninstall."
            raise HeinzelError(msg)
        transition = Transition(
            id=uuid4(),
            before=before,
            candidate=candidate,
            action="uninstall" if candidate is None else "update" if before else "install",
            phase="prepared",
            generation=max(self._generation(), before.generation if before else 0) + 1,
        )
        atomic_record(self.directory / "transition.json", transition)
        return transition

    def _switched(self, transition: Transition, active: Activation | None) -> bool:
        if self._generation() != transition.generation:
            return False
        if transition.candidate is None:
            return active is None
        return active == Activation(
            generation=transition.generation,
            deployment=transition.candidate,
            previous=transition.before.deployment if transition.before else None,
        )

    def switch(self, identifier: UUID) -> Activation | None:
        """Change installed authority only after the caller drained and stopped its owned backend.

        Raises:
            HeinzelError: If the plan, exclusive authority or verified state does not agree.
        """
        self._require_lock()
        pending = self.pending()
        if not pending or pending.id != identifier or pending.phase != "prepared" or self.active() != pending.before:
            msg = "The desktop plan is stale; inspect the current transition before applying it."
            raise HeinzelError(msg)
        with writer_lease(self.directory, exclusive=True), self._backend_stopped():
            return self._switch(pending)

    def cancel(self, identifier: UUID) -> None:
        """Cancel only unchanged prepared intent; never roll back an activated runtime or rewind its generation.

        Raises:
            HeinzelError: If the exact plan is stale, already switched or lacks exclusive authority.
        """
        self._require_lock()
        pending = self.pending()
        if not pending or pending.id != identifier or pending.phase != "prepared" or self.active() != pending.before:
            msg = "The desktop plan is stale or already activated; reconcile it instead of cancelling."
            raise HeinzelError(msg)
        # Preserve the durable high-water mark even when preparation was interrupted before activation.
        atomic_record(
            self.directory / "generation.json", HighWater(generation=max(self._generation(), pending.generation))
        )
        (self.directory / "transition.json").unlink()
        fsync_directory(self.directory)

    def _switch(self, pending: Transition) -> Activation | None:
        atomic_record(self.directory / "generation.json", HighWater(generation=pending.generation))
        if pending.candidate is None:
            (self.directory / "active.json").unlink()
            fsync_directory(self.directory)
            active = None
        else:
            self.verify(pending.candidate)
            active = Activation(
                generation=pending.generation,
                deployment=pending.candidate,
                previous=pending.before.deployment if pending.before else None,
            )
            atomic_record(self.directory / "active.json", active)
        atomic_record(self.directory / "transition.json", pending.model_copy(update={"phase": "switched"}))
        return active

    def complete(self, identifier: UUID, proof: RuntimeProof | None = None) -> None:
        """Complete only exact verified runtime identity, or a stopped uninstall preserving data.

        Raises:
            HeinzelError: If the plan, exclusive authority or verified state does not agree.
        """
        self._require_lock()
        pending = self.pending()
        active = self.active()
        if (
            not pending
            or pending.id != identifier
            or pending.phase != "switched"
            or not self._switched(pending, active)
        ):
            msg = "The desktop transition changed; inspect it before completing the plan."
            raise HeinzelError(msg)
        if active is not None:
            _, manifest = self.verify(active.deployment)
            if (
                not proof
                or proof.deployment != active.deployment
                or proof.generation != active.generation
                or proof.version != manifest.core_version
                or proof.ui_sha256 != manifest.ui_sha256
            ):
                msg = "The actual owned backend has not proved this exact runtime and UI version."
                raise HeinzelError(msg)
        atomic_record(self.directory / "transition.json", pending.model_copy(update={"phase": "verified"}))
        (self.directory / "transition.json").unlink()
        fsync_directory(self.directory)

    def purge_uninstalled_runtime(self) -> tuple[int, int]:
        """Remove verified deployment/environment payloads after uninstall, keeping profile data and fences.

        This is an explicit cleanup step; it never removes profile configuration, credentials, user work,
        lifecycle metadata, or the native application. Every runtime and environment is verified before any
        deletion begins. Orphaned or modified payloads are retained and cause the operation to refuse.

        Returns:
            The number of deployment and standalone environment directories removed.

        Raises:
            HeinzelError: If a runtime is active, a transition is pending, a writer/backend is live, or any
                payload cannot be proven to belong to this profile.
        """
        self._require_lock()
        if self.active() is not None or self.pending() is not None:
            msg = "Uninstall the managed runtime and settle its transition before removing runtime files."
            raise HeinzelError(msg)
        with writer_lease(self.directory, exclusive=True), self._backend_stopped():
            if self.active() is not None or self.pending() is not None:
                raise HeinzelError(SAFE_ERROR)
            cleanup = self._read("cleanup.json", RuntimeCleanup)
            if cleanup is None:
                cleanup = RuntimeCleanup(id=uuid4(), targets=tuple(_verified_cleanup_targets(self)))
                if len(cleanup.model_dump_json().encode()) + 1 > MAX_METADATA:
                    msg = (
                        "The verified runtime inventory is too large for a durable cleanup plan; no files were changed."
                    )
                    raise HeinzelError(msg)
                atomic_record(self.directory / "cleanup.json", cleanup)
            self._resume_cleanup(cleanup)
            (self.directory / "cleanup.json").unlink()
            fsync_directory(self.directory)
        deployments = sum(target.parent == "deployments" for target in cleanup.targets)
        environments = len(cleanup.targets) - deployments
        return deployments, environments

    def _resume_cleanup(self, cleanup: RuntimeCleanup) -> None:
        """Move planned trees to journal-specific names and safely resume deletion after interruption.

        Raises:
            HeinzelError: If a target changed or a quarantine path conflicts with another tree.
        """
        for index, target in enumerate(cleanup.targets):
            parent = self.directory / target.parent
            source = parent / target.name
            staged = parent / f".cleanup-{cleanup.id.hex}-{index}"
            source_exists = _exists(source)
            staged_exists = _exists(staged)
            if source_exists and staged_exists:
                raise HeinzelError(SAFE_ERROR)
            if source_exists:
                _verify_remaining_tree(source, target.files, target.directories, complete=True)
                try:
                    source.rename(staged)
                    fsync_directory(parent)
                except OSError:
                    raise HeinzelError(_CLEANUP_INTERRUPTED) from None
                staged_exists = True
            if staged_exists:
                _verify_remaining_tree(staged, target.files, target.directories, complete=False)
                try:
                    shutil.rmtree(staged)
                    fsync_directory(parent)
                except OSError:
                    raise HeinzelError(_CLEANUP_INTERRUPTED) from None

    def recover(self) -> str:
        """Reconcile metadata only; an interrupted activation still requires fresh running proof.

        Raises:
            HeinzelError: If the plan, exclusive authority or verified state does not agree.
        """
        self._require_lock()
        pending = self.pending()
        if pending is None:
            return "settled"
        active = self.active()
        if pending.phase == "prepared" and active == pending.before:
            return "prepared"
        if not self._switched(pending, active):
            msg = "The desktop activation and journal disagree; repair this profile before starting it."
            raise HeinzelError(msg)
        if pending.phase == "verified":
            (self.directory / "transition.json").unlink()
            fsync_directory(self.directory)
            return "settled"
        if pending.phase == "prepared":
            atomic_record(self.directory / "transition.json", pending.model_copy(update={"phase": "switched"}))
        return "verification-required"


def _verified_cleanup_targets(store: LifecycleStore) -> list[CleanupTarget]:
    """Collect exact file inventories for complete deployment and environment trees.

    Raises:
        HeinzelError: If any tree is not fully owned and referenced by a verified runtime inventory.
    """
    deployments = store.directory / "deployments"
    environments = store.directory / "environments"
    private_directory(deployments)
    referenced: set[Path] = set()
    targets: list[CleanupTarget] = []
    for entry in deployments.iterdir():
        if not entry.is_dir() or entry.is_symlink():
            raise HeinzelError(SAFE_ERROR)
        _, manifest = store.verify(entry.name)
        deployment_inventory = dict(manifest.files.items())
        deployment_inventory["runtime.json"] = entry.name
        targets.append(
            CleanupTarget(
                parent="deployments",
                name=entry.name,
                files=deployment_inventory,
                directories=_directory_inventory(entry),
            )
        )
        if "environment.json" in manifest.files:
            environment = EnvironmentManifest.model_validate_json(regular_bytes(entry / "environment.json"))
            environment_path = Path(environment.directory)
            referenced.add(environment_path)
            targets.append(
                CleanupTarget(
                    parent="environments",
                    name=environment_path.name,
                    files=environment.files,
                    directories=_directory_inventory(environment_path),
                )
            )
    _verified_environments(environments, referenced)
    return targets


def _verified_environments(environments: Path, referenced: set[Path]) -> list[Path]:
    """Return the exact environment set named by verified runtimes, refusing unreferenced payloads.

    Raises:
        HeinzelError: If an environment is missing, unowned or not referenced by a verified runtime.
    """
    try:
        environments.lstat()
    except FileNotFoundError:
        if not referenced:
            return []
        raise HeinzelError(SAFE_ERROR) from None
    private_directory(environments)
    paths = list(environments.iterdir())
    if set(paths) != referenced or any(not path.is_dir() or path.is_symlink() for path in paths):
        raise HeinzelError(SAFE_ERROR)
    return paths


def _exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _directory_inventory(directory: Path) -> tuple[str, ...]:
    """List relative directories in deterministic order after the owning tree was verified."""
    return tuple(
        sorted(
            (Path(current) / name).relative_to(directory).as_posix()
            for current, directories, _ in os.walk(directory, followlinks=False)
            for name in directories
        )
    )


def _verify_remaining_tree(
    directory: Path,
    inventory: Mapping[str, str],
    expected_directories: tuple[str, ...],
    *,
    complete: bool,
) -> None:
    """Check a complete source or partial quarantine against the exact pre-deletion inventory.

    Raises:
        HeinzelError: If ownership, type, path, bytes or completeness do not match the recorded inventory.
    """
    remaining = set(inventory)
    remaining_directories: set[str] = set(expected_directories)
    for current, directories, files in os.walk(directory, followlinks=False):
        root = Path(current)
        private_directory(root)
        for name in directories:
            path = root / name
            private_directory(path)
            relative = path.relative_to(directory).as_posix()
            if relative not in remaining_directories:
                raise HeinzelError(SAFE_ERROR)
            remaining_directories.remove(relative)
        for name in files:
            path = root / name
            relative = path.relative_to(directory).as_posix()
            expected = inventory.get(relative)
            if expected is None or hashlib.sha256(regular_bytes(path, limit=MAX_RUNTIME_FILE)).hexdigest() != expected:
                raise HeinzelError(SAFE_ERROR)
            remaining.remove(relative)
    if complete and (remaining or remaining_directories):
        raise HeinzelError(SAFE_ERROR)
