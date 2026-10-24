"""Models of the credential module."""

from typing import Literal

from pydantic import BaseModel, Field

from heinzel import ModuleSettings

Backend = Literal["auto", "keyring", "environment"]


class CredentialSettings(ModuleSettings):
    """Settings of the credential module: the `[system.credential]` table or `HZ_SYSTEM_CREDENTIAL__*` variables."""

    backend: Backend = Field(
        default="auto",
        description="Where credentials live. `auto`: the OS keychain when there is one, else the environment. "
        "`keyring`: whatever keyring backend is configured, even one that is not an OS keychain. "
        "`environment`: read-only HZ_CREDENTIAL_<SERVICE>_<NAME> variables.",
    )


class CredentialInfo(BaseModel):
    """A declared credential and whether it has a value; never the value itself."""

    name: str = Field(description="Name of the credential, e.g. api-token.")
    service: str = Field(description="Service it is kept under; by default the qualified name of its module.")
    purpose: str = Field(description="What it is for and where to get one.")
    module: str = Field(description="Qualified name of the module that declared it.")
    backend: str = Field(description="Where the value lives: a keyring backend or `environment`.")
    present: bool = Field(description="Whether a value is stored.")


class RemovedCredential(CredentialInfo):
    """A credential after `hz system credential remove`."""

    removed: bool = Field(description="Whether there was a value to remove.")
