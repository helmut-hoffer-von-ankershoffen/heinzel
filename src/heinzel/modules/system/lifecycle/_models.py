"""Private desktop deployment plans and authoritative activation records."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")]
Channel = Literal["stable", "preview", "development"]
Version = Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$")]


class Record(BaseModel):
    """Closed immutable JSON written only by the lifecycle authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True, serialize_by_alias=True)


class RuntimeManifest(Record):
    """Hash inventory of a staged runtime; its bytes define the deployment identity."""

    schema_version: StrictInt = Field(default=1, ge=1, le=1, alias="schema")
    core_version: Version
    python: str
    python_sha256: Digest
    ui_sha256: Digest
    files: dict[str, Digest] = Field(min_length=1, max_length=100_000)


class EnvironmentManifest(Record):
    """Complete private environment inventory plus retained installation intent, separate from resolved wheels."""

    schema_version: StrictInt = Field(default=1, ge=1, le=1, alias="schema")
    directory: str
    requested: dict[str, str] = Field(min_length=1, max_length=128)
    versions: dict[str, str] = Field(min_length=1, max_length=128)
    wheel_sha256: dict[str, Digest] = Field(min_length=1, max_length=128)
    files: dict[str, Digest] = Field(min_length=1, max_length=100_000)


class Activation(Record):
    """A fenced pointer to an installed deployment, independent of whether a backend is running."""

    schema_version: StrictInt = Field(default=1, ge=1, le=1, alias="schema")
    generation: StrictInt = Field(ge=1)
    deployment: Digest
    previous: Digest | None


class Transition(Record):
    """Durable intent before a pointer change; only an actual running receipt completes it."""

    schema_version: StrictInt = Field(default=1, ge=1, le=1, alias="schema")
    id: UUID
    before: Activation | None
    candidate: Digest | None
    phase: Literal["prepared", "switched", "verified"]
    action: Literal["install", "update", "uninstall"]
    generation: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def consistent_action(self) -> "Transition":
        """Refuse syntactically valid records whose action contradicts their persisted target.

        Raises:
            ValueError: If the record does not describe one coherent lifecycle change.
        """
        expected = "uninstall" if self.candidate is None else "update" if self.before else "install"
        if self.action != expected or (self.action == "uninstall" and self.before is None):
            msg = "A lifecycle transition must match its installed and candidate states"
            raise ValueError(msg)
        return self


class HighWater(Record):
    """The last allocated activation generation, retained after uninstall so stale writers never regain authority."""

    schema_version: StrictInt = Field(default=1, ge=1, le=1, alias="schema")
    generation: StrictInt = Field(ge=0)


class RuntimeProof(Record):
    """An authenticated observation of the owned worker, never an acknowledgement that merely changed disk state."""

    deployment: Digest
    generation: StrictInt = Field(ge=1)
    instance: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    version: Version
    ui_sha256: Digest


class CleanupTarget(Record):
    """One exact deployment or environment tree owned by an interrupted cleanup transaction."""

    parent: Literal["deployments", "environments"]
    name: str = Field(pattern=r"^(?:[a-f0-9]{64}|[a-f0-9]{32})$")
    files: dict[str, Digest] = Field(min_length=1, max_length=100_000)
    directories: tuple[str, ...] = Field(max_length=100_000)

    @model_validator(mode="after")
    def named_for_parent(self) -> "CleanupTarget":
        """Bind each opaque directory name to its one intended inventory parent.

        Raises:
            ValueError: If the identifier length does not match the inventory parent.
        """
        if len(self.name) != (64 if self.parent == "deployments" else 32):
            msg = "A cleanup target name does not match its inventory parent."
            raise ValueError(msg)
        return self


class RuntimeCleanup(Record):
    """Durable plan for idempotently removing only verified, inactive runtime payloads."""

    schema_version: StrictInt = Field(default=1, ge=1, le=1, alias="schema")
    id: UUID
    targets: tuple[CleanupTarget, ...] = Field(max_length=1024)

    @model_validator(mode="after")
    def unique_targets(self) -> "RuntimeCleanup":
        """Refuse a journal that assigns one owned tree more than once.

        Raises:
            ValueError: If any target directory is duplicated.
        """
        identities = [(target.parent, target.name) for target in self.targets]
        if len(set(identities)) != len(identities):
            msg = "A runtime cleanup journal cannot target one directory more than once."
            raise ValueError(msg)
        return self
