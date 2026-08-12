"""Parser for Feature Symbology exports (``<TAG>_FS.xml``)."""

from __future__ import annotations

import logging
from pathlib import Path
from xml.etree import ElementTree as ET

from .constants import REL_MAP
from .models import ETRef, FeatureSymbology
from .xmlutil import attr, children, find_deep, iter_descendants, tag_of

log = logging.getLogger(__name__)


def short_relationship(raw: str) -> str:
    """Map a verbose relationship name to its short label, tolerating unknowns."""
    raw = (raw or "").strip()
    if raw in REL_MAP:
        return REL_MAP[raw]
    tail = raw.rsplit("__", 1)[-1]
    for suffix in ("ElementTemplateHolder", "ElementTemplate"):
        if tail.endswith(suffix):
            tail = tail[: -len(suffix)]
            break
    return tail or "Default"


def parse_fs(path: str | Path) -> tuple[dict[tuple[str, str, str], FeatureSymbology], list[str]]:
    """Parse a Feature Symbologies export. Returns (symbologies-by-key, warnings)."""
    warnings: list[str] = []
    src = str(path)
    try:
        root = ET.parse(src).getroot()
    except (ET.ParseError, OSError) as exc:
        return {}, [f"FS export unreadable ({Path(src).name}): {exc}"]

    container = find_deep(root, "FeatureSymbologies")
    if container is None:
        return {}, [f"No <FeatureSymbologies> element in {Path(src).name}"]

    out: dict[tuple[str, str, str], FeatureSymbology] = {}
    for node in children(container, "FeatureSymbology"):
        kids = children(node)
        if not kids:
            warnings.append(f"FeatureSymbology '{attr(node, 'name')}' has no aspect child")
            continue

        aspect = kids[0]
        stype = tag_of(aspect)
        if stype.endswith("FeatureSymbology"):
            stype = stype[: -len("FeatureSymbology")]

        fs = FeatureSymbology(
            stype=stype,
            featurepath=attr(node, "featurePath"),
            name=attr(node, "name"),
            source_file=src,
        )

        seen: set[tuple[str, str]] = set()
        for ref in iter_descendants(aspect, "ElementTemplateRef"):
            et_path = attr(ref, "featurePath")
            if not et_path:
                continue
            rel = short_relationship(attr(ref, "relationship"))
            key = (rel, et_path)
            if key in seen:
                continue
            seen.add(key)
            fs.refs.append(ETRef(relationship=rel, et_path=et_path))

        if fs.key in out:
            warnings.append(f"Duplicate feature symbology key {fs.key} in {Path(src).name}")
        out[fs.key] = fs

    log.debug("Parsed %d feature symbologies from %s", len(out), src)
    return out, warnings
