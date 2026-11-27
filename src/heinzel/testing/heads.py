"""Exercise the installed head factories in-process with explicit test operations."""

import inspect
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from heinzel.kernel import Config, CurrentUser, Kernel, ModuleRecord, Principal, Settings
from heinzel.kernel._exposure import Exposed, command_words, exposed

type Head = Literal["cli", "api", "mcp", "web"]

HEADS: tuple[Head, ...] = ("cli", "api", "mcp", "web")


class OperationNotExposedError(LookupError):
    """The requested public operation is unavailable on this head."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """A head's native status, decoded payload and failure detail, if any."""

    status: int
    payload: object
    error_kind: Head | None = None
    detail: str = ""


class _MemoryUser(CurrentUser):
    """The actual head authentication context with a disposable in-memory owner."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.state_dir / "identity")
        self._principal = Principal(f"u-{uuid4().hex}")

    def owner(self) -> Principal:
        return self._principal


class FakeKernel(Kernel):
    """Operate on explicitly supplied records and fakes without discovery or user identity storage.

    Registration and invocation still use Kernel's validation and injection. Supply isolated settings and bind fakes for
    any external services the operations exercise.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        records: Sequence[ModuleRecord] = (),
        services: Mapping[type[object], object] | None = None,
    ) -> None:
        """Register only these module records, with no discovery or project configuration."""
        super().__init__(settings, records, config=Config.empty())
        self.bind(CurrentUser, _MemoryUser(settings))
        for kind, instance in (services or {}).items():
            self.bind(kind, instance)


def operate(head: Head, kernel: Kernel, operation: str, /, **arguments: object) -> Outcome:
    """Call a dotted operation path over the selected head's real local transport.

    Raises:
        OperationNotExposedError: If the head does not expose this operation.
    """
    path = tuple(operation.split("."))
    item = next((item for item in exposed(kernel, head) if item.path == path), None)
    if item is None:
        msg = f"{operation} is not exposed by {head}"
        raise OperationNotExposedError(msg)
    if head == "cli":
        return _cli(kernel, item, arguments)
    if head == "mcp":
        return _mcp(kernel, item, arguments)
    return _http(head, kernel, item, arguments)


def _cli(kernel: Kernel, item: Exposed, arguments: Mapping[str, object]) -> Outcome:
    from typer.testing import CliRunner  # noqa: PLC0415 - load only the selected transport

    from heinzel.heads.cli import build  # noqa: PLC0415 - load only the selected transport

    options = ["--json"]
    positionals: list[str] = []
    names = {parameter.name for parameter in item.parameters}
    for parameter in item.parameters:
        if parameter.name not in arguments:
            continue
        value = arguments[parameter.name]
        if parameter.default is inspect.Parameter.empty:
            positionals.extend(_wire_values(value))
        else:
            option = parameter.name.replace("_", "-")
            if isinstance(value, bool):
                options.append(f"--{option}" if value else f"--no-{option}")
            else:
                options.extend(f"--{option}={text}" for text in _wire_values(value))
    options.extend(f"--{name.replace('_', '-')}={value}" for name, value in arguments.items() if name not in names)
    if positionals:
        options.extend(("--", *positionals))
    result = CliRunner().invoke(build(kernel), [*command_words(item.path), *options])
    payload = json.loads(result.stdout) if result.stdout.strip() else None
    return Outcome(result.exit_code, payload, "cli" if result.exit_code else None, result.output)


def _wire_values(value: object) -> list[str]:
    if isinstance(value, (tuple, list)) and all(isinstance(part, (str, int, float, bool)) for part in value):
        return [str(part) for part in value]
    return [value if isinstance(value, str) else json.dumps(value)]


def _mcp(kernel: Kernel, item: Exposed, arguments: Mapping[str, object]) -> Outcome:
    import anyio  # noqa: PLC0415 - load only the selected transport
    from mcp import Client  # noqa: PLC0415 - load only the selected transport

    from heinzel.heads.mcp import build  # noqa: PLC0415 - load only the selected transport

    async def call() -> Outcome:
        async with Client(build(kernel)) as client:
            response = await client.call_tool(item.name, dict(arguments))
        return Outcome(
            int(response.is_error),
            response.structured_content,
            "mcp" if response.is_error else None,
            str(response.content) if response.is_error else "",
        )

    return anyio.run(call)


def _http(head: Literal["api", "web"], kernel: Kernel, item: Exposed, arguments: Mapping[str, object]) -> Outcome:
    from fastapi.testclient import TestClient  # noqa: PLC0415 - load only the selected transport

    if head == "web":
        from heinzel.heads.web import build  # noqa: PLC0415 - the API driver does not use the web head
        from heinzel.modules.system.client import Devices, McpTokens  # noqa: PLC0415 - web-only storage

        bearer = uuid4().hex
        directory = kernel.settings.state_dir / "web"
        app = build(
            kernel, port=None, token=bearer, static=None, devices=Devices(directory), mcp_tokens=McpTokens(directory)
        )
        path = "/api/" + "/".join(item.path)
        headers = {"Authorization": f"Bearer {bearer}"}
    else:
        from heinzel.heads.api import build as api  # noqa: PLC0415 - local in-process API only

        app = api(kernel)
        path = "/" + "/".join(item.path)
        headers = {}
    method = item.operation.http_method
    request_body = item.operation.private and method == "POST"
    with TestClient(app, base_url="http://localhost") as client:
        response = client.request(
            method,
            path,
            headers=headers,
            json=dict(arguments) if request_body else None,
            params=None if request_body else _query(arguments),
        )
    return Outcome(response.status_code, response.json(), head if response.is_error else None, response.text)


def _query(arguments: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple((name, text) for name, value in arguments.items() for text in _wire_values(value))
