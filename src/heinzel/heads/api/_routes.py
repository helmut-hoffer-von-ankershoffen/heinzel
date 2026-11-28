"""Bind exposed operations to ordinary and streaming REST routes."""

import inspect
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, get_args, get_origin

from anyio.lowlevel import checkpoint_if_cancelled
from fastapi import Body, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import ConfigDict, create_model
from pydantic.fields import FieldInfo
from starlette.types import Message

from heinzel.heads._exposure import Exposed
from heinzel.heads._presence import mark_authenticated_presence
from heinzel.heads._stream import request_person, streamed
from heinzel.kernel import Kernel
from heinzel.kernel.ai._agent_writes import calling

UNAVAILABLE = 503


def normal_body(item: Exposed) -> bool:
    """Use a normal JSON body for private POST operations."""
    return item.operation.private and item.operation.http_method == "POST"


def endpoint(
    kernel: Kernel, item: Exposed, *, body_parameters: list[inspect.Parameter] | None = None
) -> Callable[..., Any]:
    """Bind public parameters, keeping opted-in private input in a strict JSON body."""
    body = normal_body(item)

    def run(response: Response, **kwargs: Any) -> Any:  # noqa: ANN401 - whatever the operation returns
        mark_authenticated_presence(kernel, item.record.qualname)
        with calling(request_person(kernel)):
            result = kernel.invoke(item.operation, **arguments(item, kwargs, body=body))
        if item.operation.exit_code is not None and item.operation.exit_code(result):
            response.status_code = UNAVAILABLE
        return result

    response_parameter = inspect.Parameter("response", inspect.Parameter.KEYWORD_ONLY, annotation=Response)
    run.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [*(body_parameters if body_parameters is not None else parameters(item, body=body)), response_parameter],
        return_annotation=item.returns,
    )
    run.__name__ = item.name
    return run


def bounded_body_route(limit: int) -> type[APIRoute]:
    """Limit bytes as the router reads them, including chunked requests and JSON padding."""

    class BoundedBodyRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
            handler = super().get_route_handler()

            async def bounded(request: Request) -> Response:
                size = 0

                async def receive() -> Message:
                    nonlocal size
                    message = await request.receive()
                    if message["type"] == "http.request":
                        size += len(message.get("body", b""))
                        if size > limit:
                            raise HTTPException(status_code=413, detail="Request body too large.")
                    return message

                return await handler(Request(request.scope, receive=receive))

            return bounded

    return BoundedBodyRoute


def stream_endpoint(
    kernel: Kernel, item: Exposed, *, body_parameters: list[inspect.Parameter] | None = None
) -> Callable[..., Any]:
    """Like `endpoint`, but the answer is the operation's events as a server-sent event stream."""

    async def run(**kwargs: Any) -> Any:  # noqa: ANN401 - a streaming response
        # Once launched, the operation can outlive the HTTP stream. Respect cancellation before launching it.
        await checkpoint_if_cancelled()
        return streamed(
            kernel, item.operation, arguments(item, kwargs, body=item.operation.private), module=item.record.qualname
        )

    run.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        body_parameters if body_parameters is not None else parameters(item, body=item.operation.private)
    )
    run.__name__ = f"{item.name}_stream"
    return run


def parameters(item: Exposed, *, body: bool) -> list[inspect.Parameter]:
    """Describe the operation's query fields or strict JSON body to FastAPI."""
    if not body:
        return [query_parameter(parameter) for parameter in item.parameters]
    fields: dict[str, Any] = {  # runtime function annotations become Pydantic field definitions
        parameter.name: (
            parameter.annotation,
            ... if parameter.default is inspect.Parameter.empty else parameter.default,
        )
        for parameter in item.parameters
    }
    model = create_model(f"{item.name}_Input", __config__=ConfigDict(extra="forbid"), **fields)
    return [
        inspect.Parameter(
            "_input",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[model, Body()],
            default=inspect.Parameter.empty if item.parameters else model(),
        )
    ]


def arguments(item: Exposed, values: dict[str, Any], *, body: bool) -> dict[str, Any]:
    """Unpack the validated body before passing operation arguments to the kernel."""
    if not body:
        return values
    # FastAPI's strict model refuses unknown fields; the kernel still owns operation validation and enforcement.
    return {parameter.name: getattr(values["_input"], parameter.name) for parameter in item.parameters}


def query_parameter(parameter: inspect.Parameter) -> inspect.Parameter:
    """Carry an operation argument's annotations into its query field."""
    annotation, description = parameter.annotation, None
    if get_origin(annotation) is Annotated:
        annotation, *metadata = get_args(annotation)
        description = next((m.description for m in metadata if isinstance(m, FieldInfo)), None)
    return parameter.replace(
        annotation=Annotated[annotation, Query(description=description)], kind=inspect.Parameter.KEYWORD_ONLY
    )
