"""The OS keychain as a credential backend, and the service behind `hz system credential` (ADR 0030)."""

import getpass
import sys
from collections.abc import Callable
from typing import NoReturn, Protocol, cast

from heinzel import Check, CredentialLet, Credentials, HeinzelError, Kernel, Status
from heinzel.kernel import CredentialBackend, DeclaredCredential, EnvironmentBackend, environment_variable
from heinzel.modules.system.credential._models import Backend, CredentialInfo, CredentialSettings

KEYCHAIN_PREFIX = "heinzel/"  # keychain entries are `heinzel/<service>`, so nothing collides with other applications


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


class KeyringBackend:
    """Credentials in the OS keychain through `keyring`; its errors become `HeinzelError`s without values."""

    def __init__(self, keychain: Keychain) -> None:
        """Wrap a keyring backend, usually `keyring.get_keyring()`."""
        self.keychain = keychain
        self.name = f"keyring ({keychain.name})"

    def get(self, service: str, name: str) -> str | None:
        """The value from the keychain, or None.

        Raises:
            HeinzelError: If the keychain can't be read.
        """
        from keyring.errors import KeyringError  # noqa: PLC0415 - keyring loads its backends on import; only when used

        try:
            return self.keychain.get_password(KEYCHAIN_PREFIX + service, name)
        except KeyringError as exc:
            self._fail("read", service, name, exc)

    def set(self, service: str, name: str, value: str) -> None:
        """Store the value in the keychain.

        Raises:
            HeinzelError: If the keychain refuses it.
        """
        from keyring.errors import KeyringError  # noqa: PLC0415 - see get

        try:
            self.keychain.set_password(KEYCHAIN_PREFIX + service, name, value)
        except KeyringError as exc:
            self._fail("store", service, name, exc, value)

    def delete(self, service: str, name: str) -> bool:
        """Remove the value from the keychain; whether there was one.

        Raises:
            HeinzelError: If the keychain refuses it.
        """
        from keyring.errors import KeyringError, PasswordDeleteError  # noqa: PLC0415 - see get

        if self.get(service, name) is None:
            return False
        try:
            self.keychain.delete_password(KEYCHAIN_PREFIX + service, name)
        except PasswordDeleteError:
            return False
        except KeyringError as exc:
            self._fail("remove", service, name, exc)
        return True

    def _fail(self, action: str, service: str, name: str, exc: Exception, value: str | None = None) -> NoReturn:
        # `from None` and the redaction keep a value out of the message and the traceback.
        reason = str(exc).replace(value, "***") if value else str(exc)
        msg = (
            f"The keychain ({self.name}) could not {action} the credential {name} of {service}: "
            f"{type(exc).__name__}: {reason}. Unlock the keychain, or set [system.credential] backend = "
            f'"environment" and export {environment_variable(service, name)}.'
        )
        raise HeinzelError(msg) from None


def _default_keychain() -> Keychain:
    import keyring  # noqa: PLC0415 - see KeyringBackend.get

    # keyring's own annotations are partial (its set_password is wrapped by a decorator); Keychain states what we use.
    return cast("Keychain", keyring.get_keyring())


def usable(keychain: Keychain) -> bool:
    """Whether keyring found a real keychain, rather than its `fail` or `null` placeholder."""
    from keyring.backends import fail, null  # noqa: PLC0415 - see KeyringBackend.get

    return not isinstance(keychain, fail.Keyring | null.Keyring)


def select_backend(choice: Backend, keychain: Callable[[], Keychain] = _default_keychain) -> CredentialBackend:
    """The backend for a `backend` setting: the keychain if usable (or forced), else the environment."""
    if choice == "environment":
        return EnvironmentBackend()
    found = keychain()
    return KeyringBackend(found) if choice == "keyring" or usable(found) else EnvironmentBackend()


def make_credentials(kernel: Kernel, settings: CredentialSettings) -> Credentials:
    """The `Credentials` every module gets: the configured backend and every declared credential."""
    return Credentials(select_backend(settings.backend), [(r.qualname, let) for r, let in kernel.lets(CredentialLet)])


def read_secret(prompt: str) -> str:
    """A value from standard input when it is piped, else from a prompt that doesn't echo; never from arguments."""
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    return sys.stdin.read().rstrip("\r\n")


class CredentialService:
    """Lists, stores and removes declared credentials, and reports them to doctor; never returns a value."""

    def __init__(self, credentials: Credentials) -> None:
        """Work on the credentials every module gets."""
        self.credentials = credentials

    def declared(self) -> list[CredentialInfo]:
        """Every declared credential with its presence."""
        return [self._info(declared) for declared in self._sorted()]

    def set(self, name: str, service: str | None, read: Callable[[str], str] = read_secret) -> CredentialInfo:
        """Store a declared credential, reading the value with `read` (a hidden prompt or standard input)."""
        declared = self.credentials.lookup(name, service)
        self.credentials.set(declared.let, read(f"Value for {declared.name} ({declared.purpose}): "))
        return self._info(declared)

    def remove(self, name: str, service: str | None) -> CredentialInfo:
        """Remove a declared credential's value."""
        return self._info(self.credentials.delete(name, service))

    def checks(self) -> list[Check]:
        """The backend, and one check per declared credential: up when stored, degraded when missing."""
        backend = self.credentials.backend
        checks = [
            Check(
                name="backend",
                status=Status.DEGRADED,
                detail="no keychain is in use; credentials are read from HZ_CREDENTIAL_<SERVICE>_<NAME> variables, "
                "which other processes of your user can read",
                fix="Install and unlock a Secret Service keychain (e.g. gnome-keyring), then set "
                '[system.credential] backend = "auto"',
            )
            if isinstance(backend, EnvironmentBackend)
            else Check(name="backend", status=Status.UP, detail=backend.name)
        ]
        for declared in self._sorted():
            label = f"{declared.service}/{declared.name}"
            checks.append(
                Check(name=f"credential {declared.name}", status=Status.UP, detail=f"{label} is stored")
                if self.credentials.has(declared.let)
                else self.credentials.attention(declared.let, f"{label} is missing: {declared.purpose}")
            )
        return checks

    def _sorted(self) -> list[DeclaredCredential]:
        return sorted(self.credentials.declared, key=lambda d: (d.service, d.name))

    def _info(self, declared: DeclaredCredential) -> CredentialInfo:
        return CredentialInfo(
            name=declared.name,
            service=declared.service,
            purpose=declared.purpose,
            module=declared.module,
            backend=self.credentials.backend.name,
            present=self.credentials.has(declared.let),
        )
