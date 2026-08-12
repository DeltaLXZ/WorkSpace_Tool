"""Parser for Element Template exports (``<TAG>_ET.xml``)."""

from __future__ import annotations

import logging
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import Element

from .models import ElementTemplate
from .xmlutil import attr, children, find_deep, iter_descendants, text_of

log = logging.getLogger(__name__)

# group element -> item element, in the Ustn_ElementParams block
_PARAMS = {
    "level": ("Levels", "Level"),
    "color": ("Colors", "Color"),
    "linestyle": ("LineStyles", "LineStyle"),
    "weight": ("Weights", "Weight"),
    "material": ("Materials", "Material"),
    "textstyle": ("TextStyles", "TextStyle"),
    "element_class": ("ElementClasses", "ElementClass"),
    "transparency": ("Transparencies", "Transparency"),
}


def parse_color_index(raw: str) -> str:
    """``0,1,11:[0,0,0]:\\\\\\`` -> ``11``. Returns the raw value if unrecognised."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    head = raw.split(":", 1)[0]
    parts = [p for p in head.split(",") if p.strip() != ""]
    if parts:
        return parts[-1].strip()
    return raw


def _param(node: Element, group: str, item: str) -> str:
    for grp in iter_descendants(node, group):
        for itm in children(grp, item):
            value = find_deep(itm, "Value")
            if value is not None:
                return text_of(value)
        value = find_deep(grp, "Value")
        if value is not None:
            return text_of(value)
    return ""


def _is_leaf(node: Element) -> bool:
    allow = attr(node, "allowChildren").strip().lower()
    if allow in ("false", "0"):
        return True
    if allow in ("true", "1"):
        return False
    return not children(node, "NodeData")


def _walk(node: Element, ancestry: list[str], out: dict, dupes: list[str], src: str) -> None:
    name = attr(node, "name")
    path = ancestry + ([name] if name else [])

    if _is_leaf(node):
        et_path = "\\".join(path)
        if not et_path:
            return
        tpl = ElementTemplate(et_path=et_path, name=name or et_path, source_file=src)
        params = find_deep(node, "ECXAttrData")
        if params is None:
            params = node
        for field, (group, item) in _PARAMS.items():
            value = _param(params, group, item)
            if field == "color":
                value = parse_color_index(value)
            setattr(tpl, field, value)
        if et_path in out:
            dupes.append(et_path)
        out[et_path] = tpl
        return

    for child in children(node, "NodeData"):
        _walk(child, path, out, dupes, src)


def parse_et(path: str | Path) -> tuple[dict[str, ElementTemplate], list[str], list[str]]:
    """Parse an Element Templates export. Returns (templates, duplicate paths, warnings)."""
    warnings: list[str] = []
    dupes: list[str] = []
    src = str(path)
    try:
        root = ET.parse(src).getroot()
    except (ET.ParseError, OSError) as exc:
        return {}, [], [f"ET export unreadable ({Path(src).name}): {exc}"]

    container = find_deep(root, "ElementTemplates")
    if container is None:
        return {}, [], [f"No <ElementTemplates> element in {Path(src).name}"]

    out: dict[str, ElementTemplate] = {}
    for node in children(container, "NodeData"):
        _walk(node, [], out, dupes, src)

    if dupes:
        warnings.append(f"{len(dupes)} duplicate element template path(s) in {Path(src).name}")
    log.debug("Parsed %d element templates from %s", len(out), src)
    return out, dupes, warnings
