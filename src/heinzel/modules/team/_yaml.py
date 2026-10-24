"""Reading and writing the YAML data files of a team repository: `hz.yaml`, `team/roster.yaml`, `people/*.yaml`.

Repository content is untrusted: anyone who can push to the team repository writes it. It is read
with the same rules as a contract's frontmatter (ADR 0021 §1): YAML 1.2 core scalars only, no anchors or aliases, no
tags but the core scalar ones, bounded size, depth and node count, no control characters. The workflow module's
loader can't be imported (modules don't import each other's private files), so the rules are repeated here; keep
them in step.
"""

import re
from typing import TYPE_CHECKING, Any, cast

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_STR = "tag:yaml.org,2002:str"
_BOOL = "tag:yaml.org,2002:bool"
_INT = "tag:yaml.org,2002:int"
_FLOAT = "tag:yaml.org,2002:float"
_NULL = "tag:yaml.org,2002:null"
_CORE_TAGS = frozenset({_STR, _BOOL, _INT, _FLOAT, _NULL})
MAX_BYTES = 256 * 1024
"""Data files larger than this aren't read: a manifest or a member list is small."""
MAX_DEPTH = 32
MAX_NODES = 10_000
CONTROLS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
"""C0 controls but tab and line feed, DEL and C1 controls: what a terminal acts on rather than shows."""


class YamlError(ValueError):
    """A data file that isn't the YAML hz reads; the message says what is wrong, and where."""


class _Loader(yaml.SafeLoader):
    """PyYAML's safe loader resolving plain scalars like YAML 1.2's core schema: `on`, `yes` and dates stay text."""


_Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag == _NULL]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_CORE_SCALARS = (
    (_BOOL, r"^(?:true|True|TRUE|false|False|FALSE)$", "tTfF"),
    (_INT, r"^[-+]?(?:0|[1-9][0-9]*)$", "-+0123456789"),
    (_FLOAT, r"^[-+]?(?:0|[1-9][0-9]*)\.[0-9]+$", "-+0123456789"),
)
for _tag, _pattern, _first in _CORE_SCALARS:
    _Loader.add_implicit_resolver(_tag, re.compile(_pattern), list(_first))  # pyright: ignore[reportUnknownMemberType] - PyYAML stubs


def load(text: str) -> dict[str, Any]:
    """Parse a YAML mapping within the limits above.

    Raises:
        YamlError: If the text is too large, holds control characters, doesn't parse, breaks a limit, has duplicate or
            non-text keys, or isn't a mapping.
    """
    if len(text.encode()) > MAX_BYTES:
        msg = f"larger than {MAX_BYTES // 1024} KiB"
        raise YamlError(msg)
    if CONTROLS.search(text.replace("\r\n", "\n")):
        msg = "holds control characters; only tabs and line breaks may appear"
        raise YamlError(msg)
    try:
        _scan(text)
        loader = _Loader(text)
        try:
            node = loader.get_single_node()
            data: object = _build(loader, node) if node is not None else {}
        finally:
            loader.dispose()
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        where = f" (line {mark.line + 1})" if mark else ""
        msg = f"doesn't parse{where}: {exc.problem or exc}"
        raise YamlError(msg) from exc
    except yaml.YAMLError as exc:
        msg = f"doesn't parse: {exc}"
        raise YamlError(msg) from exc
    except ValueError as exc:  # PyYAML's scanner lets an escape past U+10FFFF (`"\U00110000"`) escape as chr()'s error
        msg = f"doesn't parse: an escape names no character ({exc})"
        raise YamlError(msg) from exc
    if not isinstance(data, dict):
        msg = "must be a mapping of keys to values"
        raise YamlError(msg)
    return cast("dict[str, Any]", data)


def dump(data: object, *, header: str = "") -> str:
    """YAML text for plain data, keys in the given order, preceded by `header` as comment lines."""
    comments = "".join(f"# {line}\n" if line else "#\n" for line in header.splitlines())
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return comments + body


def _scan(text: str) -> None:
    """Refuse anchors, aliases, non-core tags and oversized structures before any node is built.

    Raises:
        YamlError: At the first one.
    """
    depth = 0
    nodes = 0
    parse = cast("Callable[..., Iterator[yaml.Event]]", yaml.parse)  # pyright: ignore[reportUnknownMemberType] - PyYAML stubs leave its Loader untyped
    for event in parse(text, Loader=_Loader):
        line = (event.start_mark.line + 1) if event.start_mark is not None else 1
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
            msg = f"anchors and aliases aren't allowed (line {line})"
            raise YamlError(msg)
        tag = getattr(event, "tag", None)
        if (isinstance(event, yaml.CollectionStartEvent) and tag is not None) or (
            isinstance(event, yaml.ScalarEvent) and tag not in {None, "!"} and tag not in _CORE_TAGS
        ):
            msg = f"the tag {tag} isn't allowed (line {line})"
            raise YamlError(msg)
        if isinstance(event, yaml.NodeEvent):
            nodes += 1
            if nodes > MAX_NODES:
                msg = f"more than {MAX_NODES} values"
                raise YamlError(msg)
        if isinstance(event, yaml.CollectionStartEvent):
            depth += 1
            if depth > MAX_DEPTH:
                msg = f"nested deeper than {MAX_DEPTH} levels (line {line})"
                raise YamlError(msg)
        elif isinstance(event, yaml.CollectionEndEvent):
            depth -= 1


def _build(loader: _Loader, node: Node) -> Any:  # noqa: ANN401 - YAML
    if isinstance(node, MappingNode):
        mapping: dict[str, Any] = {}
        for key_node, value_node in node.value:
            line = key_node.start_mark.line + 1
            if not isinstance(key_node, ScalarNode) or key_node.tag != _STR:
                msg = f"keys must be text (line {line})"
                raise YamlError(msg)
            key = _plain(cast("str", key_node.value), line)
            if key in mapping:
                msg = f"duplicate key {key!r} (line {line})"
                raise YamlError(msg)
            mapping[key] = _build(loader, value_node)
        return mapping
    if isinstance(node, SequenceNode):
        return [_build(loader, item) for item in node.value]
    value = loader.construct_object(node, deep=True)  # pyright: ignore[reportUnknownMemberType] - PyYAML stubs leave node untyped
    return _plain(value, node.start_mark.line + 1) if isinstance(value, str) else value


def _plain(text: str, line: int) -> str:
    r"""`text`, unless an escape such as `\e` decoded into a control character the text scan can't see.

    Raises:
        YamlError: If it holds a control character other than a tab or line break.
    """
    if CONTROLS.search(text):
        msg = f"control characters aren't allowed (line {line})"
        raise YamlError(msg)
    return text
