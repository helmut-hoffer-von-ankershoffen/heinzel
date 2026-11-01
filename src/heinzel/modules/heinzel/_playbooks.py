"""Playbooks as the workflow module consumes them: listing, discovering assets and finding templates (ADR 0037).

Playbooks are data plugins contribute (`Module.playbook`). Their discovery hints run like contract inputs: through
`Kernel.handle_for` as a job of this module that declared only the capability it reads, so a hint gets read handles
on the adapters configuration binds and nothing else. One hint's failure is that hint's problem, never the listing's.
"""

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from pydantic import BaseModel, Field, JsonValue, ValidationError
from pydantic_core import to_jsonable_python

from heinzel import HeinzelError, Kernel, SetupLet, clean_text
from heinzel.kernel import ContractTemplate, DiscoveryHint, PlaybookLet, Requester, Requirement, toml_value
from heinzel.modules.heinzel._jsonable import jsonable
from heinzel.modules.heinzel._models import Input, Step

MAX_CANDIDATES = 20
"""Candidates kept per hint: a proposal lists what a person can choose from, not a search result."""
REFERENCE_FIELDS = ("ref", "key", "id", "url")
"""Where a found item's reference is, in order: a `Document`'s ref, an `Issue`'s key, a message's id, a URL."""


class HintView(BaseModel):
    """A discovery hint as `hz heinzel playbooks` shows it."""

    read: str = Field(description="The capability read method it calls.")
    args: dict[str, JsonValue] = Field(description="Its arguments besides the time window.")
    window: str | None = Field(description="How far back it reads, for methods with a time window.")
    match: str | None = Field(description="The pattern a candidate's title must match; every item if none.")
    summary: str = Field(description="What it looks for, in words.")


class AssetView(BaseModel):
    """An asset type a playbook knows."""

    name: str = Field(description="Its name; templates refer to it as `{{asset:<name>}}`.")
    description: str = Field(description="What it is.")
    hints: list[HintView] = Field(description="Where `hz heinzel discover` looks for candidates.")
    kept_by: str | None = Field(
        default=None, description="The template, `<module>:<name>`, whose output keeps it true; none if none does."
    )


class TemplateView(BaseModel):
    """A template contract a playbook offers."""

    template: str = Field(description="Its qualified name, `<module>:<name>`, as `--template` takes it.")
    assets: list[str] = Field(description="The asset types its placeholders name, filled with `--asset`.")
    keeps: list[str] = Field(description="The asset types its output keeps true.")
    command: str = Field(description="The command that writes it into your workflows directory.")


class DefaultView(BaseModel):
    """A setting a playbook recommends; never applied without your yes."""

    key: str = Field(description="The setting, `<section>.<key>`.")
    value: JsonValue = Field(description="The recommended value.")
    command: str = Field(description="The command that applies it, if you agree.")


class PlaybookView(BaseModel):
    """One playbook: what it is for, its asset types, templates and recommended settings."""

    module: str = Field(description="The module that contributes it.")
    summary: str = Field(description="What it is for.")
    assets: list[AssetView] = Field(description="The asset types it knows.")
    templates: list[TemplateView] = Field(description="The template contracts it offers.")
    defaults: list[DefaultView] = Field(description="The settings it recommends.")


class PlaybookList(BaseModel):
    """`hz heinzel playbooks`: every playbook the installed modules contribute."""

    playbooks: list[PlaybookView] = Field(description="The playbooks, by module.")
    next: list[Step] = Field(description="What to do next, as commands.")


class Candidate(BaseModel):
    """Something a hint found that may be an asset of its type."""

    playbook: str = Field(description="The module whose playbook's hint found it.")
    asset: str = Field(description="The asset type, as `--asset <type>=<ref>` takes it.")
    ref: str = Field(description="Its reference, in the adapter's notation: what `--asset <type>=<ref>` takes.")
    title: str = Field(description="Its title.")
    url: str | None = Field(default=None, description="Where people open it, if it has a web address.")
    hint: str = Field(description="The read method that found it.")


class HintProblem(BaseModel):
    """A hint that couldn't run, and what to do about it."""

    playbook: str = Field(description="The module whose playbook's hint it is.")
    asset: str = Field(description="The asset type.")
    hint: str = Field(description="The read method.")
    problem: str = Field(description="Why it couldn't run.")


class Discovery(BaseModel):
    """`hz heinzel discover`: candidates for each asset type, found through capabilities you can read."""

    playbooks: list[str] = Field(description="The playbooks whose hints ran, by module.")
    candidates: list[Candidate] = Field(description="What the hints found, by asset type.")
    problems: list[HintProblem] = Field(description="Hints that couldn't run.")
    next: list[Step] = Field(description="What to do next, as commands.")


def playbooks(kernel: Kernel) -> list[tuple[str, PlaybookLet]]:
    """Every playbook, with the qualified name of its module, sorted by it."""
    return sorted(((record.qualname, let) for record, let in kernel.lets(PlaybookLet)), key=lambda p: p[0])


def find_template(kernel: Kernel, name: str) -> tuple[str, ContractTemplate]:
    """The template `<module>:<name>`, or `<name>` when only one playbook has it, with its qualified name.

    Raises:
        HeinzelError: If no playbook has it, or a bare name is in more than one.
    """
    module, _, bare = name.rpartition(":")
    found = [
        (f"{qualname}:{t.name}", t)
        for qualname, playbook in playbooks(kernel)
        for t in playbook.templates
        if t.name == bare and module in {"", qualname}
    ]
    if len(found) > 1:
        msg = f"Template {name!r} is in more than one playbook: say {' or '.join(q for q, _ in found)}"
        raise HeinzelError(msg)
    if not found:
        known = [f"{q}:{t.name}" for q, p in playbooks(kernel) for t in p.templates]
        listed = ", ".join(known) if known else "none: no installed plugin contributes a playbook"
        msg = f"No template {name!r}; the templates are: {listed}. `hz heinzel playbooks` lists them"
        raise HeinzelError(msg)
    return found[0]


def listing(kernel: Kernel) -> PlaybookList:
    """Every playbook as `hz heinzel playbooks` shows it."""
    views = [_view(qualname, playbook) for qualname, playbook in playbooks(kernel)]
    if not views:
        steps = NO_PLAYBOOKS
    else:
        steps = [Step(command="hz heinzel discover", why="find candidates for each asset type in what you can read")]
        with_setup = {record.qualname for record, _ in kernel.lets(SetupLet)}
        steps += [
            Step(
                command=f"hz {qualname.replace('.', ' ')} setup",
                why="accept its recommended settings, choose its assets and write its contracts",
            )
            for qualname, _ in playbooks(kernel)
            if qualname in with_setup
        ]
    return PlaybookList(playbooks=views, next=steps)


def _view(qualname: str, playbook: PlaybookLet) -> PlaybookView:
    return PlaybookView(
        module=qualname,
        summary=playbook.summary,
        assets=[
            AssetView(
                name=a.name,
                description=a.description,
                hints=[
                    HintView(
                        read=h.read,
                        args=cast("dict[str, JsonValue]", jsonable(dict(h.args))),
                        window=h.window,
                        match=h.match,
                        summary=h.summary,
                    )
                    for h in a.hints
                ],
                kept_by=f"{qualname}:{a.kept_by}" if a.kept_by else None,
            )
            for a in playbook.assets
        ],
        templates=[
            TemplateView(
                template=f"{qualname}:{t.name}",
                assets=t.placeholders(),
                keeps=[a.name for a in playbook.assets if a.kept_by == t.name],
                command=f"hz heinzel new {t.name} --template {qualname}:{t.name}",
            )
            for t in playbook.templates
        ],
        defaults=[
            DefaultView(
                key=key,
                value=cast("JsonValue", to_jsonable_python(value)),
                command=f"hz system config set {key} {_shell_value(value)}",
            )
            for key, value in playbook.defaults.items()
        ],
    )


def _shell_value(value: object) -> str:
    """A value as `hz system config set` takes it on a shell line: bare when it is a plain word, else TOML, quoted."""
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_./:@-]+", value):
        return value
    return "'" + toml_value(value).replace("'", "'\\''") + "'"


NO_PLAYBOOKS = [
    Step(
        command="hz system plugin search --text playbook",
        why="playbooks come with plugins: asset types, template contracts and recommended settings",
    ),
    Step(
        command="hz system plugin new ../my-playbook",
        why="or write your own: docs/PLAYBOOKS.md#writing-a-playbook says how",
    ),
]


def discover(kernel: Kernel, module: str, clock: Callable[[], datetime], only: str | None = None) -> Discovery:
    """Run every hint of every playbook (or of `only`'s) through read-only handles and collect the candidates.

    The same problem (an unbound capability, say) is reported once per playbook, not once per hint.

    Args:
        kernel: Hands out the handles.
        module: The qualified name of the workflow module, whose jobs may get handles on a requester's behalf.
        clock: The current time; time windows end now.
        only: A playbook's module; every playbook's hints if None.

    Raises:
        HeinzelError: If `only` names no playbook.
    """
    chosen = [(q, p) for q, p in playbooks(kernel) if only in {None, q}]
    if only is not None and not chosen:
        msg = f"No playbook {only!r}; `hz heinzel playbooks` lists them"
        raise HeinzelError(msg)
    candidates: list[Candidate] = []
    problems: list[HintProblem] = []
    for qualname, playbook in chosen:
        for asset in playbook.assets:
            for hint in asset.hints:
                try:
                    candidates += _run(kernel, module, qualname, asset.name, hint, clock())
                except _BadHintError as exc:
                    why = f"{exc} This is the playbook's mistake: report it to the authors of {qualname}"
                    problems.append(HintProblem(playbook=qualname, asset=asset.name, hint=hint.read, problem=why))
                except HeinzelError as exc:
                    problem = clean_text(str(exc))
                    if not any(p.playbook == qualname and p.problem == problem for p in problems):
                        problems.append(
                            HintProblem(playbook=qualname, asset=asset.name, hint=hint.read, problem=problem)
                        )
                except Exception as exc:  # noqa: BLE001 - isolation boundary: an adapter's failure is the hint's own
                    why = clean_text(f"{type(exc).__name__}: {exc}")
                    problems.append(HintProblem(playbook=qualname, asset=asset.name, hint=hint.read, problem=why))
    if not chosen:
        steps = NO_PLAYBOOKS
    else:
        steps = [
            Step(command=t.command, why="write it; fill each asset with --asset <type>=<ref> from the candidates")
            for q, p in chosen
            for t in _view(q, p).templates
        ]
    return Discovery(playbooks=[q for q, _ in chosen], candidates=candidates, problems=problems, next=steps)


class _BadHintError(HeinzelError):
    """A hint whose arguments don't fit its read method: the playbook's mistake, not the person's."""


def _run(  # noqa: PLR0913, PLR0917 - everything one hint's read needs
    kernel: Kernel, module: str, playbook: str, asset: str, hint: DiscoveryHint, now: datetime
) -> list[Candidate]:
    """The candidates one hint finds.

    Raises:
        _BadHintError: If the hint's arguments don't fit its method.
        HeinzelError: If its capability isn't served.
    """
    window = {"window": hint.window} if hint.window is not None else {}
    try:
        spec = Input.model_validate({"read": hint.read, "args": dict(hint.args), **window})
    except ValidationError as exc:
        msg = f"The hint doesn't fit {hint.read}: {exc.errors()[0]['msg']}."
        raise _BadHintError(msg) from exc
    requirement = Requirement.parse(f"{spec.capability}:read")
    # A read declared by data, like a contract's inputs: a read-only job of the module that owns contracts (ADR 0037).
    requester = Requester(module=module, kind="job", name=f"discover-{playbook}", requires=(requirement,))
    handle = kernel.handle_for(requester, spec.capability)
    result: object = getattr(handle, spec.method)(**spec.arguments(now))
    items = cast("list[object]", result) if isinstance(result, list) else [result]
    pattern = re.compile(hint.match) if hint.match is not None else None
    found: list[Candidate] = []
    for item in items:
        data = to_jsonable_python(item)
        if not isinstance(data, dict):
            continue
        fields = cast("dict[str, Any]", data)
        title = clean_text(str(fields.get("title") or ""))
        if pattern is not None and not pattern.search(title):
            continue
        ref = next((str(fields[f]) for f in REFERENCE_FIELDS if fields.get(f)), None)
        if ref is None:
            continue
        url = fields.get("url")
        found.append(
            Candidate(
                playbook=playbook,
                asset=asset,
                ref=clean_text(ref),
                title=title,
                url=clean_text(url) if isinstance(url, str) else None,
                hint=hint.read,
            )
        )
        if len(found) >= MAX_CANDIDATES:
            break
    return found
