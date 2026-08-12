"""Detection of export role (FD/FS/ET/Levels) and standard-set tag."""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from pathlib import Path

from .constants import ALL_ROLES, ROLE_ET, ROLE_FD, ROLE_FS, ROLE_LEVEL, ROLE_PATTERNS
from .parse_fd import embedded_dgnlib_name

log = logging.getLogger(__name__)

_COMPILED = {role: re.compile(pattern) for role, pattern in ROLE_PATTERNS.items()}

_SNIFF_MARKERS = (
    (ROLE_FD, "<FeatureDefinitions"),
    (ROLE_FS, "<FeatureSymbologies"),
    (ROLE_ET, "<ElementTemplates"),
)

# Underscore is a word character, so \b will not fire between "Bridge" and "_Features".
_DESCRIPTOR_RE = re.compile(
    r"[_\- ](bridge|road|roadway|rail|features?|levels?|elem|element|temp|templates?|imperial|"
    r"metric|standards?|civil|dgnlib)(?![a-z0-9]).*$",
    re.IGNORECASE,
)


def _sniff_role(path: Path) -> str | None:
    """Identify a file by content when the filename is uninformative."""
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as fh:
            head = fh.read(16384)
    except OSError:
        return None
    for role, marker in _SNIFF_MARKERS:
        if marker in head:
            return role
    if re.search(r"%SECTION\s*,\s*Levels", head, re.IGNORECASE):
        return ROLE_LEVEL
    return None


def classify(path: str | os.PathLike) -> tuple[str | None, str]:
    """Return (role, filename-derived tag) for a candidate export file."""
    p = Path(path)
    if p.suffix.lower() not in (".xml", ".csv"):
        return None, ""

    for role, rx in _COMPILED.items():
        match = rx.match(p.name)
        if match:
            tag = _clean_tag(match.group("tag"))
            return role, tag

    # Sniffed by content: the filename told us nothing, so claim no tag and let the
    # embedded dgnlib name decide.
    role = _sniff_role(p)
    if role is None:
        return None, ""
    return role, ""


def _clean_tag(raw: str) -> str:
    tag = (raw or "").strip().strip("_-. ")
    tag = _DESCRIPTOR_RE.sub("", tag)
    tag = tag.strip("_-. ")
    return tag


def _longest_common_prefix(names: list[str]) -> str:
    if not names:
        return ""
    prefix = os.path.commonprefix(names)
    return _clean_tag(prefix)


def resolve_tag(
    role_files: dict[str, list[Path]],
    filename_tags: dict[str, str],
    forced: str | None = None,
    folder_hint: str = "",
) -> tuple[str, list[str]]:
    """Resolve the standard-set tag. Priority: forced, filename, embedded, folder, prefix."""
    warnings: list[str] = []
    if forced:
        return forced, warnings

    ordered = [ROLE_FD, ROLE_FS, ROLE_ET, ROLE_LEVEL]
    candidates = [filename_tags[r] for r in ordered if filename_tags.get(r)]
    if candidates:
        distinct = {c.lower() for c in candidates}
        if len(distinct) > 1:
            warnings.append(
                "Input filenames disagree on the standard-set tag "
                f"({', '.join(sorted(set(candidates)))}); using '{candidates[0]}'."
            )
        return candidates[0], warnings

    for role in (ROLE_FD, ROLE_FS):
        for path in role_files.get(role, []):
            embedded = embedded_dgnlib_name(path)
            if embedded:
                tag = _clean_tag(embedded)
                if tag:
                    return tag, warnings

    if folder_hint:
        tag = _clean_tag(folder_hint)
        if tag:
            return tag, warnings

    names = [p.stem for paths in role_files.values() for p in paths]
    prefix = _longest_common_prefix(names)
    return prefix or "STANDARD", warnings


def group_by_tag(
    paths: list[str | os.PathLike], forced: str | None = None
) -> tuple[dict[str, dict[str, list[Path]]], list[str]]:
    """Group export files into standard sets keyed by detected tag."""
    warnings: list[str] = []
    buckets: dict[str, dict[str, list[Path]]] = defaultdict(lambda: defaultdict(list))
    tags_seen: dict[str, dict[str, str]] = defaultdict(dict)

    for raw in paths:
        p = Path(raw)
        if not p.is_file():
            continue
        role, tag = classify(p)
        if role is None:
            continue
        key = forced or (tag or "STANDARD")
        buckets[key][role].append(p)
        tags_seen[key].setdefault(role, tag)

    resolved: dict[str, dict[str, list[Path]]] = {}
    for key, role_files in buckets.items():
        tag, warns = resolve_tag(dict(role_files), tags_seen[key], forced)
        warnings.extend(warns)
        target = resolved.setdefault(tag, defaultdict(list))
        for role, files in role_files.items():
            target[role].extend(files)

    for tag, role_files in resolved.items():
        missing = [r for r in ALL_ROLES if r not in role_files]
        if missing:
            log.warning("Standard set '%s' is missing input(s): %s", tag, ", ".join(missing))

    return {t: dict(rf) for t, rf in resolved.items()}, warnings
