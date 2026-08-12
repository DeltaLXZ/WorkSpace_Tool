"""Parser for Feature Definition exports (``<TAG>_FD.xml``)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from .constants import SUFFIX_MAP
from .models import FDRef, FeatureDefinition
from .xmlutil import attr, children, find_deep, iter_descendants, tag_of

log = logging.getLogger(__name__)

_REF_RE = re.compile(r"^(?P<body>.*?)>~(?P<suf>\w+)$")


def split_symbology_ref(raw: str) -> tuple[str, str, str]:
    """Split a ``TaggedFeatureSymbologyRef`` featurePath.

    ``Solid\\Abutments\\Piles_steel>~Sol`` -> ``("Solid", "Abutments", "Piles_steel")``.
    The leading token repeats the aspect type and is dropped; ``resolve`` keeps the
    undropped form as a fallback candidate.
    """
    raw = (raw or "").strip()
    match = _REF_RE.match(raw)
    if match:
        body = match.group("body")
        stype = SUFFIX_MAP.get(match.group("suf"), match.group("suf"))
    else:
        body, stype = raw, ""

    tokens = [t for t in body.replace("/", "\\").split("\\") if t]
    if not tokens:
        return stype, "", ""
    if len(tokens) == 1:
        return stype, "", tokens[0]

    name = tokens[-1]
    middle = tokens[1:-1]
    return stype, "\\".join(middle), name


def embedded_dgnlib_name(path: str | Path) -> str:
    """Read the ``file="...dgnlib"`` attribute from the export root, if present."""
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError):
        return ""
    value = attr(root, "file")
    if not value:
        return ""
    return Path(value.replace("\\", "/")).stem


def parse_fd(path: str | Path) -> tuple[list[FeatureDefinition], list[str]]:
    """Parse a Feature Definitions export. Returns (definitions, warnings)."""
    warnings: list[str] = []
    src = str(path)
    try:
        root = ET.parse(src).getroot()
    except (ET.ParseError, OSError) as exc:
        return [], [f"FD export unreadable ({Path(src).name}): {exc}"]

    container = find_deep(root, "FeatureDefinitions")
    if container is None:
        return [], [f"No <FeatureDefinitions> element in {Path(src).name}"]

    out: list[FeatureDefinition] = []
    for node in children(container, "FeatureDefinition"):
        fd = FeatureDefinition(
            name=attr(node, "name"),
            featurepath=attr(node, "featurePath"),
            description=attr(node, "description"),
            source_file=src,
        )

        app = find_deep(node, "ApplicationFeatureDefinitions")
        holder = node
        if app is not None:
            kids = children(app)
            if kids:
                holder = kids[0]
                fd.item_type = tag_of(holder)
                fd.provider = attr(holder, "ApplicationObjectSettingsProviderName")

        seen: set[tuple[str, str, str, str]] = set()
        for ref_node in iter_descendants(holder, "TaggedFeatureSymbologyRef"):
            raw = attr(ref_node, "featurePath")
            if not raw:
                continue
            stype, fs_path, fs_name = split_symbology_ref(raw)
            if not fs_name:
                warnings.append(f"FD '{fd.name}': unparsable symbology ref '{raw}'")
                continue
            key = (stype, fs_path, fs_name, raw)
            if key in seen:
                continue
            seen.add(key)
            fd.refs.append(FDRef(stype=stype, fs_featurepath=fs_path, fs_name=fs_name, raw=raw))

        if not fd.name:
            warnings.append(f"Skipped an unnamed FeatureDefinition in {Path(src).name}")
            continue
        out.append(fd)

    log.debug("Parsed %d feature definitions from %s", len(out), src)
    return out, warnings
