"""The OS keychain as a credential backend, and the service behind `hz system credential` (ADR 0030)."""

import getpass
import sys
from collections import defaultdict
from collections.abc import Callable
from typing import NoReturn, Protocol, cast

from heinzel import Check, CredentialLet, Credentials, HeinzelError, Kernel, Status
from heinzel.kernel import CredentialBackend, DeclaredCredential, EnvironmentBackend, environment_variable
from heinzel.modules.system.credential._models import Backend, CredentialInfo, CredentialSettings, RemovedCredential

KEYCHAIN_PREFIX = "heinzel/"  # keychain entries are `heinzel/<service>`, so nothing collides with other applications

# The keyring backends that are an operating system's keychain. Anything else (keyrings.alt's PlaintextKeyring, a
# backend a plugin registers, one chosen by PYTHON_KEYRING_BACKEND or keyringrc.cfg) may write a plaintext file, which
# ADR 0030 rules out: `auto` falls back to the environment instead, and a forced one is degraded in doctor.
OS_KEYCHAINS = frozenset(
    {
        "keyring.backends.macOS.Keyring",
        "keyring.backends.Windows.WinVaultKeyring",
        "keyring.backends.SecretService.Keyring",
        "keyring.backends.kwallet.DBusKeyring",
        "keyring.backends.kwallet.DBusKeyringKWallet4",
    }
)
CHAINER = "keyring.backends.chainer.ChainerBackend"


class Keychain(Protocol):
    """What we use of a keyring backend (`keyring.backend.KeyringBackend`)."""

    @property
    def name(self) -> str:
        """The backend's name, e.g. `macOS Keyring`."""
        ...

    def get_password(self, service: str, username: str) -> str | None:
        """The stored password, or None."""
        ...

    def set_password(self, service: str, username: str, password: str) -> None:
        """Store a password."""
        ...

    def delete_password(self, service: str, username: str) -> None:
        """Remove a password."""
        ...


def _qualname(keychain: object) -> str:
    return f"{type(keychain).__module__}.{type(keychain).__qualname__}"


def is_os_keychain(keychain: object) -> bool:
    """Whether a keyring backend is an operating system's keychain; a chainer only if every backend in it is."""
    if _qualname(keychain) == CHAINER:
        children = cast("list[object]", getattr(keychain, "backends", []))
        return bool(children) and all(is_os_keychain(child) for child in children)
    return _qualname(keychain) in OS_KEYCHAINS


class KeyringBackend:
    """Credentials in a keyring backend; its errors become `HeinzelError`s without values."""

    def __init__(self, keychain: Keychain) -> None:
        """Wrap a keyring backend, usually `keyring.get_keyring()`."""
        self.keychain = keychain
        self.name = f"keyring ({keychain.name})"
        self.os_keychain = is_os_keychain(keychain)

    def get(self, service: str, name: str) -> str | None:
        """The value from the keychain, or None.

        Raises:
            HeinzelError: If the keychain can't be read.
        """
        try:
            return self.keychain.get_password(KEYCHAIN_PREFIX + service, name)
        except Exception as exc:  # noqa: BLE001 - third-party backends raise anything; report it without values
            self._fail("read", service, name, exc)

    def set(self, service: str, name: str, value: str) -> None:
        """Store the value in the keychain.

        Raises:
            HeinzelError: If the keychain refuses it.
        """
        try:
            self.keychain.set_password(KEYCHAIN_PREFIX + service, name, value)
        except Exception as exc:  # noqa: BLE001 - see get
            self._fail("store", service, name, exc, value)

    def delete(self, service: str, name: str) -> bool:
        """Remove the value from the keychain; whether there was one.

        Raises:
            HeinzelError: If the keychain refuses it.
        """
        from keyring.errors import PasswordDeleteError  # noqa: PLC0415 - keyring loads its backends on import

        try:
            self.keychain.delete_password(KEYCHAIN_PREFIX + service, name)
        except PasswordDeleteError:  # keyring's way of saying there was nothing to delete
            return False
        except Exception as exc:  # noqa: BLE001 - see get
            self._fail("remove", service, name, exc)
        return True

    def _fail(self, action: str, service: str, name: str, exc: Exception, value: str | None = None) -> NoReturn:
        # `from None` and the redaction keep a value out of the message and the traceback.
        reason = str(exc).replace(value, "***") if value else str(exc)
        msg = (
            f"The keychain ({self.name}) could not {action} the credential {service}/{name}: "
            f"{type(exc).__name__}: {reason}. Unlock the keychain, or set [system.credential] backend = "
            f'"environment" and export {environment_variable(service, name)}.'
        )
        raise HeinzelError(msg) from None


def _default_keychain() -> Keychain:
    import keyring  # noqa: PLC0415 - keyring loads its backends on import; only when credentials are used

    # keyring's own annotations are partial (its set_password is wrapped by a decorator); Keychain states what we use.
    return cast("Keychain", keyring.get_keyring())


def select_backend(choice: Backend, keychain: Callable[[], Keychain] = _default_keychain) -> CredentialBackend:
    """The backend a `backend` setting picks: an OS keychain under `auto`, a forced keyring backend, else the env."""
    if choice == "environment":
        return EnvironmentBackend()
    found = keychain()
    return KeyringBackend(found) if choice == "keyring" or is_os_keychain(found) else EnvironmentBackend()


def make_credentials(kernel: Kernel, settings: CredentialSettings) -> Credentials:
    """The `Credentials` every module gets: the configured backend and every declared credential."""
    return Credentials(select_backend(settings.backend), [(r.qualname, let) for r, let in kernel.lets(CredentialLet)])


def read_secret(prompt: str) -> str:
    """A value from standard input when it is piped, else from a prompt that doesn't echo; never from arguments."""
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    return sys.stdin.read().rstrip("\r\n")


class CredentialService:
    """Finds declared credentials by name and service, stores and removes them, reports them; never returns values."""

    def __init__(self, credentials: Credentials) -> None:
        """Work on the credentials every module gets."""
        self.credentials = credentials

    def lookup(self, name: str, service: str | None = None) -> DeclaredCredential:
        """A declared credential by name, and by service when several services declare the name.

        Raises:
            HeinzelError: If nobody declared it, or several services declare the name and none was given.
        """
        found = [d for d in self.credentials.declared if d.name == name and service in {None, d.service}]
        label = name if service is None else f"{service}/{name}"
        services = sorted({d.service for d in found})
        if not found:
            msg = f"No module declares the credential {label}; `hz system credential list` shows the declared ones."
            raise HeinzelError(msg)
        if len(services) > 1:
            msg = f"Several services declare the credential {label}: {', '.join(services)}; pass --service."
            raise HeinzelError(msg)
        return found[0]

    def declared(self) -> list[CredentialInfo]:
        """Every declared credential with its presence."""
        return [self._info(declared) for declared in self._sorted()]

    def set(self, name: str, service: str | None, read: Callable[[str], str] = read_secret) -> CredentialInfo:
        """Store a declared credential, reading the value with `read` (a hidden prompt or standard input)."""
        declared = self.lookup(name, service)
        self.credentials.set(declared.let, read(f"Value for {declared.label} ({declared.purpose}): "))
        return self._info(declared)

    def remove(self, name: str, service: str | None) -> RemovedCredential:
        """Remove a declared credential's value."""
        declared = self.lookup(name, service)
        removed = self.credentials.delete(declared.let)
        return RemovedCredential(**self._info(declared).model_dump(), removed=removed)

    def checks(self) -> list[Check]:
        """The backend, colliding variable names, and each credential: up when stored, degraded when missing."""
        checks = [self._backend_check()]
        unique = {(d.service, d.name): d for d in self._sorted()}
        variables: defaultdict[str, list[str]] = defaultdict(list)
        for declared in unique.values():
            variables[environment_variable(declared.service, declared.name)].append(declared.label)
        checks.extend(
            Check(
                name=f"variable {variable}",
                status=Status.DEGRADED,
                detail=f"{' and '.join(labels)} share this variable in the environment fallback",
                fix="Rename one of the credentials or services; dots and dashes both become `_`",
            )
            for variable, labels in variables.items()
            if len(labels) > 1
        )
        checks.extend(
            Check(name=f"credential {declared.label}", status=Status.UP, detail="stored")
            if self.credentials.has(declared.let)
            else self.credentials.attention(declared.let, f"missing: {declared.purpose}")
            for declared in unique.values()
        )
        return checks

    def _backend_check(self) -> Check:
        backend = self.credentials.backend
        if isinstance(backend, EnvironmentBackend):
            return Check(
                name="backend",
                status=Status.DEGRADED,
                detail="no OS keychain is in use; credentials are read from HZ_CREDENTIAL_<SERVICE>_<NAME> "
                "variables, which other processes of your user can read",
                fix="Install and unlock a Secret Service keychain (e.g. gnome-keyring), then set "
                '[system.credential] backend = "auto"',
            )
        if isinstance(backend, KeyringBackend) and not backend.os_keychain:
            return Check(
                name="backend",
                status=Status.DEGRADED,
                detail=f"{backend.name} is not an OS keychain and may keep values in a plaintext file",
                fix='Set [system.credential] backend = "auto" and remove the backend from PYTHON_KEYRING_BACKEND '
                "or keyringrc.cfg",
            )
        return Check(name="backend", status=Status.UP, detail=backend.name)

    def _sorted(self) -> list[DeclaredCredential]:
        return sorted(self.credentials.declared, key=lambda d: (d.service, d.name, d.module))

    def _info(self, declared: DeclaredCredential) -> CredentialInfo:
        return CredentialInfo(
            name=declared.name,
            service=declared.service,
            purpose=declared.purpose,
            module=declared.module,
            backend=self.credentials.backend.name,
            present=self.credentials.has(declared.let),
        )
