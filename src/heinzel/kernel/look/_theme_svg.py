"""Validate the SVG logos and static avatars shipped by themes."""

import re
import xml.etree.ElementTree as ET

MAX_LOGO_BYTES = 200 * 1024
"""The largest logo file a theme may ship, 200 KB."""
_SVG = "{http://www.w3.org/2000/svg}"
_REFERENCES = frozenset({"href", "src"})


def _local(name: str) -> str:
    """An XML name without its `{namespace}`, lowercase."""
    return name.rpartition("}")[2].lower()


def _svg_text_problem(text: str) -> str | None:
    """Why the text of an SVG file could run or load something, or None; checked before it is parsed."""
    lowered = text.lower()
    for marker, problem in (
        ("<!doctype", "declares a DOCTYPE; entities aren't allowed"),
        ("<!entity", "declares an entity; entities aren't allowed"),
        ("<script", "holds a <script>"),
        ("<foreignobject", "holds a <foreignObject>"),
        ("xml-stylesheet", "links a stylesheet"),
        ("@import", "imports a stylesheet"),
        ("image-set(", "loads an image (image-set)"),
    ):
        if marker in lowered:
            return problem
    for match in re.finditer(r"url\(\s*['\"]?\s*(.?)", lowered):
        if match.group(1) != "#":
            return "refers to something outside itself (url(...)); only url(#id) is allowed"
    return None


def _svg_element_problem(element: ET.Element) -> str | None:
    """Why one element of an SVG file could run or load something, or None."""
    tag = _local(element.tag)
    if tag in {"script", "foreignobject"}:
        return f"holds a <{tag}>"
    for name, value in element.attrib.items():
        attribute = _local(name)
        if attribute.startswith("on"):
            return f"has an event handler ({attribute}=)"
        if attribute in _REFERENCES and not value.strip().startswith("#"):
            return f"refers to something outside itself ({attribute}={value[:40]!r}); only #id is allowed"
        if tag in {"set", "animate"} and attribute == "attributename":
            target = value.strip().lower().rpartition(":")[2]
            if target in _REFERENCES or target.startswith("on"):
                return f"animates {target}, which could point outside the file"
    return _svg_css_problem(tag, element)


def _svg_css_problem(tag: str, element: ET.Element) -> str | None:
    """Why the decoded text and style of one element could load something, or None."""
    styles = [value for name, value in element.attrib.items() if _local(name) == "style"]
    if tag == "style":
        styles.append(element.text or "")
    if any("\\" in style for style in styles):
        return f"escapes characters in a {'<style>' if tag == 'style' else 'style'} (\\), which could hide a url(...)"
    # Character references are decoded by now: `&#117;rl(` is `url(` here, so the text checks run again.
    decoded = [element.text, element.tail, *element.attrib.values()]
    return next((problem for text in decoded if text and (problem := _svg_text_problem(text))), None)


def svg_problem(content: bytes) -> str | None:
    """Why `content` isn't a safe SVG logo, or None.

    A logo is UTF-8 XML of at most 200 KB whose root is `<svg>`, without a DOCTYPE or entities, a `<script>`, a
    `<foreignObject>`, an event-handler attribute (`on...=`), or a reference (`href`, `xlink:href`, `src`, `url(...)`)
    to anything but an element of the same file (`#id`): no external file, no `data:` URI.
    """
    if len(content) > MAX_LOGO_BYTES:
        return f"is larger than {MAX_LOGO_BYTES // 1024} KB; simplify it, e.g. with svgo"
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return "isn't UTF-8 text"
    return _svg_text_problem(text) or _svg_tree_problem(content)


def _svg_tree_problem(content: bytes) -> str | None:
    """Why the parsed elements of an SVG file aren't a safe logo, or None; its text passed `_svg_text_problem`."""
    try:
        root = ET.fromstring(content)  # noqa: S314 - DOCTYPE and entities are refused before: nothing expands
    except ET.ParseError as exc:
        return f"isn't well-formed XML: {exc}"
    if root.tag not in {"svg", f"{_SVG}svg"}:
        return "isn't an SVG image: its root element isn't <svg>"
    return next((problem for element in root.iter() if (problem := _svg_element_problem(element))), None)


def require_static_avatar(content: bytes, file: str) -> None:
    """Reject styling and animation in previously validated SVG avatar bytes.

    Raises:
        ValueError: If the avatar contains styling or animation instructions.
    """
    root = ET.fromstring(content)  # noqa: S314 - read_logo already refused DOCTYPE/entities and checked XML
    for element in root.iter():
        tag = _local(element.tag)
        if tag.startswith("animate") or tag in {"set", "discard", "style"}:
            msg = f"Avatar file {file!r} must be static SVG without animation or stylesheets"
            raise ValueError(msg)
        if any(_local(name) == "style" for name in element.attrib):
            msg = f"Avatar file {file!r} must use SVG presentation attributes instead of inline styles"
            raise ValueError(msg)
