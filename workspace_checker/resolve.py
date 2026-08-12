"""Assembly of standard sets and resolution of the FD -> FS -> ET -> Level chain."""

from __future__ import annotations

import logging
from pathlib import Path

from .constants import ROLE_ET, ROLE_FD, ROLE_FS, ROLE_LEVEL
from .models import FDRef, IntegrityIssue, ResolvedRow, StandardSet
from .parse_et import parse_et
from .parse_fd import parse_fd
from .parse_fs import parse_fs
from .parse_levels import parse_levels

log = logging.getLogger(__name__)

ISSUE_FS_MISSING = "FS reference not found"
ISSUE_ET_MISSING = "Element template not found"
ISSUE_LEVEL_MISSING = "Level not defined in level library"
ISSUE_FS_NO_ET = "Feature symbology has no element template reference"
ISSUE_ET_NO_LEVEL = "Element template has no level assigned"
ISSUE_FALLBACK = "FS reference matched by fallback path"
ISSUE_NOT_PROVIDED = "Input not provided"

BROKEN_ISSUES = frozenset({ISSUE_FS_MISSING, ISSUE_ET_MISSING, ISSUE_LEVEL_MISSING})

STATUS_OK = "OK"
STATUS_FS_MISSING = "FS MISSING"
STATUS_ET_MISSING = "ET MISSING"
STATUS_NOT_PROVIDED = "NOT PROVIDED"


def load_standard_set(tag: str, role_files: dict[str, list[Path]]) -> StandardSet:
    """Build a StandardSet from grouped export files; multiple files per role are merged."""
    std = StandardSet(tag=tag)

    for path in role_files.get(ROLE_FD, []):
        fds, warns = parse_fd(path)
        std.fds.extend(fds)
        std.parse_warnings.extend(warns)
        if fds or not warns:
            std.inputs_present.add(ROLE_FD)
        std.input_files.setdefault(ROLE_FD, str(path))

    for path in role_files.get(ROLE_FS, []):
        fs, warns = parse_fs(path)
        std.fs.update(fs)
        std.parse_warnings.extend(warns)
        if fs or not warns:
            std.inputs_present.add(ROLE_FS)
        std.input_files.setdefault(ROLE_FS, str(path))

    for path in role_files.get(ROLE_ET, []):
        et, dupes, warns = parse_et(path)
        std.et.update(et)
        std.duplicate_et_paths.extend(dupes)
        std.parse_warnings.extend(warns)
        if et or not warns:
            std.inputs_present.add(ROLE_ET)
        std.input_files.setdefault(ROLE_ET, str(path))

    for path in role_files.get(ROLE_LEVEL, []):
        levels, dupes, warns = parse_levels(path)
        std.levels.update(levels)
        std.duplicate_levels.extend(dupes)
        std.parse_warnings.extend(warns)
        if levels or not warns:
            std.inputs_present.add(ROLE_LEVEL)
        std.input_files.setdefault(ROLE_LEVEL, str(path))

    log.info(
        "Set '%s': %d FD, %d FS, %d ET, %d levels (inputs: %s)",
        tag,
        len(std.fds),
        len(std.fs),
        len(std.et),
        len(std.levels),
        ",".join(sorted(std.inputs_present)) or "none",
    )
    return std


def candidate_keys(ref: FDRef) -> list[tuple[str, str, str]]:
    """Lookup keys for a symbology reference, most-specific first.

    The primary key drops the leading aspect token from the exported path. Agencies
    that do not follow that convention are matched by the fallbacks instead.
    """
    keys: list[tuple[str, str, str]] = [(ref.stype, ref.fs_featurepath, ref.fs_name)]

    body = ref.raw.split(">~", 1)[0] if ">~" in ref.raw else ref.raw
    tokens = [t for t in body.replace("/", "\\").split("\\") if t]
    if len(tokens) >= 2:
        keys.append((ref.stype, "\\".join(tokens[:-1]), tokens[-1]))
    keys.append((ref.stype, "", ref.fs_name))

    seen: set[tuple[str, str, str]] = set()
    ordered: list[tuple[str, str, str]] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def _row(fd, ref: FDRef, **kw) -> ResolvedRow:
    return ResolvedRow(
        fd_name=fd.name,
        fd_item_type=fd.item_type,
        fd_path=fd.fd_path,
        stype=ref.stype,
        fs_featurepath=ref.fs_featurepath,
        fs_name=ref.fs_name,
        **kw,
    )


def resolve(std: StandardSet) -> tuple[list[ResolvedRow], list[IntegrityIssue]]:
    """Flatten every FD aspect into rows, recording every unresolved link."""
    rows: list[ResolvedRow] = []
    issues: list[IntegrityIssue] = []
    have_fs = std.has(ROLE_FS)
    have_et = std.has(ROLE_ET)
    have_levels = std.has(ROLE_LEVEL)

    for fd in std.fds:
        if not fd.refs:
            issues.append(
                IntegrityIssue(fd.name, "", "", "", "FD has no symbology references", fd.fd_path)
            )
            rows.append(_row(fd, FDRef("", "", "", ""), status=STATUS_FS_MISSING))
            continue

        for ref in fd.refs:
            if not have_fs:
                issues.append(
                    IntegrityIssue(
                        fd.name, ref.stype, ref.fs_featurepath, ref.fs_name,
                        ISSUE_NOT_PROVIDED, "FS export not provided",
                    )
                )
                rows.append(_row(fd, ref, status=STATUS_NOT_PROVIDED))
                continue

            fs = None
            matched_key = None
            for key in candidate_keys(ref):
                if key in std.fs:
                    fs, matched_key = std.fs[key], key
                    break

            if fs is None:
                issues.append(
                    IntegrityIssue(
                        fd.name, ref.stype, ref.fs_featurepath, ref.fs_name,
                        ISSUE_FS_MISSING, ref.raw,
                    )
                )
                rows.append(_row(fd, ref, status=STATUS_FS_MISSING))
                continue

            if matched_key != (ref.stype, ref.fs_featurepath, ref.fs_name):
                issues.append(
                    IntegrityIssue(
                        fd.name, ref.stype, ref.fs_featurepath, ref.fs_name,
                        ISSUE_FALLBACK, f"{ref.raw} -> {matched_key}",
                    )
                )

            if not fs.refs:
                issues.append(
                    IntegrityIssue(
                        fd.name, ref.stype, ref.fs_featurepath, ref.fs_name,
                        ISSUE_FS_NO_ET, "",
                    )
                )
                rows.append(_row(fd, ref, status=STATUS_ET_MISSING))
                continue

            for et_ref in fs.refs:
                if not have_et:
                    rows.append(
                        _row(
                            fd, ref,
                            et_relationship=et_ref.relationship,
                            et_path=et_ref.et_path,
                            status=STATUS_NOT_PROVIDED,
                        )
                    )
                    continue

                tpl = std.et.get(et_ref.et_path)
                if tpl is None:
                    issues.append(
                        IntegrityIssue(
                            fd.name, ref.stype, ref.fs_featurepath, ref.fs_name,
                            ISSUE_ET_MISSING, et_ref.et_path,
                        )
                    )
                    rows.append(
                        _row(
                            fd, ref,
                            et_relationship=et_ref.relationship,
                            et_path=et_ref.et_path,
                            status=STATUS_ET_MISSING,
                        )
                    )
                    continue

                level = std.levels.get(tpl.level) if tpl.level else None
                if have_levels and tpl.level and level is None:
                    issues.append(
                        IntegrityIssue(
                            fd.name, ref.stype, ref.fs_featurepath, ref.fs_name,
                            ISSUE_LEVEL_MISSING, f"{tpl.et_path} -> {tpl.level}",
                        )
                    )
                if not tpl.level:
                    issues.append(
                        IntegrityIssue(
                            fd.name, ref.stype, ref.fs_featurepath, ref.fs_name,
                            ISSUE_ET_NO_LEVEL, tpl.et_path,
                        )
                    )

                rows.append(
                    _row(
                        fd, ref,
                        et_relationship=et_ref.relationship,
                        et_path=tpl.et_path,
                        et_name=tpl.name,
                        level=tpl.level,
                        bylevel_color=level.bylevel_color if level else "",
                        bylevel_weight=level.bylevel_weight if level else "",
                        bylevel_style=level.bylevel_style if level else "",
                        et_color=tpl.color,
                        et_weight=tpl.weight,
                        et_linestyle=tpl.linestyle,
                        material=tpl.material,
                        textstyle=tpl.textstyle,
                        status=STATUS_OK,
                    )
                )

    return rows, issues


def build_combined(std: StandardSet, rows: list[ResolvedRow]) -> list[dict]:
    """One summary record per feature definition."""
    by_fd: dict[str, list[ResolvedRow]] = {}
    for row in rows:
        by_fd.setdefault(row.fd_name, []).append(row)

    out: list[dict] = []
    for fd in std.fds:
        fd_rows = by_fd.get(fd.name, [])
        stypes = sorted({r.stype for r in fd_rows if r.stype})
        templates = sorted({r.et_path for r in fd_rows if r.et_path})
        levels = sorted({r.level for r in fd_rows if r.level})
        colors = sorted({r.bylevel_color for r in fd_rows if r.bylevel_color})

        summary_parts = []
        for stype in stypes:
            lv = sorted({r.level for r in fd_rows if r.stype == stype and r.level})
            summary_parts.append(f"{stype}: {', '.join(lv) if lv else '(none)'}")

        out.append(
            {
                "FD_Name": fd.name,
                "FD_ItemType": fd.item_type,
                "FD_Path": fd.fd_path,
                "SymbologyTypes": ", ".join(stypes),
                "Num_SymRefs": len(fd.refs),
                "Num_ElementTemplates": len(templates),
                "Distinct_Levels": ", ".join(levels),
                "Distinct_ByLevel_Colors": ", ".join(colors),
                "Aspect_Level_Summary": " | ".join(summary_parts),
            }
        )
    return out
