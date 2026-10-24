"""Credentials: the one way modules and plugins get secrets (ADR 0030).

Modules declare what they need with `module.credential(...)` and ask for `Credentials` by type. The service reads and
writes values through a backend: the OS keychain (provided by `system.credential`), environment variables where no
keychain exists, or memory in tests. Values never appear in anything the service returns besides `get` and `require`,
nor in its error messages.
"""

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from heinzel.kernel._errors import HeinzelError
from heinzel.kernel._lets import Check, CredentialLet, Status

ENVIRONMENT_PREFIX = "HZ_CREDENTIAL_"


class CredentialBackend(Protocol):
    """Where credential values live. Implementations never log or echo values."""

    @property
    def name(self) -> str:
        """What `hz system credential list` and doctor show, e.g. `keyring (macOS Keychain)` or `environment`."""
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
    def fix(self) -> str:
        """The command that stores (or replaces) the value."""
        return f"hz system credential set {self.name} --service {self.service}"


class Credentials:
    """Secrets for modules and plugins, injected by type; only declared credentials can be read or written."""

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

    def lookup(self, credential: CredentialLet | str, service: str | None = None) -> DeclaredCredential:
        """Find a declared credential by its let, or by name and optionally service.

        Raises:
            HeinzelError: If nobody declared it, or several services declare the name and none was given.
        """
        if isinstance(credential, CredentialLet):
            found = [d for d in self.declared if d.let is credential]
            label = credential.name
        else:
            found = [d for d in self.declared if d.name == credential and service in {None, d.service}]
            label = credential if service is None else f"{credential} (service {service})"
        services = sorted({d.service for d in found})
        if not found:
            msg = f"No module declares the credential {label}; `hz system credential list` shows the declared ones."
            raise HeinzelError(msg)
        if len(services) > 1:
            msg = f"Several services declare the credential {label}: {', '.join(services)}; pass --service."
            raise HeinzelError(msg)
        return found[0]

    def get(self, credential: CredentialLet | str, service: str | None = None) -> str | None:
        """The value of a declared credential, or None if it isn't stored."""
        declared = self.lookup(credential, service)
        return self.backend.get(declared.service, declared.name)

    def has(self, credential: CredentialLet | str, service: str | None = None) -> bool:
        """Whether a declared credential has a value."""
        return self.get(credential, service) is not None

    def require(self, credential: CredentialLet | str, service: str | None = None) -> str:
        """The value of a declared credential.

        Raises:
            HeinzelError: If it isn't stored, with the command that stores it.
        """
        declared = self.lookup(credential, service)
        value = self.backend.get(declared.service, declared.name)
        if value is None:
            msg = f"The credential {declared.name} ({declared.purpose}) is missing; store it with `{declared.fix}`."
            raise HeinzelError(msg)
        return value

    def set(self, credential: CredentialLet | str, value: str, service: str | None = None) -> DeclaredCredential:
        """Store the value of a declared credential.

        Raises:
            HeinzelError: If the value is empty, or the backend can't store it.
        """
        declared = self.lookup(credential, service)
        if not value.strip():
            msg = f"The value for {declared.name} is empty; nothing was stored."
            raise HeinzelError(msg)
        self.backend.set(declared.service, declared.name, value)
        return declared

    def delete(self, credential: CredentialLet | str, service: str | None = None) -> DeclaredCredential:
        """Remove the value of a declared credential; removing a missing one is fine."""
        declared = self.lookup(credential, service)
        self.backend.delete(declared.service, declared.name)
        return declared

    def attention(self, credential: CredentialLet | str, detail: str, service: str | None = None) -> Check:
        """A degraded health check for a credential that is missing, expired or rejected, with the fix.

        `detail` says what happened ("GitHub rejected the token: 401"); it must not contain the value.
        """
        declared = self.lookup(credential, service)
        return Check(name=f"credential {declared.name}", status=Status.DEGRADED, detail=detail, fix=declared.fix)
