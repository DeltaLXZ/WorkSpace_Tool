"""Standards health checks over a resolved standard set."""

from __future__ import annotations

from .config import Settings
from .constants import ROLE_ET, ROLE_FD, ROLE_FS, ROLE_LEVEL
from .models import CheckResult, IntegrityIssue, ResolvedRow, Severity, StandardSet
from .resolve import BROKEN_ISSUES

LAYER = "standards"

_NOT_PROVIDED = "input not provided"

GUIDANCE = {
    "reference_integrity": (
        "Open Integrity_Checks and work the list. 'FS reference not found' means a feature "
        "definition points at a symbology that was not exported or was renamed; 'Element "
        "template not found' means the symbology points at a template path that no longer "
        "exists. Both draw nothing predictable in the model."
    ),
    "fd_coverage": (
        "These feature definitions resolve to no element template, so anything placed with "
        "them inherits the active level and symbology. Give each one a symbology with at "
        "least a Default element template."
    ),
    "level_completeness": (
        "Element templates name levels that do not exist in the level library. Either add "
        "the levels or repoint the templates. Until then elements land on an undefined "
        "level and pick up unpredictable display."
    ),
    "bylevel_symbology": (
        "These levels carry no ByLevel colour, so anything drawn ByLevel falls back to the "
        "element or view default. Set a ByLevel colour unless the level is deliberately "
        "inheriting."
    ),
    "orphan_element_template": (
        "Templates defined but referenced by no symbology. Harmless if they are intentional "
        "spares - add them to ignore.orphan_element_template_paths in the config to silence "
        "this - otherwise delete them so the library stays honest."
    ),
    "orphan_feature_symbology": (
        "Symbologies not tied to any feature definition. Nothing can place them, so either "
        "wire them to a feature definition or remove them."
    ),
    "duplicate_level_names": (
        "Two level entries share a name, so the later one silently overwrote the earlier. "
        "Confirm which is intended in the level library."
    ),
    "duplicate_element_template_paths": (
        "Two templates resolve to the same path, making it ambiguous which one applies. "
        "Rename one."
    ),
    "empty_level_reference": (
        "These templates assign no level, so elements stay on whatever level is active when "
        "they are placed. Assign a level unless that is genuinely intended."
    ),
    "plot_flag_sanity": (
        "Levels used by element templates are flagged not to plot. Correct for construction "
        "geometry, a defect for anything meant to appear on a sheet. Check each against its "
        "intent."
    ),
}


def _skip(name: str, title: str, missing: str) -> CheckResult:
    return CheckResult(
        name=name,
        title=title,
        severity=Severity.NOT_EVALUATED,
        result="Not evaluated",
        detail=f"{_NOT_PROVIDED}: {missing}",
        layer=LAYER,
    )


def _used_levels(std: StandardSet, settings: Settings) -> set[str]:
    return {
        t.level
        for t in std.et.values()
        if t.level and not settings.ignored("levels", t.level)
    }


def _referenced_et_paths(std: StandardSet) -> set[str]:
    return {ref.et_path for fs in std.fs.values() for ref in fs.refs}


def _referenced_fs_keys(std: StandardSet) -> set[tuple[str, str, str]]:
    from .resolve import candidate_keys

    used: set[tuple[str, str, str]] = set()
    for fd in std.fds:
        for ref in fd.refs:
            for key in candidate_keys(ref):
                if key in std.fs:
                    used.add(key)
                    break
    return used


def run_standards_checks(
    std: StandardSet,
    rows: list[ResolvedRow],
    issues: list[IntegrityIssue],
    settings: Settings,
) -> list[CheckResult]:
    """Evaluate every standards check, honouring configured severities and ignores."""
    checks: list[CheckResult] = []
    has_fd = std.has(ROLE_FD)
    has_fs = std.has(ROLE_FS)
    has_et = std.has(ROLE_ET)
    has_levels = std.has(ROLE_LEVEL)

    # 1 - reference integrity ------------------------------------------------ #
    if not (has_fd and has_fs):
        checks.append(
            _skip("reference_integrity", "Reference integrity", "FD and FS exports required")
        )
    else:
        broken = [i for i in issues if i.issue in BROKEN_ISSUES]
        if broken:
            checks.append(
                CheckResult(
                    "reference_integrity",
                    "Reference integrity",
                    settings.severity("reference_integrity", Severity.FAIL),
                    f"{len(broken)} broken link(s)",
                    "Every FD -> FS -> ET -> Level hop must resolve. See Integrity_Checks.",
                    [f"{i.fd_name} [{i.stype}] {i.issue}: {i.detail}" for i in broken[:25]],
                    LAYER,
                )
            )
        else:
            checks.append(
                CheckResult(
                    "reference_integrity",
                    "Reference integrity",
                    Severity.PASS,
                    "All references resolve",
                    f"{len(rows)} resolved row(s), 0 broken links.",
                    layer=LAYER,
                )
            )

    # 2 - FD coverage -------------------------------------------------------- #
    if not (has_fd and has_fs and has_et):
        checks.append(_skip("fd_coverage", "Feature definition coverage", "FD, FS and ET required"))
    else:
        resolved_fds = {r.fd_name for r in rows if r.et_path and r.status == "OK"}
        uncovered = [fd.name for fd in std.fds if fd.name not in resolved_fds]
        if uncovered:
            checks.append(
                CheckResult(
                    "fd_coverage",
                    "Feature definition coverage",
                    settings.severity("fd_coverage", Severity.FAIL),
                    f"{len(uncovered)} of {len(std.fds)} FD resolve to no element template",
                    "These feature definitions will draw with no controlled symbology.",
                    uncovered[:25],
                    LAYER,
                )
            )
        else:
            checks.append(
                CheckResult(
                    "fd_coverage",
                    "Feature definition coverage",
                    Severity.PASS,
                    f"All {len(std.fds)} feature definitions resolve",
                    layer=LAYER,
                )
            )

    # 3 - level library completeness ----------------------------------------- #
    if not (has_et and has_levels):
        checks.append(
            _skip("level_completeness", "Level library completeness", "ET and Levels required")
        )
    else:
        used = _used_levels(std, settings)
        missing = sorted(name for name in used if name not in std.levels)
        if missing:
            checks.append(
                CheckResult(
                    "level_completeness",
                    "Level library completeness",
                    settings.severity("level_completeness", Severity.FAIL),
                    f"{len(missing)} level(s) used by templates are not in the level library",
                    "Elements land on an undefined level and inherit unpredictable symbology.",
                    missing[:25],
                    LAYER,
                )
            )
        else:
            checks.append(
                CheckResult(
                    "level_completeness",
                    "Level library completeness",
                    Severity.PASS,
                    f"All {len(used)} used level(s) exist in the level library",
                    layer=LAYER,
                )
            )

    # 4 - ByLevel symbology --------------------------------------------------- #
    if not (has_et and has_levels):
        checks.append(_skip("bylevel_symbology", "ByLevel symbology", "ET and Levels required"))
    else:
        used = _used_levels(std, settings)
        no_color = sorted(
            name
            for name in used
            if name in std.levels and not std.levels[name].bylevel_color.strip()
        )
        if no_color:
            checks.append(
                CheckResult(
                    "bylevel_symbology",
                    "ByLevel symbology",
                    settings.severity("bylevel_symbology", Severity.WARN),
                    f"{len(no_color)} used level(s) have no ByLevel colour",
                    "Display falls back to the element or view default.",
                    no_color[:25],
                    LAYER,
                )
            )
        else:
            checks.append(
                CheckResult(
                    "bylevel_symbology",
                    "ByLevel symbology",
                    Severity.PASS,
                    "Every used level defines a ByLevel colour",
                    layer=LAYER,
                )
            )

    # 5 - orphan element templates -------------------------------------------- #
    if not (has_fs and has_et):
        checks.append(_skip("orphan_element_template", "Element template usage", "FS and ET required"))
    else:
        referenced = _referenced_et_paths(std)
        orphans = sorted(
            p
            for p in std.et
            if p not in referenced and not settings.ignored("orphan_element_template_paths", p)
        )
        if orphans:
            checks.append(
                CheckResult(
                    "orphan_element_template",
                    "Element template usage",
                    settings.severity("orphan_element_template", Severity.WARN),
                    f"{len(orphans)} element template(s) are defined but never referenced",
                    "Harmless if intentional spares; otherwise dead weight in the library.",
                    orphans[:25],
                    LAYER,
                )
            )
        else:
            checks.append(
                CheckResult(
                    "orphan_element_template",
                    "Element template usage",
                    Severity.PASS,
                    "Every element template is referenced",
                    layer=LAYER,
                )
            )

    # 6 - orphan feature symbologies ------------------------------------------ #
    if not (has_fd and has_fs):
        checks.append(
            _skip("orphan_feature_symbology", "Feature symbology usage", "FD and FS required")
        )
    else:
        used_keys = _referenced_fs_keys(std)
        orphans = sorted(f"{k[0]}|{k[1]}|{k[2]}" for k in std.fs if k not in used_keys)
        if orphans:
            checks.append(
                CheckResult(
                    "orphan_feature_symbology",
                    "Feature symbology usage",
                    settings.severity("orphan_feature_symbology", Severity.WARN),
                    f"{len(orphans)} feature symbology(ies) are not tied to any feature definition",
                    "Defined symbology that nothing can ever place.",
                    orphans[:25],
                    LAYER,
                )
            )
        else:
            checks.append(
                CheckResult(
                    "orphan_feature_symbology",
                    "Feature symbology usage",
                    Severity.PASS,
                    "Every feature symbology is referenced by a feature definition",
                    layer=LAYER,
                )
            )

    # 7 - duplicate level names ------------------------------------------------ #
    if not has_levels:
        checks.append(_skip("duplicate_level_names", "Duplicate level names", "Levels required"))
    elif std.duplicate_levels:
        checks.append(
            CheckResult(
                "duplicate_level_names",
                "Duplicate level names",
                settings.severity("duplicate_level_names", Severity.WARN),
                f"{len(std.duplicate_levels)} duplicate level name(s)",
                "Later definitions overwrote earlier ones during parsing.",
                sorted(set(std.duplicate_levels))[:25],
                LAYER,
            )
        )
    else:
        checks.append(
            CheckResult(
                "duplicate_level_names",
                "Duplicate level names",
                Severity.PASS,
                "No duplicate level names",
                layer=LAYER,
            )
        )

    # 8 - duplicate element template paths -------------------------------------- #
    if not has_et:
        checks.append(
            _skip("duplicate_element_template_paths", "Duplicate template paths", "ET required")
        )
    elif std.duplicate_et_paths:
        checks.append(
            CheckResult(
                "duplicate_element_template_paths",
                "Duplicate template paths",
                settings.severity("duplicate_element_template_paths", Severity.WARN),
                f"{len(std.duplicate_et_paths)} duplicate element template path(s)",
                "Ambiguous which definition wins.",
                sorted(set(std.duplicate_et_paths))[:25],
                LAYER,
            )
        )
    else:
        checks.append(
            CheckResult(
                "duplicate_element_template_paths",
                "Duplicate template paths",
                Severity.PASS,
                "No duplicate element template paths",
                layer=LAYER,
            )
        )

    # 9 - empty level references -------------------------------------------------- #
    if not has_et:
        checks.append(_skip("empty_level_reference", "Empty level references", "ET required"))
    else:
        blank = sorted(p for p, t in std.et.items() if not t.level.strip())
        if blank:
            checks.append(
                CheckResult(
                    "empty_level_reference",
                    "Empty level references",
                    settings.severity("empty_level_reference", Severity.WARN),
                    f"{len(blank)} element template(s) assign no level",
                    "Elements placed with these templates stay on the active level.",
                    blank[:25],
                    LAYER,
                )
            )
        else:
            checks.append(
                CheckResult(
                    "empty_level_reference",
                    "Empty level references",
                    Severity.PASS,
                    "Every element template assigns a level",
                    layer=LAYER,
                )
            )

    # 10 - plot flag sanity --------------------------------------------------------- #
    if not (has_et and has_levels):
        checks.append(_skip("plot_flag_sanity", "Plot flag sanity", "ET and Levels required"))
    else:
        used = _used_levels(std, settings)
        no_plot = sorted(
            name
            for name in used
            if name in std.levels and std.levels[name].plot.strip() in ("0", "False", "false")
        )
        if no_plot:
            checks.append(
                CheckResult(
                    "plot_flag_sanity",
                    "Plot flag sanity",
                    settings.severity("plot_flag_sanity", Severity.WARN),
                    f"{len(no_plot)} level(s) used by templates are set not to plot",
                    "Correct for construction levels; a defect for anything meant to appear on a sheet.",
                    no_plot[:25],
                    LAYER,
                )
            )
        else:
            checks.append(
                CheckResult(
                    "plot_flag_sanity",
                    "Plot flag sanity",
                    Severity.PASS,
                    "No used level is flagged non-plotting",
                    layer=LAYER,
                )
            )

    for check in checks:
        if check.severity not in (Severity.PASS, Severity.NOT_EVALUATED):
            check.guidance = GUIDANCE.get(check.name, "")

    return checks

