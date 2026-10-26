"""Authenticated, instance-bound proof that a replacement server loaded the expected configuration."""

import json
import re
import secrets
from collections.abc import Callable
from typing import cast

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from pydantic.errors import PydanticInvalidForJsonSchema, PydanticUserError

from heinzel import __version__
from heinzel.kernel import CurrentUser, HeinzelError, Kernel, Settings

ACCEPTED = 202
CANT_RESTART = "This server can't restart itself (it isn't `hz ui`, e.g. `make web-dev`); stop it and start it again."
MAX_VERIFY_BYTES = 64 * 1024
_ORIGIN_POLICY = ("hz", "ui_allowed_origins")
_SECRET = re.compile(
    r"(?:^|_)(?:token|password|passphrase|secret|api_key|credential|private_key)(?:$|_)", re.IGNORECASE
)


class Restart(BaseModel):
    """The retiring server's acknowledgement; a replacement instance must still be verified."""

    restarting: bool = Field(description="Always true: wait for a different /lifecycle instance.")
    message: str = Field(description="What happens, for the person waiting.")
    instance: str = Field(description="The retiring server instance; an answer from it cannot prove a restart.")


class Lifecycle(BaseModel):
    """Facts about code actually loaded by this UI server, independent of the installation on disk."""

    instance: str = Field(description="An opaque identifier generated for this server instance.")
    version: str = Field(description="The hz code version loaded by this running server.")
    restartable: bool = Field(description="Whether this server can restart itself using the existing socket handoff.")


class ExpectedSetting(BaseModel):
    """A value the person expects this server to have loaded, never returned as configuration data."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    section: str = Field(min_length=1, max_length=200, description="The setting's owning section.")
    key: str = Field(min_length=1, max_length=200, description="The setting name within its section.")
    value: JsonValue = Field(description="The exact JSON value that must have been loaded.")


class ExpectedLifecycle(BaseModel):
    """The exact process, code and selected settings whose loaded state must agree."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    instance: str = Field(min_length=1, max_length=128, description="The replacement instance being verified.")
    version: str = Field(min_length=1, max_length=128, description="The expected loaded code version.")
    settings: list[ExpectedSetting] = Field(max_length=100, description="Only the target settings being applied.")


class LifecycleVerified(BaseModel):
    """Whether this exact server loaded the requested code and configuration; no raw loaded values are exposed."""

    instance: str = Field(description="The instance that answered this verification.")
    version: str = Field(description="The code version loaded by the answering instance.")
    matches: bool = Field(description="Whether instance, version and every expected loaded setting agree.")
    mismatched: list[str] = Field(description="Names of unknown, duplicated or differently loaded settings.")


def _safe_schema(node: object, definitions: dict[str, object], seen: frozenset[str] = frozenset()) -> bool:
    """Fail closed for private fields, secrets, nested secrets and unresolved or recursive schema references."""
    if isinstance(node, list):
        return all(_safe_schema(child, definitions, seen) for child in cast("list[object]", node))
    if not isinstance(node, dict):
        return True
    table = cast("dict[str, object]", node)
    if table.get("x-private") or table.get("writeOnly") or table.get("format") == "password":
        return False
    if "$ref" in table:
        reference = table["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/$defs/") or reference in seen:
            return False
        name = reference.removeprefix("#/$defs/")
        if name not in definitions or not _safe_schema(definitions[name], definitions, seen | {reference}):
            return False
    properties = table.get("properties", {})
    return not (
        isinstance(properties, dict) and any(_SECRET.search(key) for key in cast("dict[str, object]", properties))
    ) and all(_safe_schema(child, definitions, seen) for key, child in table.items() if key != "$ref")


def _loaded_settings(kernel: Kernel) -> dict[tuple[str, str], str]:
    """Capture only safe declared fields; a private or secret field never becomes an equality oracle."""
    loaded: dict[tuple[str, str], str] = {}
    for section, settings in kernel.settings_of():
        try:
            schema = type(settings).model_json_schema(by_alias=False)
        except (PydanticInvalidForJsonSchema, PydanticUserError):
            continue
        definitions = schema.get("$defs", {})
        properties = schema.get("properties", {})
        for key in type(settings).model_fields:
            if _SECRET.search(key) or key not in properties or not _safe_schema(properties[key], definitions):
                continue
            try:
                values = settings.model_dump(mode="json", include={key})
                if key in values:
                    loaded[section, key] = json.dumps(values[key], sort_keys=True, allow_nan=False)
            except ValueError:
                continue  # A plugin's non-JSON setting cannot be proven by a JSON expectation.
    return loaded


def _expected_origins(value: JsonValue, startup: Settings) -> frozenset[str] | None:
    """Reuse the core origin grammar for arrays without letting process environment replace an expectation."""
    if not isinstance(value, list) or not all(isinstance(origin, str) for origin in value):
        return None
    try:
        # Every other field is explicitly supplied from startup, so unrelated runtime env cannot influence proof.
        return Settings.model_validate(startup.model_dump() | {"ui_allowed_origins": value}).ui_allowed_origins
    except ValidationError:
        return None  # Do not reflect invalid guesses or their values in the verification response.


async def _expectation(request: Request) -> ExpectedLifecycle:
    """Bound streamed bodies before JSON parsing; authentication runs before this route is entered.

    Raises:
        HTTPException: If the body exceeds the verification budget.
        RequestValidationError: If the body does not describe a bounded JSON expectation.
    """
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_VERIFY_BYTES:
            raise HTTPException(status_code=413, detail="Restart verification is limited to 64 KiB.")
        body.extend(chunk)
    try:
        expected = ExpectedLifecycle.model_validate_json(body)
        # JsonValue's recursive float schema accepts non-finite numbers independently of the model config.
        for setting in expected.settings:
            json.dumps(setting.value, allow_nan=False)
    except ValidationError as error:
        # Input values are never reflected in validation errors: settings can include a rejected secret guess.
        raise RequestValidationError(error.errors(include_input=False, include_context=False)) from error
    except ValueError as error:
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("body",), "msg": "Settings must contain only finite JSON values."}]
        ) from error
    else:
        return expected


def add_lifecycle(app: FastAPI, kernel: Kernel, restart: Callable[[], None] | None) -> None:
    """Add transport lifecycle routes to an API whose shared middleware already enforces authentication."""
    lifecycle = Lifecycle(instance=secrets.token_hex(16), version=__version__, restartable=restart is not None)
    # ConfigService.report rereads files; only this startup snapshot proves what the new process actually loaded.
    loaded = _loaded_settings(kernel)
    startup = kernel.settings
    origin_policy = frozenset(startup.ui_allowed_origins)

    def status(response: Response) -> Lifecycle:
        response.headers["Cache-Control"] = "no-store"
        return lifecycle

    app.add_api_route(
        "/lifecycle",
        status,
        methods=["GET"],
        response_model=Lifecycle,
        tags=["web"],
        operation_id="web_lifecycle",
        summary="Read this UI server's instance, loaded version and restart capability.",
    )

    async def verify(request: Request, response: Response) -> LifecycleVerified:
        expected = await _expectation(request)
        response.headers["Cache-Control"] = "no-store"
        if any((setting.section, setting.key) == _ORIGIN_POLICY for setting in expected.settings):
            try:
                owner_session = kernel.resolve(CurrentUser).context().authentication == "web-session"
            except HeinzelError:
                owner_session = False
            if not owner_session:
                raise HTTPException(status_code=403, detail="Origin policy proof requires the owning web session.")
        seen: set[tuple[str, str]] = set()
        mismatched: list[str] = []
        for setting in expected.settings:
            key = setting.section, setting.key
            agrees = (
                _expected_origins(setting.value, startup) == origin_policy
                if key == _ORIGIN_POLICY
                else loaded.get(key) == json.dumps(setting.value, sort_keys=True, allow_nan=False)
            )
            if key in seen or not agrees:
                mismatched.append(f"{setting.section}.{setting.key}")
            seen.add(key)
        return LifecycleVerified(
            instance=lifecycle.instance,
            version=lifecycle.version,
            matches=expected.instance == lifecycle.instance
            and expected.version == lifecycle.version
            and not mismatched,
            mismatched=mismatched,
        )

    app.add_api_route(
        "/lifecycle/verify",
        verify,
        methods=["POST"],
        response_model=LifecycleVerified,
        tags=["web"],
        operation_id="web_lifecycle_verify",
        summary="Verify expected settings against this instance's startup snapshot without exposing their values.",
        responses={
            413: {"description": "The streamed JSON body exceeds 64 KiB."},
            422: {"description": "The body does not contain bounded, finite JSON expectations."},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["instance", "version", "settings"],
                            "properties": {
                                "instance": {"type": "string", "minLength": 1, "maxLength": 128},
                                "version": {"type": "string", "minLength": 1, "maxLength": 128},
                                "settings": {
                                    "type": "array",
                                    "maxItems": 100,
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["section", "key", "value"],
                                        "properties": {
                                            "section": {"type": "string", "minLength": 1, "maxLength": 200},
                                            "key": {"type": "string", "minLength": 1, "maxLength": 200},
                                            "value": {},
                                        },
                                    },
                                },
                            },
                        }
                    }
                },
            }
        },
    )

    def restart_server(response: Response) -> Restart:
        if restart is None:
            raise HeinzelError(CANT_RESTART)
        restart()
        response.status_code = ACCEPTED
        return Restart(
            restarting=True,
            message="Restarting hz ui to load what changed; it answers again in a few seconds.",
            instance=lifecycle.instance,
        )

    app.add_api_route(
        "/restart",
        restart_server,
        methods=["POST"],
        response_model=Restart,
        status_code=ACCEPTED,
        tags=["web"],
        operation_id="web_restart",
        summary="Restart hz ui to load changes a running server can't, such as an added plugin.",
        description="The process runs `hz` again with the same port and token. Poll /lifecycle until its instance "
        "differs from the retiring instance in this reply, then verify the loaded version and target settings. "
        "A server that isn't `hz ui` (`make web-dev`) answers 400.",
    )
