"""Reading contract files: strict YAML frontmatter, the Markdown body, and diagnostics with file and line (ADR 0021).

Everything here is pure: text in, a `Contract` with its diagnostics out. Nothing needs a model, the network or a
kernel; checks that need the kernel (publish operations, name collisions) live in the service.
"""

import errno
import hashlib
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError
from pydantic_core import ErrorDetails
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from heinzel.modules.workflow._models import (
    SCHEMA_VERSION,
    SUFFIX,
    Agent,
    Diagnostic,
    Frontmatter,
    Hz,
    Input,
    Namespace,
    Output,
    Publish,
    Severity,
)

KeyPath = tuple[str | int, ...]

_STR = "tag:yaml.org,2002:str"
_BOOL = "tag:yaml.org,2002:bool"
_INT = "tag:yaml.org,2002:int"
_FLOAT = "tag:yaml.org,2002:float"
_NULL = "tag:yaml.org,2002:null"


class _Loader(yaml.SafeLoader):
    """PyYAML's safe loader resolving plain scalars like YAML 1.2's core schema, a little stricter.

    YAML 1.1 reads `on`, `yes` and `no` as booleans, `1:20` as 80 and `2026-10-24` as a date; under the core schema
    they stay strings, so `on:` is a key. Numbers with a leading zero (`010`) stay strings too, rather than becoming
    octal (YAML 1.1) or ten (YAML 1.2): an id like that is text.
    """


_Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag == _NULL]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_CORE_SCALARS = (
    (_BOOL, r"^(?:true|True|TRUE|false|False|FALSE)$", "tTfF"),
    (_INT, r"^[-+]?(?:0|[1-9][0-9]*)$|^0x[0-9a-fA-F]+$", "-+0123456789"),
    (
        _FLOAT,
        (
            r"^[-+]?(?:\.[0-9]+|(?:0|[1-9][0-9]*)\.[0-9]*)(?:[eE][-+]?[0-9]+)?$|^[-+]?(?:0|[1-9][0-9]*)[eE][-+]?[0-9]+$"
            r"|^[-+]?\.(?:inf|Inf|INF)$|^\.(?:nan|NaN|NAN)$"
        ),
        "-+0123456789.",
    ),
)
for _tag, _pattern, _first in _CORE_SCALARS:
    _Loader.add_implicit_resolver(_tag, re.compile(_pattern), list(_first))  # pyright: ignore[reportUnknownMemberType] - PyYAML stubs

KNOWN_SECTIONS = ("description", "goal", "requires", "maintains", "continuity", "steps", "done when", "invariants")
RETIRED_SECTIONS = {
    "criteria": "postconditions go under ### Done when",
    "ensures": "postconditions go under ### Done when",
    "fulfillment": "describe the render under ### Steps",
    "constraints": "the actuation boundary goes under ### Invariants",
    "memory": "state lives in the outputs (### Maintains)",
    "services": "inputs are declared in `hz.inputs` and described under ### Requires",
    "wiring": "inputs are declared in `hz.inputs`; there is no semantic wiring",
    "execution": "ProseScript is not supported; give guidance under ### Steps",
}
DEFERRED_SECTIONS = {
    "tools": "the agent gets no hz tools yet; read-only runner tools go in `hz.agent.tools` (ADR 0029, option a)",
}
MAX_BYTES = 256 * 1024
"""Contract files larger than this aren't read: a contract is a page of prose, not a data dump."""
MAX_DEPTH = 32
MAX_NODES = 10_000
_CORE_TAGS = frozenset({_STR, _BOOL, _INT, _FLOAT, _NULL})
_SCHEDULE_LINE = re.compile(r"^\s*schedule\s*:")
_QUOTE_SCHEDULE = 'quote the cron expression: schedule: "0 7 * * MON"'
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_ITEM = re.compile(r"^[-*+][ \t]+`([a-z][a-z0-9_]*)`")
_LIST = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Section:
    """A `###` section of the body: its heading line and its lines."""

    name: str
    line: int
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        """The section's text without its heading."""
        return "\n".join(self.lines).strip()

    @property
    def items(self) -> list[str]:
        """Names of its top-level items written ``- `name`: ...``."""
        return [m.group(1) for line in self.lines if (m := _ITEM.match(line))]


@dataclass(frozen=True, slots=True)
class Contract:
    """A contract file as read: its frontmatter when valid, its body and every problem found."""

    path: Path
    namespace: Namespace
    name: str
    frontmatter: Frontmatter | None
    body: str
    """The Markdown body without HTML comments: the agent's instructions."""
    diagnostics: tuple[Diagnostic, ...] = field(default=())
    lines: Mapping[KeyPath, int] = field(default_factory=dict[KeyPath, int])
    """The 0-based frontmatter line of every key path, for diagnostics added later."""
    sha256: str = ""
    """SHA-256 of the file's bytes, as `sha256:<hex>`; what `[workflow] schedule_team` pins."""

    def line_of(self, *path: str | int) -> int:
        """The file line of the deepest known key along `path`; 1 when there is no frontmatter."""
        return _line(self.lines, path) if self.lines else 1

    def adding(self, *problems: Diagnostic) -> "Contract":
        """The same contract with more diagnostics."""
        return replace(self, diagnostics=(*self.diagnostics, *problems)) if problems else self

    @property
    def valid(self) -> bool:
        """Whether it has no errors, so it can be compiled."""
        return self.frontmatter is not None and not any(d.severity == "error" for d in self.diagnostics)

    @property
    def qualified(self) -> str:
        """Its qualified name, `<namespace>:<name>`."""
        return f"{self.namespace}:{self.name}"


class _Problems:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.found: list[Diagnostic] = []

    def add(  # noqa: PLR0913, PLR0917 - every part of a diagnostic
        self, line: int, code: str, where: str, message: str, hint: str = "", severity: Severity = "error"
    ) -> None:
        self.found.append(
            Diagnostic(file=self.path, line=line, code=code, severity=severity, field=where, message=message, hint=hint)
        )


class _YamlError(Exception):
    def __init__(self, line: int, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.line = line
        self.code = code
        self.hint = hint


def contract_name(path: Path) -> str:
    """The name a file declares by its own name: the stem before `.prose.md`."""
    return path.name.removesuffix(SUFFIX) if path.name.endswith(SUFFIX) else path.stem


def read_contract(path: Path, namespace: Namespace, text: str | None = None) -> Contract:
    """Read and check one contract file, without a kernel. `text` stands in for the file's content in tests.

    Never raises: whatever goes wrong with one file is that file's diagnostic, so one bad file never stops the daemon.
    """
    try:
        return _read(path, namespace, text)
    except Exception as exc:  # noqa: BLE001 - isolation boundary: one file's failure is its own diagnostic
        problems = _Problems(path)
        problems.add(1, "HZC001", "file", f"can't read the contract: {type(exc).__name__}: {exc}")
        return Contract(path, namespace, contract_name(path), None, "", tuple(problems.found))


def _read(path: Path, namespace: Namespace, text: str | None) -> Contract:
    problems = _Problems(path)
    name = contract_name(path)
    try:
        if text is None:
            size = path.stat().st_size  # before reading, so a huge file is never loaded
            if size > MAX_BYTES:
                problems.add(1, "HZC001", "file", f"the file is {size // 1024} KiB; contracts are at most 256 KiB")
                return Contract(path, namespace, name, None, "", tuple(problems.found))
            raw = path.read_bytes()
        else:
            raw = text.encode()
        content = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        problems.add(1, "HZC001", "file", f"can't read the file: {exc}")
        return Contract(path, namespace, name, None, "", tuple(problems.found))
    if len(raw) > MAX_BYTES:
        problems.add(1, "HZC001", "file", f"the file is {len(raw) // 1024} KiB; contracts are at most 256 KiB")
        return Contract(path, namespace, name, None, "", tuple(problems.found))
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    content = content.replace("\r\n", "\n").removeprefix("\ufeff")
    if not path.name.endswith(SUFFIX):
        problems.add(1, "HZC010", "file", f"a contract file is named `<name>{SUFFIX}`", f"rename it to {name}{SUFFIX}")
    split = _split(content)
    if split is None:
        problems.add(
            1,
            "HZC001",
            "frontmatter",
            "no YAML frontmatter",
            "start the file with a line `---`, the frontmatter, and another line `---`",
        )
        return Contract(path, namespace, name, None, content, tuple(problems.found), sha256=digest)
    yaml_text, body, body_line = split
    try:
        data, lines = _load(yaml_text)
    except _YamlError as exc:
        problems.add(exc.line + 2, exc.code, "frontmatter", str(exc), exc.hint)
        body_text = _COMMENT.sub("", body).strip()
        return Contract(path, namespace, name, None, body_text, tuple(problems.found), sha256=digest)
    frontmatter = _validate(data, lines, problems)
    if frontmatter is not None and frontmatter.name != name:
        problems.add(
            _line(lines, ("name",)),
            "HZC010",
            "name",
            f"name {frontmatter.name!r} differs from the file name",
            f"rename the file to {frontmatter.name}{SUFFIX} or set `name: {name}`",
        )
    _check_body(body, body_line, frontmatter, problems)
    body_text = _COMMENT.sub("", body).strip()
    return Contract(path, namespace, name, frontmatter, body_text, tuple(problems.found), lines, digest)


def _split(content: str) -> tuple[str, str, int] | None:
    """Frontmatter text, body and the body's first line number, or None without frontmatter."""
    lines = content.split("\n")
    if not lines or lines[0].rstrip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].rstrip() in {"---", "..."}:
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :]), index + 2
    return None


def _load(text: str) -> tuple[dict[str, Any], dict[KeyPath, int]]:
    """Parse YAML, keeping the line of every key, within limits that keep a hostile file cheap.

    Anchors and aliases (the "billion laughs"), explicit tags other than the core scalar ones, more than 32 levels of
    nesting and more than 10,000 nodes are refused by scanning the event stream before anything is built.

    Raises:
        _YamlError: If the YAML doesn't parse, breaks a limit, has a duplicate or non-string key, or isn't a mapping.
    """
    source = text.split("\n")

    def hint(line: int) -> str:
        return _QUOTE_SCHEDULE if 0 <= line < len(source) and _SCHEDULE_LINE.match(source[line]) else ""

    lines: dict[KeyPath, int] = {}
    try:
        _scan(text)
        loader = _Loader(text)
        try:
            node = loader.get_single_node()
            data = _build(loader, node, (), lines) if node is not None else None
        finally:
            loader.dispose()
    except _YamlError as exc:
        exc.hint = exc.hint or hint(exc.line)
        raise
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        line = mark.line if mark else 0
        raise _YamlError(line, "HZC001", f"YAML doesn't parse: {exc.problem or exc}", hint(line)) from exc
    except yaml.YAMLError as exc:
        raise _YamlError(0, "HZC001", f"YAML doesn't parse: {exc}") from exc
    if not isinstance(data, dict):
        raise _YamlError(0, "HZC001", "the frontmatter must be a mapping of keys to values")
    return cast("dict[str, Any]", data), lines


def _scan(text: str) -> None:
    """Refuse what the frontmatter never needs, from the event stream, before any node is built.

    Raises:
        _YamlError: At the first anchor, alias, non-core tag, nesting deeper than 32 or node past 10,000.
    """
    depth = 0
    nodes = 0
    parse = cast("Callable[..., Iterator[yaml.Event]]", yaml.parse)  # pyright: ignore[reportUnknownMemberType] - PyYAML stubs leave its Loader untyped
    for event in parse(text, Loader=_Loader):
        line = event.start_mark.line if event.start_mark is not None else 0
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
            raise _YamlError(line, "HZC004", "anchors and aliases aren't allowed", "write each value out")
        tag = getattr(event, "tag", None)
        if isinstance(event, yaml.CollectionStartEvent) and tag is not None:
            raise _YamlError(line, "HZC004", f"tags aren't allowed on lists and maps: {tag}")
        if isinstance(event, yaml.ScalarEvent) and tag not in {None, "!"} and tag not in _CORE_TAGS:
            raise _YamlError(line, "HZC004", f"the tag {tag} isn't allowed; only text, numbers, booleans and null")
        if isinstance(event, yaml.NodeEvent):
            nodes += 1
            if nodes > MAX_NODES:
                raise _YamlError(line, "HZC004", f"more than {MAX_NODES} values; a contract's frontmatter is small")
        if isinstance(event, yaml.CollectionStartEvent):
            depth += 1
            if depth > MAX_DEPTH:
                raise _YamlError(line, "HZC004", f"nested deeper than {MAX_DEPTH} levels")
        elif isinstance(event, yaml.CollectionEndEvent):
            depth -= 1


def _build(loader: _Loader, node: Node, path: KeyPath, lines: dict[KeyPath, int]) -> Any:  # noqa: ANN401 - YAML
    lines.setdefault(path, node.start_mark.line)
    if isinstance(node, MappingNode):
        mapping: dict[str, Any] = {}
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode) or key_node.tag != _STR:
                raise _YamlError(
                    key_node.start_mark.line, "HZC004", f"keys must be text, not {key_node.tag.rpartition(':')[2]}"
                )
            key = cast("str", key_node.value)
            if key in mapping:
                raise _YamlError(key_node.start_mark.line, "HZC004", f"duplicate key {key!r}")
            lines[(*path, key)] = key_node.start_mark.line
            mapping[key] = _build(loader, value_node, (*path, key), lines)
        return mapping
    if isinstance(node, SequenceNode):
        return [_build(loader, item, (*path, index), lines) for index, item in enumerate(node.value)]
    return loader.construct_object(node, deep=True)  # pyright: ignore[reportUnknownMemberType] - PyYAML stubs leave node untyped


def _line(lines: Mapping[KeyPath, int], path: KeyPath) -> int:
    """The file line of the deepest known key along `path`."""
    end = len(path)
    while end > 0 and path[:end] not in lines:
        end -= 1
    return lines.get(path[:end], 0) + 2  # the frontmatter starts on the file's second line


def _validate(data: dict[str, Any], lines: dict[KeyPath, int], problems: _Problems) -> Frontmatter | None:
    data = {k: v for k, v in data.items() if not k.startswith("x-")}  # extension keys, ignored by contract
    deferred = _deferred(data)
    for path, message in deferred:
        problems.add(_line(lines, path), "HZC023", ".".join(map(str, path)), message)
    if deferred:
        return None
    hz = data.get("hz")
    if isinstance(hz, dict):
        version = cast("dict[str, Any]", hz).get("schema")
        if isinstance(version, int) and not isinstance(version, bool) and version > SCHEMA_VERSION:
            problems.add(
                _line(lines, ("hz", "schema")),
                "HZC003",
                "hz.schema",
                f"schema {version} is newer than this Heinzelmännchen reads ({SCHEMA_VERSION})",
                "run `hz system update`",
            )
            return None
    try:
        return Frontmatter.model_validate(data)
    except ValidationError as exc:
        for error in exc.errors():
            _report(error, lines, problems)
        return None


def _deferred(data: dict[str, Any]) -> list[tuple[KeyPath, str]]:
    """Every key ADR 0021 names that this version doesn't build, in one pass, so all are reported at once."""
    hz = data.get("hz")
    if not isinstance(hz, dict):
        return []
    hz_map = cast("dict[str, Any]", hz)
    places: list[tuple[object, type[Any], KeyPath]] = [
        (hz_map, Hz, ("hz",)),
        (hz_map.get("agent"), Agent, ("hz", "agent")),
    ]
    for key, model in (("inputs", Input), ("outputs", Output), ("publishes", Publish)):
        places += [(value, model, ("hz", key, index)) for index, value in _items(hz_map.get(key))]
    found = [
        ((*path, key), f"`{key}` is not supported yet: {model.deferred_keys[key]}")
        for value, model, path in places
        if isinstance(value, dict)
        for key in cast("dict[str, Any]", value)
        if key in model.deferred_keys
    ]
    if hz_map.get("catch_up") == "all":
        found.append((("hz", "catch_up"), "`catch_up: all` is not supported yet: it comes with schedule slots"))
    if hz_map.get("scope") == "team":
        found.append(
            (("hz", "scope"), "`scope: team` is not supported yet: it comes with leases in the state repository")
        )
    return found


def _items(value: object) -> list[tuple[str | int, object]]:
    if isinstance(value, dict):
        return list(cast("dict[str | int, object]", value).items())
    if isinstance(value, list):
        return list(enumerate(cast("list[object]", value)))
    return []


def _report(error: ErrorDetails, lines: dict[KeyPath, int], problems: _Problems) -> None:
    context = error.get("ctx") or {}
    loc: KeyPath = tuple(error["loc"])
    match error["type"]:
        case "contract":
            loc = (*loc, *context.get("path", ()))
            code, message = "HZC002", str(context["message"])
        case "deferred":
            loc = (*loc, str(context["key"]))
            code, message = "HZC023", error["msg"]
        case "extra_forbidden":
            code, message = "HZC002", f"unknown key `{loc[-1]}`"
        case "missing":
            code, message = "HZC002", f"`{loc[-1]}` is required"
        case _:
            code, message = "HZC002", error["msg"]
    if loc[:2] == ("hz", "schedule"):
        code = "HZC011"
    where = ".".join(str(part) for part in loc) or "frontmatter"
    hint = "see docs/schemas/contract-frontmatter-1.json for every key" if code == "HZC002" else ""
    problems.add(_line(lines, loc), code, where, message, hint)


def sections(body: str, first_line: int) -> list[Section]:
    """The `###` sections of a body, ignoring headings inside fenced code; text before the first is dropped."""
    found: list[Section] = []
    name: str | None = None
    start = 0
    collected: list[str] = []
    fence: str | None = None
    for offset, raw in enumerate(body.split("\n")):
        if (opened := _FENCE.match(raw)) is not None:
            marker = opened.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
        heading = _HEADING.match(raw) if fence is None else None
        if heading is not None and len(heading.group(1)) <= 3:  # noqa: PLR2004 - `###` sections; `####` are facets
            if name is not None:
                found.append(Section(name, start, tuple(collected)))
            name, start, collected = (heading.group(2) if len(heading.group(1)) == 3 else None), first_line + offset, []  # noqa: PLR2004
            continue
        collected.append(raw)
    if name is not None:
        found.append(Section(name, start, tuple(collected)))
    return found


def _check_body(body: str, first_line: int, frontmatter: Frontmatter | None, problems: _Problems) -> None:
    by_name = _sections_by_name(body, first_line, problems)
    end = first_line + body.rstrip().count("\n")
    _check_required(by_name, end, problems)
    if frontmatter is not None:
        _check_items(by_name, end, frontmatter, problems)


def _sections_by_name(body: str, first_line: int, problems: _Problems) -> dict[str, Section]:
    by_name: dict[str, Section] = {}
    for section in sections(_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), body), first_line):
        key = section.name.strip().lower()
        if key in RETIRED_SECTIONS:
            problems.add(section.line, "HZC106", f"### {section.name}", "unsupported section", RETIRED_SECTIONS[key])
        elif key in DEFERRED_SECTIONS:
            problems.add(section.line, "HZC023", f"### {section.name}", "not supported yet", DEFERRED_SECTIONS[key])
        elif key not in KNOWN_SECTIONS:
            problems.add(
                section.line,
                "HZC105",
                f"### {section.name}",
                "unknown section; the agent sees it as part of the contract",
                f"known sections: {', '.join(s.title() for s in KNOWN_SECTIONS)}",
                severity="warning",
            )
        by_name.setdefault(key, section)
    return by_name


def _check_required(by_name: dict[str, Section], end: int, problems: _Problems) -> None:
    goal = by_name.get("goal")
    if goal is None or not goal.text:
        problems.add(
            goal.line if goal else end,
            "HZC101",
            "### Goal",
            "missing or empty",
            "state in one sentence what stays true, e.g. 'The weekly digest is current.'",
        )
    done = by_name.get("done when")
    if done is None or not any(_LIST.match(line) for line in done.lines):
        problems.add(
            done.line if done else end,
            "HZC107",
            "### Done when",
            "missing or without items",
            "list the postconditions the draft must meet, one `- [ ] ...` per line",
        )


def _check_items(by_name: dict[str, Section], end: int, frontmatter: Frontmatter, problems: _Problems) -> None:
    for section_name, declared, key in (
        ("requires", sorted(frontmatter.hz.inputs), "hz.inputs"),
        ("maintains", sorted(frontmatter.hz.outputs), "hz.outputs"),
    ):
        found = by_name.get(section_name)
        if found is None:
            if declared:
                problems.add(
                    end,
                    "HZC103",
                    f"### {section_name.title()}",
                    "missing",
                    f"add the section with one ``- `name`: ...`` item per key of `{key}`: {', '.join(declared)}",
                )
            continue
        items = sorted(set(found.items))
        if items != declared:
            problems.add(
                found.line,
                "HZC103",
                f"### {section_name.title()}",
                f"items {items} don't match `{key}` {declared}",
                f"write one ``- `name`: ...`` item per key of `{key}`, and no others",
            )


def collect(directory: Path) -> list[Path]:
    """The contract files in `directory`, sorted; none if it doesn't exist.

    Raises:
        NotADirectoryError: If `directory` is a file.
        OSError: If `directory` is a symbolic link, or can't be listed.
    """
    # Symbolic links are skipped: a link in a team repository could make your daemon read a file outside it.
    if directory.is_symlink():
        raise OSError(errno.ELOOP, "it is a symbolic link; contracts are read from real directories only")
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise NotADirectoryError(errno.ENOTDIR, "it is a file, not a directory")
    return sorted(p for p in directory.iterdir() if p.name.endswith(SUFFIX) and not p.is_symlink() and p.is_file())
