"""Namespace-tolerant ElementTree helpers.

Bentley exports are namespaced, but hand-edited or re-saved files sometimes are not.
Everything here matches on the local tag name so both cases parse identically.
"""

from __future__ import annotations

from typing import Iterator
from xml.etree.ElementTree import Element


def local(tag: str) -> str:
    """Return the local part of a possibly namespaced tag."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def tag_of(elem: Element) -> str:
    return local(elem.tag)


def children(elem: Element, name: str | None = None) -> list[Element]:
    if name is None:
        return [c for c in elem if isinstance(c.tag, str)]
    return [c for c in elem if isinstance(c.tag, str) and local(c.tag) == name]


def first_child(elem: Element, name: str) -> Element | None:
    for c in elem:
        if isinstance(c.tag, str) and local(c.tag) == name:
            return c
    return None


def iter_descendants(elem: Element, name: str) -> Iterator[Element]:
    for node in elem.iter():
        if isinstance(node.tag, str) and local(node.tag) == name:
            if node is not elem:
                yield node


def find_deep(root: Element, name: str) -> Element | None:
    if isinstance(root.tag, str) and local(root.tag) == name:
        return root
    for node in root.iter():
        if isinstance(node.tag, str) and local(node.tag) == name:
            return node
    return None


def attr(elem: Element, name: str, default: str = "") -> str:
    """Attribute lookup that ignores namespace prefixes and is case-insensitive."""
    if name in elem.attrib:
        return elem.attrib[name]
    lowered = name.lower()
    for key, value in elem.attrib.items():
        if local(key).lower() == lowered:
            return value
    return default


def text_of(elem: Element | None) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()
