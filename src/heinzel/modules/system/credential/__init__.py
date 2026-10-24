"""The credential module: secrets modules declare, kept in the OS keychain (ADR 0030); values are never shown."""

from typing import Annotated

from pydantic import Field

from heinzel import Check, Credentials, Module
from heinzel.modules.system.credential._models import CredentialInfo, CredentialSettings, RemovedCredential
from heinzel.modules.system.credential._service import CredentialService, KeyringBackend, make_credentials

# Essential, like system.config: `Credentials` is public API that plugins ask for, and without its provider every
# operation that needs a credential would silently disappear.
module = Module("credential", "Store and check the credentials modules need, in the OS keychain.", essential=True)
module.settings(CredentialSettings)
module.service(Credentials, make_credentials)
module.service(CredentialService)

Name = Annotated[str, Field(description="Name of the credential, as `hz system credential list` shows it.")]
Service = Annotated[
    str | None,
    Field(description="Service it is kept under; needed only when several services declare the name."),
]


@module.operation(name="list")
def list_(manager: CredentialService) -> list[CredentialInfo]:
    """List the credentials modules declare and whether each is stored, never their values."""
    return manager.declared()


@module.operation(read_only=False, heads={"cli"})  # the value comes from a hidden prompt or stdin, never a payload
def set_(manager: CredentialService, name: Name, service: Service = None) -> CredentialInfo:
    """Store a credential, read from standard input or a hidden prompt, never from the command line."""
    return manager.set(name, service)


@module.operation(read_only=False, heads={"cli"})  # so a web page can't remove credentials through the REST server
def remove(manager: CredentialService, name: Name, service: Service = None) -> RemovedCredential:
    """Remove a credential's value from the keychain."""
    return manager.remove(name, service)


@module.health
def credential(manager: CredentialService) -> list[Check]:
    """The keychain in use and every declared credential that is missing."""
    return manager.checks()


__all__ = ["CredentialInfo", "CredentialService", "CredentialSettings", "KeyringBackend", "RemovedCredential", "module"]
