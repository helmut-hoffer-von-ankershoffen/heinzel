"""The REST head: every operation becomes an HTTP endpoint with an OpenAPI schema (served at `/docs`).

Read-only operations are `GET`, the others `POST`; parameters are query parameters described by their `Field`. A
`HeinzelError` becomes `400 {"error": ...}`; an operation whose exit code would be non-zero answers `503`, so
`GET /system/doctor` doubles as a health probe.
"""

import inspect
from collections.abc import Callable
from typing import Annotated, Any, get_args, get_origin

from fastapi import FastAPI, Query, Response
from fastapi.responses import JSONResponse
from pydantic.fields import FieldInfo

from heinzel import __version__
from heinzel.heads._exposure import Exposed, exposed
from heinzel.kernel import HeinzelError, Kernel, boot

HEAD = "api"
UNAVAILABLE = 503


def build(kernel: Kernel) -> FastAPI:
    """A FastAPI app with one endpoint per exposed operation."""
    app = FastAPI(
        title="🧝 Heinzelmännchen",
        version=__version__,
        description="Open-source helpers that keep product teams running. Every endpoint is also a `hz` command.",
    )

    @app.exception_handler(HeinzelError)
    async def heinzel_error(request: object, exc: HeinzelError) -> JSONResponse:  # noqa: ARG001 - FastAPI signature
        return JSONResponse(status_code=400, content={"error": str(exc)})

    for item in exposed(kernel, HEAD):
        app.add_api_route(
            "/" + "/".join(item.path),
            endpoint(kernel, item),
            methods=["GET" if item.operation.read_only else "POST"],
            summary=item.operation.summary,
            description=inspect.getdoc(item.operation.func) or "",
            response_model=item.returns if item.returns is not inspect.Signature.empty else None,
            tags=[item.record.path[0]],
            operation_id=item.name,
        )
    return app


def endpoint(kernel: Kernel, item: Exposed) -> Callable[..., Any]:
    """A function with the operation's public parameters as query parameters, run through the kernel."""

    def run(response: Response, **kwargs: Any) -> Any:  # noqa: ANN401 - whatever the operation returns
        result = kernel.invoke(item.operation, **kwargs)
        if item.operation.exit_code is not None and item.operation.exit_code(result):
            response.status_code = UNAVAILABLE
        return result

    response_parameter = inspect.Parameter("response", inspect.Parameter.KEYWORD_ONLY, annotation=Response)
    run.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [_query(p) for p in item.parameters] + [response_parameter], return_annotation=item.returns
    )
    run.__name__ = item.name
    return run


def _query(parameter: inspect.Parameter) -> inspect.Parameter:
    annotation, description = parameter.annotation, None
    if get_origin(annotation) is Annotated:
        annotation, *metadata = get_args(annotation)
        description = next((m.description for m in metadata if isinstance(m, FieldInfo)), None)
    return parameter.replace(
        annotation=Annotated[annotation, Query(description=description)], kind=inspect.Parameter.KEYWORD_ONLY
    )


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Boot the kernel and serve its operations over HTTP until interrupted."""
    import uvicorn  # noqa: PLC0415 - only needed when serving

    uvicorn.run(build(boot()), host=host, port=port)
