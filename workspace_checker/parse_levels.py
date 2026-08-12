"""Parser for Level Manager CSV exports (``<TAG>_Level.csv``)."""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

from .models import Level

log = logging.getLogger(__name__)

_SECTION_RE = re.compile(r"^\s*%SECTION\s*,\s*(?P<name>[^,]*)", re.IGNORECASE)

# normalised header token -> Level field
_COLUMNS = {
    "name": "name",
    "number": "number",
    "description": "description",
    "bylevelcolor": "bylevel_color",
    "bylevelweight": "bylevel_weight",
    "bylevelstyle": "bylevel_style",
    "bylevellinestyle": "bylevel_style",
    "plot": "plot",
    "globaldisplay": "global_display",
    "globalfreeze": "global_freeze",
}


def _norm(token: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (token or "").lower())


def _read_lines(path: str | Path) -> list[str]:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=encoding, newline="") as fh:
                return fh.read().splitlines()
        except UnicodeDecodeError:
            continue
    return []


def parse_levels(path: str | Path) -> tuple[dict[str, Level], list[str], list[str]]:
    """Parse the ``Levels`` section of a level export.

    Returns (levels-by-name, duplicate names, warnings). The header row is separated
    from the section marker by one or more blank lines, which are skipped.
    """
    warnings: list[str] = []
    dupes: list[str] = []
    src = str(path)
    lines = _read_lines(src)
    if not lines:
        return {}, [], [f"Level export unreadable or empty ({Path(src).name})"]

    start = None
    for idx, line in enumerate(lines):
        match = _SECTION_RE.match(line)
        if match and _norm(match.group("name")) == "levels":
            start = idx + 1
            break
    if start is None:
        return {}, [], [f"No '%SECTION,Levels' block in {Path(src).name}"]

    # Skip blank lines between the section marker and the real header row.
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines):
        return {}, [], [f"'%SECTION,Levels' block is empty in {Path(src).name}"]

    body: list[str] = []
    for line in lines[start:]:
        if _SECTION_RE.match(line):
            break
        body.append(line)

    rows = list(csv.reader(body))
    header: list[str] = []
    for row in rows:
        if row and _norm(row[0]) == "name":
            header = [_norm(c) for c in row]
            rows = rows[rows.index(row) + 1 :]
            break
    if not header:
        return {}, [], [f"No level header row found in {Path(src).name}"]

    out: dict[str, Level] = {}
    for row in rows:
        if not row or not any(c.strip() for c in row):
            continue
        record = {header[i]: row[i].strip() for i in range(min(len(header), len(row)))}
        name = record.get("name", "")
        if not name:
            continue
        level = Level(name=name, source_file=src)
        for token, field in _COLUMNS.items():
            if token in record and record[token] != "":
                setattr(level, field, record[token])
        if name in out:
            dupes.append(name)
        out[name] = level

    if dupes:
        warnings.append(f"{len(dupes)} duplicate level name(s) in {Path(src).name}")
    log.debug("Parsed %d levels from %s", len(out), src)
    return out, dupes, warnings
