"""Credentials: the one way modules and plugins get secrets (ADR 0030).

Modules declare what they need with `module.credential(...)` and ask for `Credentials` by type. The service reads and
writes values through a backend: the OS keychain (provided by `system.credential`), environment variables where no
keychain exists, or memory in tests. Values never appear in anything the service returns besides `get` and `require`,
nor in its error messages.

Service names are a naming scheme, not an isolation boundary: every module and plugin runs in the same process with
the user's rights and could read any credential. `get` taking only the let a module declared makes intent explicit.
"""

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._lets import Check, CredentialLet, Status

ENVIRONMENT_PREFIX = "HZ_CREDENTIAL_"


class CredentialBackend(Protocol):
    """Where credential values live. Implementations never log or echo values."""

    @property
    def name(self) -> str:
        """What `hz system credential list` and doctor show, e.g. `keyring (macOS Keyring)` or `environment`."""
        ...

    def get(self, service: str, name: str) -> str | None:
        """The value, or None if there is none."""
        ...

    def set(self, service: str, name: str, value: str) -> None:
        """Store a value, replacing any earlier one."""
        ...

    def delete(self, service: str, name: str) -> bool:
        """Remove a value; whether there was one."""
        ...


def environment_variable(service: str, name: str) -> str:
    """The variable the environment backend reads: `HZ_CREDENTIAL_<SERVICE>_<NAME>`, upper case, others as `_`."""
    return ENVIRONMENT_PREFIX + re.sub(r"[^A-Z0-9]", "_", f"{service}_{name}".upper())


def subprocess_environment(environment: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for a program Heinzelmännchen starts: the current one without `HZ_CREDENTIAL_*` variables.

    uv, git, copier and the like never need a module's credentials, so they don't inherit them.
    """
    source = os.environ if environment is None else environment
    return {key: value for key, value in source.items() if not key.startswith(ENVIRONMENT_PREFIX)}


class EnvironmentBackend:
    """Read-only credentials from `HZ_CREDENTIAL_<SERVICE>_<NAME>` variables, for machines without a keychain."""

    name = "environment"

    def get(self, service: str, name: str) -> str | None:
        """The variable's value; None when unset or empty."""
        return os.environ.get(environment_variable(service, name)) or None

    def set(self, service: str, name: str, value: str) -> None:
        """Refuse: a process can't change the environment it was started in.

        Raises:
            HeinzelError: Always, naming the variable to export.
        """
        del value  # never part of the message
        msg = (
            f"No keychain is available, so credentials come from the environment: export "
            f"{environment_variable(service, name)} where Heinzelmännchen runs."
        )
        raise HeinzelError(msg)

    def delete(self, service: str, name: str) -> bool:
        """Refuse, as `set` does.

        Raises:
            HeinzelError: Always, naming the variable to unset.
        """
        msg = f"Credentials come from the environment: unset {environment_variable(service, name)} instead."
        raise HeinzelError(msg)


@dataclass(frozen=True, slots=True)
class DeclaredCredential:
    """A credential some module declared, with the service it is kept under."""

    service: str
    name: str
    purpose: str
    module: str
    """Qualified name of the declaring module."""
    let: CredentialLet

    @property
    def label(self) -> str:
        """`service/name`, how doctor and messages name it."""
        return f"{self.service}/{self.name}"

    @property
    def fix(self) -> str:
        """The command that stores (or replaces) the value."""
        return f"hz system credential set {self.name} --service {self.service}"


class Credentials:
    """Secrets for modules and plugins, injected by type; a module reads only the credentials it declared."""

    def __init__(self, backend: CredentialBackend, declared: Iterable[tuple[str, CredentialLet]] = ()) -> None:
        """Serve the declared credentials from a backend.

        Args:
            backend: Where values live.
            declared: Every declared credential with the qualified name of its module, as `Kernel.lets` returns them.
        """
        self.backend = backend
        self.declared = [
            DeclaredCredential(
                service=let.service or module, name=let.name, purpose=let.purpose, module=module, let=let
            )
            for module, let in declared
        ]

    def _declared(self, credential: CredentialLet) -> DeclaredCredential:
        found = next((d for d in self.declared if d.let is credential), None)
        if found is None:
            msg = (
                f"The credential {credential.name} is not declared by a registered module; declare it with "
                f"`module.credential(...)` and pass the let it returns."
            )
            raise HeinzelError(msg)
        return found

    def get(self, credential: CredentialLet) -> str | None:
        """The value of a credential the module declared, or None if it isn't stored.

        Raises:
            HeinzelError: If the let wasn't declared by a registered module, or the backend fails.
        """
        return self._read(self._declared(credential))

    def _read(self, declared: DeclaredCredential) -> str | None:
        try:
            return self.backend.get(declared.service, declared.name)
        except HeinzelError:
            raise
        except Exception as exc:  # noqa: BLE001 - a backend's error text may hold the value, which we can't redact
            msg = f"Could not read {declared.label} from {self.backend.name}: {type(exc).__name__}"
            raise HeinzelError(msg) from None

    def has(self, credential: CredentialLet) -> bool:
        """Whether a credential the module declared has a value."""
        return self.get(credential) is not None

    def require(self, credential: CredentialLet) -> str:
        """The value of a credential the module declared.

        Raises:
            HeinzelError: If it isn't stored, with the command that stores it.
        """
        declared = self._declared(credential)
        value = self._read(declared)
        if value is None:
            msg = f"The credential {declared.label} ({declared.purpose}) is missing; store it with `{declared.fix}`."
            raise HeinzelError(msg)
        return value

    def set(self, credential: CredentialLet, value: str) -> None:
        """Store the value of a declared credential; `hz system credential set` does this for people.

        Raises:
            HeinzelError: If the value is empty, or the backend can't store it; never with the value in the message.
        """
        declared = self._declared(credential)
        if not value.strip():
            msg = f"The value for {declared.label} is empty; nothing was stored."
            raise HeinzelError(msg)
        try:
            self.backend.set(declared.service, declared.name, value)
        except HeinzelError:
            raise
        except Exception as exc:  # noqa: BLE001 - any backend's error text may echo the value; redact it
            reason = f"{type(exc).__name__}: {str(exc).replace(value, '***')}"
            msg = f"Could not store {declared.label} in {self.backend.name}: {reason}"
            raise HeinzelError(msg) from None

    def delete(self, credential: CredentialLet) -> bool:
        """Remove the value of a declared credential; whether there was one.

        Raises:
            HeinzelError: If the backend can't remove it.
        """
        declared = self._declared(credential)
        try:
            return self.backend.delete(declared.service, declared.name)
        except HeinzelError:
            raise
        except Exception as exc:  # noqa: BLE001 - see _read
            msg = f"Could not remove {declared.label} from {self.backend.name}: {type(exc).__name__}"
            raise HeinzelError(msg) from None

    def attention(self, credential: CredentialLet, detail: str) -> Check:
        """A degraded health check for a credential that is missing, expired or rejected, with the fix.

        `detail` says what happened ("the example service rejected the token: 401"); it must not contain the value.
        """
        declared = self._declared(credential)
        return Check(name=f"credential {declared.label}", status=Status.DEGRADED, detail=detail, fix=declared.fix)
