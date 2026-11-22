"""Authenticated, instance-bound proof that a replacement server loaded the expected configuration (the routes).

The models and the comparison live in the lifecycle context (`_verify`); this file is the web transport.
"""

import json
import secrets
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from heinzel import __version__
from heinzel.kernel import CurrentUser, HeinzelError, Kernel
from heinzel.modules.system.lifecycle._verify import (
    ExpectedLifecycle,
    Lifecycle,
    LifecycleVerified,
    Restart,
    loaded_settings,
    mismatches,
    requires_owner_session,
)

ACCEPTED = 202
CANT_RESTART = (
    "This server can't restart itself (it isn't `hz ui serve`, e.g. `make web-dev`); stop it and start it again."
)
MAX_VERIFY_BYTES = 64 * 1024


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
    app.state.lifecycle = lifecycle
    # ConfigService.report rereads files; only this startup snapshot proves what the new process actually loaded.
    loaded = loaded_settings(kernel)
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
        if requires_owner_session(expected):
            try:
                owner_session = kernel.resolve(CurrentUser).context().authentication == "web-session"
            except HeinzelError:
                owner_session = False
            if not owner_session:
                raise HTTPException(status_code=403, detail="Origin policy proof requires the owning web session.")
        mismatched = mismatches(expected, loaded, startup, origin_policy)
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
        "A server that isn't `hz ui serve` (`make web-dev`) answers 400.",
    )
