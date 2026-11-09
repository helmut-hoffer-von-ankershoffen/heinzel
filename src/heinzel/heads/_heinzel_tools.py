"""One private call path for the admitted MCP and local CLI adapters."""

import json
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from heinzel.kernel import HeinzelError, Kernel
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._heinzel_invocation import HeinzelInput, HeinzelInvocations, HeinzelTarget
from heinzel.kernel._logging import get_logger, step
from heinzel.kernel._session_store import SessionStore

type Disposition = Literal["queued", "running", "completed", "cancelled", "uncertain"]
type Boundary = Literal["reserve", "start", "complete"]
type CheckActive = Callable[[], None]
_REFUSED = "The reviewed Heinzel request is unavailable or its outcome is unresolved"
log = get_logger("mcp")


@dataclass(frozen=True, slots=True)
class HeinzelWork:
    """The exact durable request presented to the admitted broker's journal."""

    target: str
    operation: str
    request_id: str
    input_hash: str


class HeinzelTools:
    """Run only the frozen reviewed targets through the same broker for both heads.

    The trusted host supplies the grant-derived scope, private result root, and a record function bound
    to the current lease. Access establishes the owning kernel identity and returns a synchronous
    lease check, without holding the broker's revocation lock while external work runs.
    """

    def __init__(  # noqa: PLR0913 - separate host-provisioned capability inputs
        self,
        kernel: Kernel,
        targets: Iterable[HeinzelTarget],
        *,
        scope: str,
        root: Path,
        access: Callable[[], AbstractContextManager[CheckActive]],
        record: Callable[[HeinzelWork, Boundary], Disposition],
    ) -> None:
        self._kernel = kernel
        self._targets: dict[str, HeinzelTarget] = {}
        for target in targets:
            if target.tool_name in self._targets:
                raise HeinzelError(_REFUSED)
            self._targets[target.tool_name] = target
        self._scope = scope
        self._results = SessionStore(root)
        self._access = access
        self._record = record

    def targets(self) -> tuple[HeinzelTarget, ...]:
        """List only current reviewed targets, without discovering ambient personal data."""
        with self._access() as check_active:
            check_active()
            service = self._kernel.resolve(HeinzelInvocations)
            current: list[HeinzelTarget] = []
            for _, target in sorted(self._targets.items()):
                check_active()
                try:
                    service.require_current(target)
                except (HeinzelError, FileNotFoundError):
                    continue
                current.append(target)
            check_active()
            return tuple(current)

    def _result(self, key: str, digest: str, value: str | None = None) -> bytes:
        with self._results.writer(key, wait=True) as writer:
            snapshot = self._results.read(key)
            if snapshot.problem:
                raise HeinzelError(_REFUSED)
            if value is not None:
                if snapshot.events:
                    raise HeinzelError(_REFUSED)
                writer.append("mcp.result", key="result", data={"digest": digest, "json": value})
                return value.encode()
            if len(snapshot.events) != 1:
                raise HeinzelError(_REFUSED)
            event = snapshot.events[0]
            retained = event.data.get("json")
            if (
                event.kind != "mcp.result"
                or event.key != "result"
                or event.data.get("digest") != digest
                or not isinstance(retained, str)
            ):
                raise HeinzelError(_REFUSED)
            return retained.encode()

    def call(self, tool_name: str, request: HeinzelInput) -> bytes:
        """Retain exact result bytes once. An interrupted effect is never replayed.

        Raises:
            HeinzelError: If authority, input, target or the durable effect state is invalid.
        """
        target = self._targets.get(tool_name)
        if target is None or (target.kind == "personal") != (request.message is not None):
            raise HeinzelError(_REFUSED)
        if request.message is not None and not request.message.strip():
            raise HeinzelError(_REFUSED)
        digest = arguments_hash([target.source_digest, request.message])
        work = HeinzelWork(target.key, target.operation, request.request_id, digest)
        key = "heinzel-" + arguments_hash([self._scope, target.key, request.request_id])
        with (
            self._access() as check_active,
            step(log, "mcp.heinzel.invoke", operation=target.operation, step="invoke"),
        ):
            service = self._kernel.resolve(HeinzelInvocations)
            check_active()
            try:
                service.require_current(target)
            except (HeinzelError, OSError):
                raise HeinzelError(_REFUSED) from None
            disposition = self._record(work, "reserve")
            if disposition == "completed":
                retained = self._result(key, digest)
                if self._record(work, "reserve") != "completed":
                    raise HeinzelError(_REFUSED)
                return retained
            if disposition != "queued" or self._record(work, "start") != "running":
                raise HeinzelError(_REFUSED)
            scoped_request = request.model_copy(update={"request_id": key})
            try:
                response = service.execute(target, scoped_request, check_active)
            except (HeinzelError, OSError):
                raise HeinzelError(_REFUSED) from None
            check_active()
            value = json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
            result = self._result(key, digest, value)
            if self._record(work, "complete") != "completed":
                raise HeinzelError(_REFUSED)
            return result
