"""Service of the connector module: each connector's state from its own checks, and its live test (ADR 0056)."""

import inspect
import re
import time
from collections.abc import Callable, Iterable

from heinzel import Check, HealthLet, HeinzelError, Kernel, Login, ModuleRecord, SetupLet, Status
from heinzel.kernel import CAPABILITIES, MODES, CapabilityView, ProviderLet, Requirement
from heinzel.modules.system.connector._models import (
    AdapterCandidate,
    CapabilityChoice,
    Connector,
    ConnectorCapability,
    ConnectorReport,
    ConnectorTest,
    State,
    Tool,
)

PROBLEMS = (Status.DEGRADED, Status.DOWN)
_BRANDS = {"github": "GitHub", "gitlab": "GitLab"}


def title_of(name: str, summary: str) -> str:
    """A module's name as its authors write it: the casing its summary uses (`GitHub`), a known brand, or `Zoom`."""
    words = name.rpartition(".")[2].replace("-", " ").replace("_", " ")
    found = re.search(rf"\b{re.escape(words)}\b", summary, re.IGNORECASE)
    if found and found[0] != found[0].lower():
        return found[0]
    return _BRANDS.get(words.lower(), words[:1].upper() + words[1:])


def login_of(checks: Iterable[Check]) -> Login | None:
    """The sign-in the checks found: the first that says there is none, else the first that says anything."""
    logins = [check.login for check in checks if check.login is not None]
    return next((login for login in logins if login.signed_in is False), logins[0] if logins else None)


def state_of(checks: list[Check], uses: list[ConnectorCapability]) -> State:
    """A connector's state: no sign-in beats serving nothing, which beats any other problem; then what the sign-in says.

    A connector without a sign-in of its own (one that reads through another capability) is `unknown` until tested.
    """
    login = login_of(checks)
    if login is not None and login.signed_in is False:
        return "not-signed-in"
    if not any(use.used for use in uses):
        return "not-bound"
    if any(check.status in PROBLEMS for check in checks):
        return "attention"
    return "connected" if login is not None and login.signed_in else "unknown"


def _run(kernel: Kernel, provider: Callable[..., list[Check]]) -> list[Check]:
    """A provider's checks; a provider that fails is a down check that says why, never a crash or a traceback."""
    try:
        return kernel.call(provider)
    except HeinzelError as exc:  # written for people: it says what to do
        return [Check(name="checks", status=Status.DOWN, detail=str(exc))]
    except Exception as exc:  # noqa: BLE001 - isolate plugins; their messages may carry what a terminal shouldn't show
        return [Check(name="checks", status=Status.DOWN, detail=f"the checks failed ({type(exc).__name__})")]


def _summary(method: Callable[..., object]) -> str:
    return (inspect.getdoc(method) or "").partition("\n")[0]


class ConnectorService:
    """Reports connectors from their checks and the capability registry; tests one live only when asked."""

    def __init__(self, kernel: Kernel, view: CapabilityView) -> None:
        """Work on the kernel and its read-only view of capabilities, injected."""
        self._kernel = kernel
        self._view = view

    def _records(self) -> dict[str, tuple[ModuleRecord, list[ProviderLet]]]:
        """Every module that provides adapters, with its adapters, by qualified name."""
        records = {record.qualname: record for record in self._kernel.modules}
        found: dict[str, tuple[ModuleRecord, list[ProviderLet]]] = {}
        for adapter in self._view.adapters:
            if (record := records.get(adapter.module)) is not None:
                found.setdefault(adapter.module, (record, []))[1].append(adapter.let)
        return found

    def _health(self, qualname: str, *, live: bool) -> list[Callable[..., list[Check]]]:
        return [
            let.provider
            for record, let in self._kernel.lets(HealthLet)
            if record.qualname == qualname and let.live is live and self._kernel.available(let.provider)
        ]

    def _connector(self, record: ModuleRecord, adapters: list[ProviderLet]) -> Connector:
        checks = [
            check for provider in self._health(record.qualname, live=False) for check in _run(self._kernel, provider)
        ]
        uses: list[ConnectorCapability] = []
        tools: list[Tool] = []
        for let in adapters:
            spec = CAPABILITIES[let.capability]
            bound, how = self._view.binding(let.capability)
            uses.append(
                ConnectorCapability(
                    capability=let.capability,
                    title=spec.title or let.capability,
                    adapter=let.adapter,
                    modes=[mode for mode in MODES if mode in let.modes],
                    used=bound == let.adapter,
                    bound=bound,
                    how=how,
                )
            )
            tools.extend(
                Tool(
                    capability=let.capability,
                    method=method,
                    mode=mode,
                    summary=_summary(getattr(spec.protocol, method)),
                )
                for method, mode in spec.methods.items()
                if mode in let.modes
            )
        setup = any(r.qualname == record.qualname for r, _ in self._kernel.lets(SetupLet))
        connect = list(dict.fromkeys(c.login.command for c in checks if c.login is not None and c.login.command))
        if not connect and setup:
            connect = [f"hz setup {record.qualname}"]
        return Connector(
            name=record.qualname,
            title=title_of(record.qualname, record.module.summary),
            summary=record.module.summary,
            distribution=record.origin.distribution,
            builtin=record.origin.builtin,
            state=state_of(checks, uses),
            login=login_of(checks),
            capabilities=uses,
            tools=tools,
            checks=checks,
            connect=connect,
            bind=[f"hz system config set {_key(use.capability)} {use.adapter}" for use in uses if not use.used],
            testable=bool(self._health(record.qualname, live=True)),
        )

    def report(self) -> ConnectorReport:
        """Every connector with its state, and every capability with who serves it and who could; nothing live."""
        connectors = [self._connector(record, adapters) for record, adapters in self._records().values()]
        by_name = {connector.name: connector for connector in connectors}
        choices: list[CapabilityChoice] = []
        for spec in CAPABILITIES.values():
            bound, how = self._view.binding(spec.name)
            # No modes: whether the chosen adapter is installed and unambiguous, whatever a requester needs.
            problem = self._view.serves(Requirement(spec.name, frozenset()))[1] if bound is not None else None
            candidates = [
                AdapterCandidate(
                    adapter=adapter.let.adapter,
                    connector=adapter.module,
                    title=by_name[adapter.module].title if adapter.module in by_name else adapter.let.adapter,
                    state=by_name[adapter.module].state if adapter.module in by_name else "unknown",
                )
                for adapter in self._view.adapters
                if adapter.let.capability == spec.name
            ]
            choices.append(
                CapabilityChoice(
                    capability=spec.name,
                    title=spec.title or spec.name,
                    purpose=spec.purpose,
                    key=_key(spec.name),
                    bound=bound,
                    how=how,
                    problem=problem,
                    candidates=candidates,
                    find=f"hz system plugin search --text {spec.name}",
                )
            )
        return ConnectorReport(
            connectors=sorted(connectors, key=lambda c: c.title.lower()),
            capabilities=sorted(choices, key=lambda c: c.title.lower()),
        )

    def test(self, name: str) -> ConnectorTest:
        """Run `name`'s live checks: one cheap read-only call through its tool, with the tool's short timeout.

        Raises:
            HeinzelError: If no connector has that name, or it has no live test.
        """
        records = self._records()
        if name not in records:
            known = ", ".join(sorted(records)) or "none is installed"
            msg = f"No connector is named {name!r}; the connectors are: {known}. See `hz system connector list`."
            raise HeinzelError(msg)
        providers = self._health(name, live=True)
        if not providers:
            msg = f"{name} has no live test; `hz system doctor` shows its offline checks."
            raise HeinzelError(msg)
        started = time.monotonic()
        checks = [check for provider in providers for check in _run(self._kernel, provider)]
        latency = round((time.monotonic() - started) * 1000)
        return ConnectorTest(
            name=name,
            passed=bool(checks) and all(check.status is Status.UP for check in checks),
            latency_ms=latency,
            login=login_of(checks),
            checks=checks,
        )


def _key(capability: str) -> str:
    return f"capabilities.{capability.replace('-', '_')}"
