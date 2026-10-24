"""Models of the credential module."""

from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from heinzel import ModuleSettings

Backend = Literal["auto", "keyring", "environment"]


class CredentialSettings(ModuleSettings):
    """Settings of the credential module: the `[system.credential]` table or `HZ_SYSTEM_CREDENTIAL__*` variables."""

    section_title: ClassVar[str | None] = "Secrets"  # the heading in Settings

    backend: Backend = Field(
        default="auto",
        title="Where secrets are kept",
        description="Where Heinzelmännchen keeps passwords and tokens: the system keychain when "
        "there is one, any configured keyring, or read-only environment variables "
        "named HZ_CREDENTIAL_<SERVICE>_<NAME>.",
        json_schema_extra={
            "x-expert": True,
            "x-labels": {
                "auto": "Automatic: the system keychain if there is one",
                "keyring": "The configured keyring",
                "environment": "Environment variables (read-only)",
            },
        },
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
