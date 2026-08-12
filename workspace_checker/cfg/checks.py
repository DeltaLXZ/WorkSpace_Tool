"""Configuration-layer health checks: paths, roles, precedence and portability."""

from __future__ import annotations

import re
from pathlib import Path

from ..config import Settings
from ..models import (
    CheckResult,
    ConfigModel,
    DgnLibInfo,
    Finding,
    Severity,
    WorkspaceTree,
)
from .roles import EXPORT_ENABLE_VAR, RoleStatus, export_enabled

LAYER = "config"

_DRIVE_RE = re.compile(r"(?<![\w$(])([A-Za-z]):[\\/]")
_MACHINE_LOCAL_RE = re.compile(
    r"\\(users|documents and settings|appdata|desktop|onedrive)\\", re.IGNORECASE
)

TITLES = {
    "dead_paths": "Configuration path validity",
    "unresolved_variables": "Unresolved configuration variables",
    "mapped_drive_paths": "Drive-letter portability",
    "machine_local_paths": "Machine-local paths",
    "role_coverage": "DGNLIB role coverage",
    "export_enablement": "Standards export enablement",
    "duplicate_dgnlib_basenames": "Ambiguous DGNLIB names",
    "workset_shadowing": "WorkSet shadowing",
    "conflicting_definitions": "Conflicting variable definitions",
    "include_graph": "Include graph integrity",
    "unreferenced_dgnlib": "Unreferenced DGNLIBs",
}

# What an auditor should actually do when a check is not clean.
GUIDANCE = {
    "dead_paths": (
        "Open the cited file at the cited line. Either the folder was moved or renamed "
        "(repoint the variable), or it was never delivered (add it). If the path belongs "
        "to a product you do not deploy, delete the line rather than leaving it dangling."
    ),
    "unresolved_variables": (
        "These variables are referenced but never defined in what you supplied. They are "
        "normally set by a higher configuration level. Re-run with --cfg pointing at the "
        "Organization or WorkSpace .cfg, or seed them with --env NAME=VALUE. Until then "
        "any path built from them cannot be judged."
    ),
    "mapped_drive_paths": (
        "Replace the drive letter with $(_USTN_WORKSPACEROOT), $(_USTN_WORKSETROOT) or a "
        "UNC path. A mapped drive is per-machine, so the delivery breaks the moment "
        "someone has a different letter or no mapping at all."
    ),
    "machine_local_paths": (
        "Paths under a user profile resolve only on the machine that wrote them. Move the "
        "content into the workspace and reference it relative to a workspace root."
    ),
    "role_coverage": (
        "Every required role needs at least one DGNLIB that exists and is on the search "
        "list. Check MS_DGNLIBLIST first: confirm the variable is defined, the folder "
        "exists, and the wildcard actually matches files."
    ),
    "export_enablement": (
        "Set _CIVIL_STANDARDS_IMPORTEXPORT = 1 at Organization or WorkSpace level so "
        "anyone using this workspace can export standards, not just the machine that "
        "happens to have it set locally."
    ),
    "duplicate_dgnlib_basenames": (
        "The search list is read left to right, so the first copy wins and later ones are "
        "silently ignored. Either delete the redundant copies or rename them so it is "
        "obvious which is authoritative."
    ),
    "workset_shadowing": (
        "A project-level library is overriding an organization standard. That is fine if "
        "deliberate, and a defect if accidental. Confirm the WorkSet copy is intended, "
        "otherwise remove it so projects inherit the library."
    ),
    "conflicting_definitions": (
        "The same variable is set to different values at different levels. Only the winner "
        "applies. Delete the losing definitions or move the intended value to the level "
        "that should own it."
    ),
    "include_graph": (
        "A missing %include stops configuration processing at that point, so everything "
        "after it silently never applies. A cycle does the same. Fix the include chain "
        "before trusting any other configuration finding."
    ),
    "unreferenced_dgnlib": (
        "These libraries are shipped but unreachable at runtime. Either add them to "
        "MS_DGNLIBLIST or remove them from the delivery so nobody assumes they are active."
    ),
}


def _skip(name: str, reason: str) -> CheckResult:
    return CheckResult(
        name=name,
        title=TITLES.get(name, name),
        severity=Severity.NOT_EVALUATED,
        result="Not evaluated",
        detail=f"input not provided: {reason}",
        layer=LAYER,
        guidance=GUIDANCE.get(name, ""),
    )


def _result(name, severity, result, detail="", findings=None) -> CheckResult:
    clean = severity in (Severity.PASS, Severity.NOT_EVALUATED)
    return CheckResult(
        name=name,
        title=TITLES.get(name, name),
        severity=severity,
        result=result,
        detail=detail,
        layer=LAYER,
        guidance="" if clean else GUIDANCE.get(name, ""),
        findings=list(findings or []),
    )


def run_config_checks(
    model: ConfigModel | None,
    roles: dict[str, RoleStatus] | None,
    dgnlibs: list[DgnLibInfo],
    tree: WorkspaceTree | None,
    settings: Settings,
) -> list[CheckResult]:
    """Evaluate every configuration check. Returns NOT_EVALUATED entries when unwired."""
    if model is None or not model.entry_points:
        return [_skip(name, "no .cfg entry point found") for name in TITLES]

    checks: list[CheckResult] = []
    partial = model.is_partial

    # A - path validity ------------------------------------------------------- #
    candidates = [m for m in model.path_members if not settings.ignored("paths", m.member)]
    unresolved = [m for m in candidates if m.unresolved]
    dead = [m for m in candidates if not m.exists and not m.unresolved]

    if dead:
        checks.append(
            _result(
                "dead_paths",
                settings.severity("dead_paths", Severity.FAIL),
                f"{len(dead)} configured path(s) do not exist",
                "The variable resolved fully, but nothing is there.",
                [
                    Finding("dead_paths", Severity.FAIL, m.variable, m.resolved,
                            m.source_file, m.line)
                    for m in dead
                ],
            )
        )
    else:
        checks.append(
            _result(
                "dead_paths",
                Severity.PASS,
                f"All {len(candidates) - len(unresolved)} fully resolved path member(s) exist",
            )
        )

    # A - unresolved variables -------------------------------------------------- #
    if unresolved:
        names = sorted({n for m in unresolved for n in m.unresolved})
        checks.append(
            _result(
                "unresolved_variables",
                settings.severity("unresolved_variables", Severity.WARN),
                f"{len(unresolved)} path(s) unresolved; {len(names)} variable(s) undefined: "
                + ", ".join(names[:6])
                + (" ..." if len(names) > 6 else ""),
                "A path built from an undefined variable is truncated, so it is reported "
                "here rather than counted as missing.",
                [
                    Finding("unresolved_variables", Severity.WARN, m.variable,
                            f"{m.resolved or '(empty)'}  [undefined: {', '.join(m.unresolved)}]",
                            m.source_file, m.line)
                    for m in unresolved
                ],
            )
        )
    else:
        checks.append(
            _result("unresolved_variables", Severity.PASS, "Every referenced variable resolved")
        )

    # A - portability ----------------------------------------------------------- #
    hardcoded: list[Finding] = []
    machine_local: list[Finding] = []
    for var in model.variables.values():
        if settings.ignored("variables", var.name):
            continue
        for definition in var.history:
            # Seeded variables come from the auditor's machine, not the delivery.
            if not definition.applied or definition.source_file == "<seed>":
                continue
            raw = definition.raw_value.strip()
            if _DRIVE_RE.search(raw):
                hardcoded.append(
                    Finding("mapped_drive_paths", Severity.WARN, var.name, raw,
                            definition.source_file, definition.line)
                )
            if _MACHINE_LOCAL_RE.search(raw):
                machine_local.append(
                    Finding("machine_local_paths", Severity.WARN, var.name, raw,
                            definition.source_file, definition.line)
                )

    if hardcoded:
        checks.append(
            _result(
                "mapped_drive_paths",
                settings.severity("mapped_drive_paths", Severity.WARN),
                f"{len(hardcoded)} definition(s) hardcode a drive letter",
                "The delivery will not move cleanly between machines.",
                hardcoded,
            )
        )
    else:
        checks.append(_result("mapped_drive_paths", Severity.PASS, "No drive-letter hardcoding"))

    if machine_local:
        checks.append(
            _result(
                "machine_local_paths",
                settings.severity("machine_local_paths", Severity.WARN),
                f"{len(machine_local)} definition(s) point into a user profile",
                "These resolve only on the machine that authored them.",
                machine_local,
            )
        )
    else:
        checks.append(
            _result("machine_local_paths", Severity.PASS,
                    "No user-profile paths in the configuration")
        )

    # B - role coverage ---------------------------------------------------------- #
    if not roles:
        checks.append(_skip("role_coverage", "no roles configured"))
    else:
        broken = [r for r in roles.values() if r.required and not r.satisfied and not r.incomplete]
        unproven = [r for r in roles.values() if r.required and r.incomplete]
        if partial:
            unproven, broken = unproven + broken, []
        fragile = [
            r for r in roles.values() if r.satisfied and r.satisfied_at in ("WorkSet", "User")
        ]

        if broken:
            checks.append(
                _result(
                    "role_coverage",
                    settings.severity("role_coverage", Severity.FAIL),
                    f"{len(broken)} required role(s) have no resolvable DGNLIB",
                    "The workspace cannot deliver these standards at all.",
                    [
                        Finding("role_coverage", Severity.FAIL, r.name,
                                f"none of {', '.join(r.variables)} resolve")
                        for r in broken
                    ],
                )
            )
        elif unproven:
            detail = (
                "Only part of the configuration chain was supplied: no workspace root "
                "(_USTN_WORKSPACEROOT / _USTN_WORKSETROOT) is defined, so the variables that "
                "wire these roles live outside the folder you pointed at. Coverage is "
                "unproven, not absent."
                if partial
                else "The variables exist but reference undefined roots, so coverage is "
                     "unproven rather than absent."
            )
            checks.append(
                _result(
                    "role_coverage",
                    Severity.WARN,
                    f"{len(unproven)} required role(s) could not be confirmed",
                    detail,
                    [
                        Finding("role_coverage", Severity.WARN, r.name,
                                r.unresolved_members[0] if r.unresolved_members
                                else f"no variable defined ({', '.join(r.variables)})")
                        for r in unproven
                    ],
                )
            )
        elif fragile:
            checks.append(
                _result(
                    "role_coverage",
                    Severity.WARN,
                    f"{len(fragile)} role(s) satisfied only at WorkSet/User level",
                    "Fragile: a new WorkSet would lose these standards.",
                    [
                        Finding("role_coverage", Severity.WARN, r.name,
                                f"satisfied at {r.satisfied_at}")
                        for r in fragile
                    ],
                )
            )
        else:
            checks.append(
                _result(
                    "role_coverage",
                    Severity.PASS,
                    f"All {len(roles)} role(s) resolve to at least one DGNLIB",
                )
            )

    # B - export enablement ------------------------------------------------------- #
    if not settings.get("config_verification", "require_export_enabled", default=True):
        checks.append(_skip("export_enablement", "not required"))
    else:
        enabled, level = export_enabled(model)
        if enabled and level in ("User", "Role"):
            checks.append(
                _result(
                    "export_enablement",
                    Severity.WARN,
                    f"{EXPORT_ENABLE_VAR} is set only at {level} level",
                    "Other users of this workspace will not be able to export standards.",
                )
            )
        elif enabled:
            checks.append(
                _result("export_enablement", Severity.PASS,
                        f"{EXPORT_ENABLE_VAR}=1 at {level or 'unknown'} level")
            )
        else:
            checks.append(
                _result(
                    "export_enablement",
                    settings.severity("export_enablement", Severity.WARN),
                    f"{EXPORT_ENABLE_VAR} is not enabled",
                    "Standards cannot be exported from this workspace until it is set to 1."
                    + (" It may be set by a configuration level you did not supply."
                       if partial else ""),
                )
            )

    # C - ambiguous / shadowed libraries -------------------------------------------- #
    duplicates = [d for d in dgnlibs if d.shadowed_by]
    if duplicates:
        checks.append(
            _result(
                "duplicate_dgnlib_basenames",
                settings.severity("duplicate_dgnlib_basenames", Severity.WARN),
                f"{len(duplicates)} DGNLIB name(s) exist in more than one location",
                "The search list is read left to right, so the first entry wins.",
                [
                    Finding("duplicate_dgnlib_basenames", Severity.WARN,
                            Path(d.path).name, f"{d.path}  loses to  {d.shadowed_by}")
                    for d in duplicates
                ],
            )
        )
    else:
        checks.append(
            _result("duplicate_dgnlib_basenames", Severity.PASS, "No ambiguous DGNLIB names")
        )

    workset_shadow = [d for d in dgnlibs if d.precedence_level in ("WorkSet", "User") and d.shadows]
    if workset_shadow:
        checks.append(
            _result(
                "workset_shadowing",
                settings.severity("workset_shadowing", Severity.WARN),
                f"{len(workset_shadow)} organization standard(s) overridden below WorkSpace level",
                "A project copy is winning over the library standard.",
                [
                    Finding("workset_shadowing", Severity.WARN, d.path,
                            f"overrides {d.shadows}")
                    for d in workset_shadow
                ],
            )
        )
    else:
        checks.append(
            _result("workset_shadowing", Severity.PASS,
                    "No organization standard is shadowed by a WorkSet copy")
        )

    # C - conflicting definitions ----------------------------------------------------- #
    conflicts: list[Finding] = []
    for var in model.variables.values():
        if settings.ignored("variables", var.name):
            continue
        applied_values = {d.value.strip() for d in var.history if d.applied and d.value.strip()}
        # A skipped ":" default is correct behaviour, not a conflict.
        losers = [
            d for d in var.history if not d.applied and d.value.strip() and d.operator != ":"
        ]
        if len(applied_values) > 1 or losers:
            lost = "; ".join(
                f"'{d.value}' at {d.level} {d.origin}" for d in losers[:3]
            )
            conflicts.append(
                Finding(
                    "conflicting_definitions",
                    Severity.WARN,
                    var.name,
                    f"winner '{var.value}' at {var.level}"
                    + (f"  |  lost: {lost}" if lost else ""),
                )
            )
    if conflicts:
        checks.append(
            _result(
                "conflicting_definitions",
                settings.severity("conflicting_definitions", Severity.WARN),
                f"{len(conflicts)} variable(s) defined more than once with different values",
                "Only the winner applies; the rest are dead configuration.",
                conflicts,
            )
        )
    else:
        checks.append(
            _result("conflicting_definitions", Severity.PASS,
                    "Every variable has a single effective definition")
        )

    # C - include graph ----------------------------------------------------------------- #
    problems: list[Finding] = []
    for cycle in model.include_cycles:
        problems.append(
            Finding("include_graph", Severity.FAIL, "include cycle",
                    " -> ".join(Path(c).name for c in cycle))
        )
    for missing in model.missing_includes:
        problems.append(
            Finding("include_graph", Severity.FAIL, "missing include", missing)
        )
    if problems:
        checks.append(
            _result(
                "include_graph",
                settings.severity("include_graph", Severity.FAIL),
                f"{len(problems)} problem(s) in the %include graph",
                "Processing stops at the failure point, so later configuration never applies.",
                problems,
            )
        )
    else:
        checks.append(
            _result("include_graph", Severity.PASS,
                    f"Include graph is acyclic (max depth {model.max_include_depth})")
        )

    # D - delivery hygiene ----------------------------------------------------------------- #
    if tree is None:
        checks.append(_skip("unreferenced_dgnlib", "no workspace folder given"))
    elif partial or (roles and any(r.incomplete for r in roles.values())):
        checks.append(
            _skip("unreferenced_dgnlib", "configuration incomplete, so wiring cannot be judged")
        )
    else:
        orphans = [
            d for d in dgnlibs
            if d.exists and not d.on_config and not settings.ignored("dgnlibs", d.relpath)
        ]
        missing = [d for d in dgnlibs if d.on_config and not d.exists]
        if missing:
            checks.append(
                _result(
                    "unreferenced_dgnlib",
                    Severity.FAIL,
                    f"{len(missing)} configured DGNLIB(s) are not in the delivery",
                    "The configuration names libraries that were not shipped.",
                    [Finding("unreferenced_dgnlib", Severity.FAIL, d.path, "not delivered")
                     for d in missing],
                )
            )
        elif orphans:
            checks.append(
                _result(
                    "unreferenced_dgnlib",
                    settings.severity("unreferenced_dgnlib", Severity.WARN),
                    f"{len(orphans)} DGNLIB(s) are delivered but never wired up",
                    "Present on disk, unreachable at runtime.",
                    [Finding("unreferenced_dgnlib", Severity.WARN, d.relpath, "not on any search list")
                     for d in orphans],
                )
            )
        else:
            checks.append(
                _result("unreferenced_dgnlib", Severity.PASS,
                        "Every delivered DGNLIB is referenced by the configuration")
            )

    return checks
