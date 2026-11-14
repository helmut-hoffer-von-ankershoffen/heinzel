"""The conformance kit: check a plugin's adapters against Core's capability contracts in the plugin's own CI (ADR 0159).

A private plugin can't be tested by Core. This kit runs in the plugin's own test suite instead, against the Core it
pins, and checks what Core relies on, for every adapter a module provides:

- **Shape.** Every method of the modes the adapter declares exists and takes the protocol's parameters, with the
  same defaults, unless the adapter lists it in `unsupported`. An `unsupported` entry names a real method.
- **Modes and hosts.** Every declared mode has a method the adapter supports. The adapter names the hosts it sends
  data to, or a function of its settings that does.
- **Read calls.** Each read method that needs no arguments, or for which the test passes sample arguments, is
  called once, with a time limit. It returns what the protocol says, or raises `NotSupportedError` naming its own
  capability and method. Meanwhile it must not write: calling one of the adapter's write methods, or a write tool of
  an `McpConnector`, fails the check, and so does a change the test's own `writes` counter sees. Write methods are
  never called.
- **AI models** (`ai-models`, interface `AI_MODELS_INTERFACE`). `models()` answers within Core's time limit with valid
  `ModelOffer`s, unique ids and at most one offer per `level`; `model(id)` returns a `pydantic_ai.models.Model`; an
  unknown or unavailable id is refused. With `live_request=True` one request through the model must answer.
- **Sign-in and health** (ADR 0055, 0136). A `@module.sign_in` provider's `status(live=False)` answers quickly and
  without the network, with a valid `SignInStatus` naming every declared grant and an account that isn't a token.
  Offline `@module.health` checks run without the network and return `Check`s. Nothing live is called.

In a plugin's tests, through the `hz_conformance` fixture (`heinzel.testing`, loaded by pytest automatically):

```python
import pytest

from acme_plugin import module

pytestmark = pytest.mark.integration


def test_conforms(hz_conformance):
    hz_conformance(module, samples={"tracker": {"issues_updated": {"window": "7d"}}})
```

A failure lists every problem at once, each with what the contract says. Warnings (`ConformanceWarning`) name what
Core tolerates but you should fix. Works on Python 3.12 and later.
"""

import asyncio
import concurrent.futures
import contextlib
import contextvars
import importlib.metadata
import inspect
import re
import socket
import threading
import typing
import warnings
from collections.abc import Callable, Generator, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Self

if TYPE_CHECKING:
    from heinzel.kernel import Capability, Kernel, Module, ProviderLet

__all__ = [
    "CALL_TIMEOUT",
    "OFFLINE_TIMEOUT",
    "UNKNOWN_MODEL_ID",
    "ConformanceError",
    "ConformanceWarning",
    "Finding",
    "Report",
    "assert_conformant",
    "check_adapter",
    "check_module",
    "offline",
]

reading: ContextVar[str | None] = ContextVar("heinzel_conformance_reading", default=None)
"""The read method the kit is calling now, as `capability.method`; while set, `McpConnector` refuses write tools."""

UNKNOWN_MODEL_ID = "hz-conformance/no-such-model"
"""The id the kit asks `model()` for, which no plugin offers."""

CALL_TIMEOUT = 10.0
"""Seconds a sample read call, building a model or one live request may take."""

Severity = Literal["fail", "warn"]


class ConformanceError(AssertionError):
    """An adapter breaks its capability's contract; the message lists every problem found."""


class ConformanceWarning(UserWarning):
    """Something Core tolerates but the plugin should fix, such as one invalid model offer Core skips."""


@dataclass(frozen=True, slots=True)
class Finding:
    """One problem with one adapter: where, which check, what is wrong and what the contract says."""

    adapter: str
    """`<name> (<capability>)`, e.g. `acme (ai-models)`."""
    check: str
    """`build`, `shape`, `unsupported`, `modes`, `hosts`, `call`, `read-only`, `models`, `model` or `request`."""
    message: str
    severity: Severity = "fail"

    def __str__(self) -> str:
        """`acme (ai-models) models: ...`."""
        return f"{self.adapter} {self.check}: {self.message}"


@dataclass
class Report:
    """What the kit checked and found, with the versions it checked against."""

    checked: list[str] = field(default_factory=list[str])
    findings: list[Finding] = field(default_factory=list[Finding])
    skipped: list[str] = field(default_factory=list[str])
    """Read methods not called, because they need arguments the test didn't pass."""
    versions: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def failures(self) -> list[Finding]:
        """The findings that fail the check."""
        return [f for f in self.findings if f.severity == "fail"]

    @property
    def warnings(self) -> list[Finding]:
        """The findings Core tolerates."""
        return [f for f in self.findings if f.severity == "warn"]

    def extend(self, other: "Report") -> None:
        """Add another report's adapters and findings to this one."""
        self.checked += other.checked
        self.findings += other.findings
        self.skipped += other.skipped
        self.versions = self.versions or other.versions

    def __str__(self) -> str:
        """A plain report: versions, adapters, failures, warnings and calls skipped."""
        versions = ", ".join(f"{name} {version}" for name, version in self.versions.items())
        lines = [f"Conformance against {versions}", f"Checked: {', '.join(self.checked) or 'no adapter'}"]
        for title, found in (("Failures", self.failures), ("Warnings", self.warnings)):
            if found:
                lines += [f"{title}:", *(f"  - {finding}" for finding in found)]
        if self.skipped:
            lines.append(f"Not called (pass sample arguments in `samples`): {', '.join(self.skipped)}")
        return "\n".join(lines)

    def raise_for_failures(self) -> None:
        """Emit each warning as a `ConformanceWarning`, then raise when anything failed.

        Raises:
            ConformanceError: Listing every failure, warning and versions.
        """
        for finding in self.warnings:
            warnings.warn(str(finding), ConformanceWarning, stacklevel=3)
        if self.failures:
            raise ConformanceError(str(self))


def versions() -> dict[str, str]:
    """The versions a check ran against: Core, the `ai-models` interface, Pydantic AI and httpx2."""
    from heinzel.kernel._ai_models import AI_MODELS_INTERFACE  # noqa: PLC0415 - cheap import of this module

    found = {"heinzel": _version("heinzel"), "ai-models interface": AI_MODELS_INTERFACE}
    found["pydantic-ai"] = _version("pydantic-ai-slim")
    found["httpx2"] = _version("httpx2")
    return found


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def check_module(  # noqa: PLR0913 - the module and the check's knobs, all keyword-only
    module: "Module",
    *,
    kernel: "Kernel | None" = None,
    samples: Mapping[str, Mapping[str, Mapping[str, object]]] | None = None,
    writes: Callable[[], int] | None = None,
    live_request: bool = False,
    timeout: float = CALL_TIMEOUT,
) -> Report:
    """Check every adapter `module` provides, built as hz builds it.

    Args:
        module: The plugin's `Module`.
        kernel: Builds each adapter with its settings and credentials injected, and evaluates a hosts function; the
            `hz_kernel` fixture is one. Without it, each factory is called without arguments.
        samples: Arguments for read calls, by capability and method: `{"mail": {"messages": {"query": "", ...}}}`.
        writes: Counts the writes your fake backend saw; a read call must not change it.
        live_request: Send one request through each `ai-models` adapter's first model.
        timeout: Seconds one call may take.
    """
    from heinzel.kernel import HealthLet, ProviderLet, SignInLet  # noqa: PLC0415 - imported when used

    report = Report(versions=versions())
    providers = [let for let in module.lets if isinstance(let, ProviderLet)]
    sign_ins = [let for let in module.lets if isinstance(let, SignInLet)]
    health = [let for let in module.lets if isinstance(let, HealthLet) and not let.live]
    if not (providers or sign_ins or health):
        report.findings.append(Finding(module.name, "build", f"module {module.name} provides no adapter"))
    for let in providers:
        label = f"{let.adapter} ({let.capability})"
        report.findings += _hosts(let, label, kernel)
        adapter, problem = _build(let.factory, label, kernel)
        if problem is not None:
            report.checked.append(label)
            report.findings.append(problem)
            continue
        report.extend(
            check_adapter(
                adapter,
                let.capability,
                modes=let.modes,
                name=let.adapter,
                samples=(samples or {}).get(let.capability),
                writes=writes,
                live_request=live_request,
                timeout=timeout,
            )
        )
    for sign_in in sign_ins:
        label = f"{module.name} (sign-in)"
        report.checked.append(label)
        provider, problem = _build(sign_in.factory, label, kernel)
        report.findings += [problem] if problem is not None else _sign_in_status(provider, sign_in.grants, label)
    for checks in health:
        label = f"{module.name} (health)"
        report.checked.append(label)
        report.findings += _health(checks.provider, label, kernel)
    return report


def _build(factory: Callable[..., object], label: str, kernel: "Kernel | None") -> tuple[Any, Finding | None]:
    """Build an adapter or provider as hz does; a factory that fails is a finding, not a crash of the kit."""
    try:
        return (kernel.call(factory) if kernel is not None else factory()), None
    except Exception as exc:  # noqa: BLE001 - reported as a finding
        hint = "" if kernel is not None else " Pass kernel= (the hz_kernel fixture) to inject settings."
        return None, Finding(label, "build", f"couldn't build it: {_detail(exc)}.{hint}")


def check_adapter(  # noqa: PLR0913 - the adapter, its declaration and the check's knobs, all keyword-only
    adapter: object,
    capability: str,
    *,
    modes: Iterable[str] = ("read",),
    name: str = "",
    samples: Mapping[str, Mapping[str, object]] | None = None,
    writes: Callable[[], int] | None = None,
    live_request: bool = False,
    timeout: float = CALL_TIMEOUT,
) -> Report:
    """Check one built adapter of `capability` serving `modes`; `samples` holds read call arguments by method.

    Raises:
        ValueError: If `capability` is not one Core defines.
    """
    from heinzel.kernel import CAPABILITIES  # noqa: PLC0415 - imported when used, see heinzel.testing

    spec = CAPABILITIES.get(capability)
    if spec is None:
        msg = f"Unknown capability {capability!r}; known: {', '.join(sorted(CAPABILITIES))}"
        raise ValueError(msg)
    label = f"{name or type(adapter).__name__} ({capability})"
    report = Report(checked=[label], versions=versions())
    served = frozenset(modes)
    report.findings += _shape(adapter, spec, served, label)
    if capability == "ai-models":
        report.findings += _ai_models(adapter, label, live_request=live_request, timeout=timeout)
        return report
    from heinzel.kernel._capabilities import declared_unsupported  # noqa: PLC0415

    skip = declared_unsupported(adapter, spec)
    for method, mode in spec.methods.items():
        if mode != "read" or "read" not in served or method in skip or not callable(getattr(adapter, method, None)):
            continue
        arguments = (samples or {}).get(method)
        if arguments is None and _required(getattr(adapter, method)):
            report.skipped.append(f"{capability}.{method}")
            continue
        report.findings += _read_call(adapter, spec, method, dict(arguments or {}), label, writes, timeout)
    return report


def assert_conformant(
    target: object,
    capability: str | None = None,
    *,
    kernel: "Kernel | None" = None,
    **options: Any,  # noqa: ANN401 - those of check_module or check_adapter
) -> Report:
    """Check a `Module` (every adapter it provides) or one built adapter of `capability`, and raise on failures.

    `options` are those of `check_module` or `check_adapter`. Returns the report, so a test can print it.

    Raises:
        ConformanceError: Listing every failure.
        ValueError: If `target` is an adapter and `capability` is missing.
    """
    from heinzel.kernel import Module  # noqa: PLC0415 - imported when used, see heinzel.testing

    if isinstance(target, Module):
        report = check_module(target, kernel=kernel, **options)
    elif capability is None:
        msg = "assert_conformant(adapter, capability): name the capability an adapter instance implements"
        raise ValueError(msg)
    else:
        report = check_adapter(target, capability, **options)
    report.raise_for_failures()
    return report


# --- shape, modes, unsupported


def parameters(function: Callable[..., object]) -> list[str]:
    """A method's parameters but `self`, as `name` or `name=default`, so defaults are compared too."""
    return [
        p.name if p.default is inspect.Parameter.empty else f"{p.name}={p.default!r}"
        for p in inspect.signature(function).parameters.values()
        if p.name != "self"
    ]


def _shape(adapter: object, spec: "Capability", served: frozenset[str], label: str) -> list[Finding]:
    from heinzel.kernel._capabilities import declared_unsupported  # noqa: PLC0415

    findings: list[Finding] = []
    declared: object = getattr(adapter, "unsupported", ())
    if isinstance(declared, str | bytes) or not isinstance(declared, Iterable):
        findings.append(
            Finding(label, "unsupported", "`unsupported` must be a set of method names, e.g. frozenset({'attach'})")
        )
    elif unknown := sorted(str(n) for n in typing.cast("Iterable[object]", declared) if n not in spec.methods):
        findings.append(
            Finding(label, "unsupported", f"names no method of {spec.name}: {', '.join(unknown)} (a typo?)")
        )
    skip = declared_unsupported(adapter, spec)
    findings.extend(
        Finding(label, "modes", f"declares mode {mode} but lists every {mode} method as unsupported; drop it")
        for mode in sorted(served)
        if mode in spec.modes and all(m in skip for m, m_mode in spec.methods.items() if m_mode == mode)
    )
    for method, mode in spec.methods.items():
        if mode not in served or method in skip:
            continue
        found = getattr(adapter, method, None)
        if not callable(found):
            findings.append(
                Finding(
                    label,
                    "shape",
                    f"{method} is missing (mode {mode}); implement it, raise NotSupportedError from it, or list it "
                    "in `unsupported`",
                )
            )
            continue
        expected, actual = parameters(getattr(spec.protocol, method)), parameters(found)
        if actual != expected:
            findings.append(
                Finding(
                    label,
                    "shape",
                    f"{method} takes ({', '.join(actual)}), the protocol {spec.protocol.__name__} takes "
                    f"({', '.join(expected)})",
                )
            )
    return findings


def _hosts(let: "ProviderLet", label: str, kernel: "Kernel | None") -> list[Finding]:
    from heinzel.kernel._lets import host_of  # noqa: PLC0415

    if let.hosts:
        return []
    if let.configured_hosts is None:
        return [
            Finding(
                label,
                "hosts",
                "declares no hosts: list the hosts it sends data to in provides(hosts=[...]), or a function of its "
                "settings; people see them in `hz system capability list`",
                "warn",
            )
        ]
    if kernel is None:
        return []
    try:
        hosts = [host_of(str(host)) for host in kernel.call(let.configured_hosts)]
    except Exception as exc:  # noqa: BLE001 - a failing hosts function is a finding
        return [Finding(label, "hosts", f"its hosts function failed with default settings: {_detail(exc)}")]
    if not hosts or None in hosts:
        return [Finding(label, "hosts", "its hosts function returned no valid host name or URL with default settings")]
    return []


# --- read calls


@dataclass
class _Tripwire:
    """Replaces an adapter's write methods during a read call and records any call."""

    adapter: object
    names: list[str]
    called: list[str] = field(default_factory=list[str])
    _set: list[str] = field(default_factory=list[str])

    def __enter__(self) -> Self:
        for name in self.names:
            try:
                setattr(self.adapter, name, self._wire(name))
            except (AttributeError, TypeError):  # slots or a frozen dataclass: the MCP and counter checks remain
                continue
            self._set.append(name)
        return self

    def _wire(self, name: str) -> Callable[..., object]:
        def wire(*_: object, **__: object) -> object:
            self.called.append(name)
            msg = f"conformance check: write method {name} called during a read call"
            raise RuntimeError(msg)

        return wire

    def __exit__(self, *_: object) -> None:
        for name in self._set:
            delattr(self.adapter, name)


def _read_call(  # noqa: PLR0913, PLR0917 - one call's context
    adapter: object,
    spec: "Capability",
    method: str,
    arguments: dict[str, object],
    label: str,
    writes: Callable[[], int] | None,
    timeout: float,
) -> list[Finding]:
    from heinzel.kernel import NotSupportedError  # noqa: PLC0415

    writers = [m for m, mode in spec.methods.items() if mode == "write" and callable(getattr(adapter, m, None))]
    before = writes() if writes is not None else 0
    with _Tripwire(adapter, writers) as tripwire:
        outcome, value = _timed(getattr(adapter, method), arguments, timeout, read=f"{spec.name}.{method}")
    findings: list[Finding] = []
    if tripwire.called:
        findings.append(
            Finding(label, "read-only", f"read method {method} called {', '.join(tripwire.called)}, which writes")
        )
    if writes is not None and writes() != before:
        findings.append(Finding(label, "read-only", f"read method {method} wrote: your `writes` counter changed"))
    if outcome == "timeout":
        return [*findings, Finding(label, "call", f"{method} didn't answer within {timeout:g}s")]
    if outcome == "error":
        if isinstance(value, NotSupportedError):
            if (value.capability, value.method) != (spec.name, method):
                findings.append(
                    Finding(
                        label,
                        "unsupported",
                        f"{method} raised NotSupportedError({value.capability!r}, {value.method!r}); it must name its "
                        f"own capability and method: NotSupportedError({spec.name!r}, {method!r}, reason)",
                    )
                )
            return findings
        if not tripwire.called:
            findings.append(Finding(label, "call", f"{method}(**{arguments!r}) failed: {_detail(value)}"))
        return findings
    if problem := _wrong_type(getattr(spec.protocol, method), value):
        findings.append(Finding(label, "call", f"{method} returned {problem}"))
    return findings


def _timed(
    func: Callable[..., object], arguments: Mapping[str, object], timeout: float, *, read: str | None = None
) -> tuple[Literal["ok", "error", "timeout"], Any]:
    """Call `func` on a thread of its own with `timeout`; `read` marks it as that read call meanwhile."""

    def run() -> object:
        if read is not None:
            reading.set(read)
        return func(**arguments)

    # A daemon thread, not an executor: a call that never returns must not keep the test process from exiting.
    future: concurrent.futures.Future[object] = concurrent.futures.Future()
    context = contextvars.copy_context()

    def work() -> None:
        try:
            future.set_result(context.run(run))
        except BaseException as exc:  # noqa: BLE001 - handed to the caller through the future
            future.set_exception(exc)

    threading.Thread(target=work, name="hz-conformance", daemon=True).start()
    try:
        return "ok", future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        return "timeout", None
    except Exception as exc:  # noqa: BLE001 - the caller judges the error
        return "error", exc


def _required(method: Callable[..., object]) -> bool:
    return any(
        p.default is inspect.Parameter.empty and p.kind not in {p.VAR_POSITIONAL, p.VAR_KEYWORD}
        for p in inspect.signature(method).parameters.values()
    )


def _wrong_type(protocol_method: Callable[..., object], value: object) -> str:
    """Empty when `value` is what the protocol method returns; else what it is instead."""
    from pydantic import TypeAdapter, ValidationError  # noqa: PLC0415

    try:
        hint = typing.get_type_hints(protocol_method).get("return", Any)
    except (NameError, TypeError):
        return ""
    if hint is Any:
        return ""
    try:
        TypeAdapter(hint).validate_python(value, strict=True)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ())) or "the value"
        return f"{type(value).__name__}, not {_hint_name(hint)} ({where}: {first.get('msg', '')})"
    except Exception:  # noqa: BLE001 - a hint pydantic can't check is left alone
        return ""
    return ""


def _hint_name(hint: object) -> str:
    return re.sub(r"\b[\w.]+\.(\w+)", r"\1", str(hint)) if not isinstance(hint, type) else hint.__name__


def _detail(exc: BaseException) -> str:
    from heinzel.kernel import clean_text  # noqa: PLC0415

    return clean_text(f"{type(exc).__name__}: {exc}")[:300]


# --- ai-models


def _says(method: str, word: str) -> str:
    """The sentence of the `AiModels.<method>` docstring containing `word`, quoted, to show what the contract says."""
    from heinzel.kernel import AiModels  # noqa: PLC0415

    doc = " ".join((inspect.getdoc(getattr(AiModels, method)) or "").split())
    sentence = next((s for s in re.split(r"(?<=\.)\s+", doc) if word in s), "")
    return f' The contract says: "{sentence}"' if sentence else ""


def _ai_models(adapter: object, label: str, *, live_request: bool, timeout: float) -> list[Finding]:
    from heinzel.kernel._ai_models import LIST_TIMEOUT  # noqa: PLC0415

    findings = [
        Finding(label, "shape", f"`{attribute}` must be a plain str")
        for attribute in ("title", "summary")
        if hasattr(adapter, attribute) and not isinstance(getattr(adapter, attribute), str)
    ]
    test = getattr(adapter, "test", None)
    if test is not None and (not callable(test) or parameters(test) != ["model_id"]):
        findings.append(Finding(label, "shape", "the optional `test` must be a method test(self, model_id: str)"))
    outcome, found = _timed(getattr(adapter, "models", _missing), {}, LIST_TIMEOUT)
    if outcome == "timeout":
        return [
            *findings,
            Finding(label, "models", f"models() took longer than {LIST_TIMEOUT:g}s.{_says('models', 'cache')}"),
        ]
    if outcome == "error":
        return [*findings, Finding(label, "models", f"models() failed: {_detail(found)}")]
    if not isinstance(found, list):
        return [*findings, Finding(label, "models", f"models() returned {type(found).__name__}, not a list")]
    offers, row_findings = _offers(typing.cast("list[object]", found), label)
    findings += row_findings
    if not offers:
        findings.append(Finding(label, "models", "models() offered no valid model, so model() wasn't checked", "warn"))
        return findings
    findings += [
        Finding(label, "models", f"{len(ids)} offers are marked level {level} ({', '.join(ids[:3])}); one at most")
        for level, ids in _levels(offers).items()
        if len(ids) > 1
    ]
    usable = [o for o in offers if o.available and o.tools]
    first = (usable or offers)[0]
    findings += _built(adapter, first.id, label, live_request=live_request, timeout=timeout)
    for model_id, why in [(UNKNOWN_MODEL_ID, "unknown"), *((o.id, "unavailable") for o in offers if not o.available)][
        :2
    ]:
        findings += _refused(adapter, model_id, why, label, timeout)
    return findings


def _levels(offers: list[Any]) -> dict[str, list[str]]:
    """The ids of the offers marked with each level, in the plugin's order."""
    levels: dict[str, list[str]] = {}
    for offer in offers:
        if offer.level is not None:
            levels.setdefault(offer.level, []).append(offer.id)
    return levels


def _missing() -> object:
    msg = "the adapter has no models() method"
    raise AttributeError(msg)


def _offers(rows: list[object], label: str) -> tuple[list[Any], list[Finding]]:
    from pydantic import ValidationError  # noqa: PLC0415

    from heinzel.kernel import ModelOffer  # noqa: PLC0415

    offers: list[ModelOffer] = []
    findings: list[Finding] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        try:
            data = row.model_dump() if isinstance(row, ModelOffer) else row
            offer = ModelOffer.model_validate(data)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first.get("loc", ()))
            findings.append(
                Finding(
                    label,
                    "models",
                    f"row {index} isn't a valid ModelOffer ({where}: {first.get('msg', '')}); Core skips it."
                    f"{_says('models', 'skipped')}",
                    "warn",
                )
            )
            continue
        if offer.id in seen:
            findings.append(Finding(label, "models", f"the id {offer.id} is offered twice; ids must be unique"))
            continue
        seen.add(offer.id)
        offers.append(offer)
    return offers, findings


def _built(adapter: object, model_id: str, label: str, *, live_request: bool, timeout: float) -> list[Finding]:
    from pydantic_ai.models import Model  # noqa: PLC0415 - only for ai-models adapters

    outcome, model = _timed(getattr(adapter, "model", _missing), {"model_id": model_id}, timeout)
    if outcome == "timeout":
        return [Finding(label, "model", f"model({model_id!r}) took longer than {timeout:g}s.{_says('model', 'Build')}")]
    if outcome == "error":
        return [Finding(label, "model", f"model({model_id!r}) failed: {_detail(model)}")]
    if not isinstance(model, Model):
        return [
            Finding(
                label,
                "model",
                f"model({model_id!r}) returned {type(model).__name__}, not a pydantic_ai.models.Model."
                f"{_says('model', 'ready')}",
            )
        ]
    if not live_request:
        return []
    request: dict[str, object] = {"model": model}
    outcome, answer = _timed(_one_request, request, timeout)
    if outcome != "ok":
        detail = f"took longer than {timeout:g}s" if outcome == "timeout" else f"failed: {_detail(answer)}"
        return [Finding(label, "request", f"one request through {model_id} {detail}")]
    return []


def _one_request(model: Any) -> object:  # noqa: ANN401 - a pydantic_ai Model
    from pydantic_ai.direct import model_request  # noqa: PLC0415
    from pydantic_ai.messages import ModelRequest  # noqa: PLC0415

    return asyncio.run(model_request(model, [ModelRequest.user_text_prompt("Reply with OK.")]))


def _refused(adapter: object, model_id: str, why: str, label: str, timeout: float) -> list[Finding]:
    from heinzel.kernel import HeinzelError  # noqa: PLC0415

    outcome, value = _timed(getattr(adapter, "model", _missing), {"model_id": model_id}, timeout)
    if outcome == "error" and isinstance(value, HeinzelError):  # ModelUnavailableError, or another clear refusal
        return []
    if outcome == "error":
        got = f"raised {type(value).__name__}"
    elif outcome == "timeout":
        got = f"took longer than {timeout:g}s"
    else:
        got = f"returned {type(value).__name__}"
    return [
        Finding(
            label,
            "model",
            f"model({model_id!r}) for an {why} id {got}; it should raise ModelUnavailableError."
            f"{_says('model', 'ModelUnavailableError')}",
            "warn",
        )
    ]


# --- sign-in and health (ADR 0055, ADR 0136): offline, quick, never a secret


OFFLINE_TIMEOUT = 5.0
"""Seconds an offline sign-in status or health check may take: it reads local evidence only."""

_network_lock = threading.Lock()


class _NetworkRefusedError(OSError):
    def __init__(self, what: str) -> None:
        super().__init__(f"conformance check: no network while offline ({what})")


@contextlib.contextmanager
def offline() -> Generator[list[str]]:
    """Refuse every internet connection and name lookup in this process meanwhile, recording each attempt.

    Local sockets (Unix domain) still work. A subprocess isn't covered: an offline check shouldn't start a vendor
    tool that goes online, and the time limit catches one that waits for the network.

    Yields:
        The attempts so far, as `connect to ...` or `look up ...`.
    """
    attempts: list[str] = []
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    original_lookup = socket.getaddrinfo

    def refuse(action: str, target: object) -> NoReturn:
        attempts.append(f"{action} {target!r}")
        raise _NetworkRefusedError(attempts[-1])

    def connect(sock: socket.socket, address: Any) -> None:  # noqa: ANN401 - any socket address
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            refuse("connect to", address)
        original_connect(sock, address)

    def connect_ex(sock: socket.socket, address: Any) -> int:  # noqa: ANN401 - any socket address
        if sock.family in {socket.AF_INET, socket.AF_INET6}:
            refuse("connect to", address)
        return original_connect_ex(sock, address)

    def lookup(host: object, *_: object, **__: object) -> Any:  # noqa: ANN401 - socket.getaddrinfo's result
        refuse("look up", host)

    with _network_lock:
        socket.socket.connect = connect  # type: ignore[method-assign, assignment]
        socket.socket.connect_ex = connect_ex  # type: ignore[method-assign, assignment]
        socket.getaddrinfo = lookup
        try:
            yield attempts
        finally:
            socket.socket.connect, socket.socket.connect_ex = original_connect, original_connect_ex  # type: ignore[method-assign]
            socket.getaddrinfo = original_lookup


def _offline_call(func: Callable[..., object], arguments: Mapping[str, object]) -> tuple[str, Any, list[str]]:
    with offline() as attempts:
        outcome, value = _timed(func, arguments, OFFLINE_TIMEOUT)
    return outcome, value, attempts


def _sign_in_status(provider: object, grants: "tuple[Any, ...]", label: str) -> list[Finding]:
    from heinzel.kernel import SignInStatus  # noqa: PLC0415
    from heinzel.kernel._telemetry import looks_like_credential  # noqa: PLC0415 - Core's own credential shapes

    status = getattr(provider, "status", None)
    if not callable(status):
        return [Finding(label, "sign-in", "the provider has no status(*, live) method")]
    outcome, value, attempts = _offline_call(status, {"live": False})
    findings = [
        Finding(label, "offline", f"status(live=False) went online: {', '.join(attempts[:3])}") for _ in attempts[:1]
    ]
    if outcome == "timeout":
        return [*findings, Finding(label, "sign-in", f"status(live=False) took longer than {OFFLINE_TIMEOUT:g}s")]
    if outcome == "error":
        return [*findings, Finding(label, "sign-in", f"status(live=False) failed: {_detail(value)}")]
    if not isinstance(value, SignInStatus):
        return [*findings, Finding(label, "sign-in", f"status(live=False) returned {type(value).__name__}")]
    if looks_like_credential(value.account, free_text=True):
        findings.append(Finding(label, "sign-in", "status().account looks like a token or key; name the account"))
    if value.live:
        findings.append(Finding(label, "sign-in", "status(live=False) says live=True: it must not ask the vendor"))
    declared = [grant.id for grant in grants]
    reported = {state.grant.id for state in value.grants}
    if missing := [grant for grant in declared if grant not in reported]:
        findings.append(Finding(label, "sign-in", f"status() leaves out the declared grants {', '.join(missing)}"))
    if extra := sorted(reported - set(declared)):
        findings.append(Finding(label, "sign-in", f"status() reports grants it doesn't declare: {', '.join(extra)}"))
    return findings


def _health(provider: Callable[..., object], label: str, kernel: "Kernel | None") -> list[Finding]:
    from heinzel.kernel import Check  # noqa: PLC0415

    def run() -> object:
        return kernel.call(provider) if kernel is not None else provider()

    outcome, value, attempts = _offline_call(run, {})
    findings = [Finding(label, "offline", f"the health checks went online: {', '.join(attempts[:3])}")][: len(attempts)]
    if outcome == "timeout":
        return [*findings, Finding(label, "health", f"the health checks took longer than {OFFLINE_TIMEOUT:g}s")]
    if outcome == "error":
        return [*findings, Finding(label, "health", f"the health checks failed: {_detail(value)}")]
    if not isinstance(value, list) or not all(isinstance(c, Check) for c in typing.cast("list[object]", value)):
        shape = _shape_of(typing.cast("object", value))
        return [*findings, Finding(label, "health", f"the health checks must return a list of Check, not {shape}")]
    return findings


def _shape_of(value: object) -> str:
    """`list[str]` for a list of strings, else the type's name: what a wrong return value is."""
    if isinstance(value, list):
        kinds = sorted({type(item).__name__ for item in typing.cast("list[object]", value)})
        return f"list[{' | '.join(kinds)}]"
    return type(value).__name__
