"""DGNLIB role coverage: which configuration variables satisfy which standards role."""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..constants import DGNLIB_EXT
from ..models import ConfigModel, DgnLibInfo, WorkspaceTree

EXPORT_ENABLE_VAR = "_CIVIL_STANDARDS_IMPORTEXPORT"


@dataclass
class RoleStatus:
    name: str
    required: bool
    variables: list[str] = field(default_factory=list)
    libraries: list[str] = field(default_factory=list)
    missing_members: list[str] = field(default_factory=list)
    unresolved_members: list[str] = field(default_factory=list)
    satisfied_at: str = ""

    @property
    def satisfied(self) -> bool:
        return bool(self.libraries)

    @property
    def incomplete(self) -> bool:
        """Unsatisfied only because the configuration could not be fully resolved."""
        return not self.satisfied and bool(self.unresolved_members)


def expand_member(member: str) -> list[Path]:
    """Expand one path-list member into concrete dgnlib files.

    Members are files, folders, or wildcards such as ``...\\Dgnlib\\*.dgnlib``.
    """
    member = os.path.expandvars(member.strip().strip('"'))
    if not member:
        return []

    if any(ch in member for ch in "*?"):
        return [Path(p) for p in glob.glob(member) if Path(p).is_file()]

    path = Path(member)
    if path.is_dir():
        return sorted(path.glob(f"*{DGNLIB_EXT}"))
    if path.is_file():
        return [path]
    return []


def role_coverage(model: ConfigModel, settings: Settings) -> dict[str, RoleStatus]:
    """Determine which roles are satisfied by the resolved configuration."""
    statuses: dict[str, RoleStatus] = {}
    for role, spec in settings.roles.items():
        status = RoleStatus(
            name=role,
            required=bool(spec.get("required", False)),
            variables=list(spec.get("vars", [])),
        )
        for var_name in status.variables:
            var = model.variables.get(var_name.upper())
            if var is None or not var.value:
                continue
            for member in [m for m in var.value.split(";") if m.strip()]:
                found = expand_member(member)
                if found:
                    status.libraries.extend(str(p) for p in found)
                    if not status.satisfied_at:
                        status.satisfied_at = var.level
                elif var.unresolved:
                    status.unresolved_members.append(
                        f"{var_name}={member} (undefined: {', '.join(var.unresolved)})"
                    )
                else:
                    status.missing_members.append(f"{var_name}={member}")
        status.libraries = sorted(dict.fromkeys(status.libraries))
        statuses[role] = status
    return statuses


def export_enabled(model: ConfigModel) -> tuple[bool, str]:
    """Whether standards export is switched on, and at which precedence level."""
    var = model.variables.get(EXPORT_ENABLE_VAR)
    if var is None:
        return False, ""
    return var.value.strip() in ("1", "true", "True"), var.level


def collect_dgnlibs(
    model: ConfigModel,
    tree: WorkspaceTree | None,
    settings: Settings,
    roles: dict[str, RoleStatus] | None = None,
) -> list[DgnLibInfo]:
    """Merge configuration-referenced and disk-discovered dgnlibs into one inventory."""
    roles = roles or role_coverage(model, settings)
    root = Path(tree.root) if tree else None

    infos: dict[str, DgnLibInfo] = {}

    def _relpath(path: Path) -> str:
        if root:
            try:
                return str(path.relative_to(root))
            except ValueError:
                pass
        return str(path)

    def _entry(path: Path) -> DgnLibInfo:
        key = str(path).lower()
        if key not in infos:
            exists = path.is_file()
            infos[key] = DgnLibInfo(
                path=str(path),
                relpath=_relpath(path),
                exists=exists,
                size=path.stat().st_size if exists else 0,
                search_index=len(infos),
            )
        return infos[key]

    for role, status in roles.items():
        for lib in status.libraries:
            info = _entry(Path(lib))
            info.on_config = True
            info.precedence_level = status.satisfied_at
            if role not in info.roles:
                info.roles.append(role)

    for var_name in settings.path_list_vars:
        var = model.variables.get(var_name)
        if not var or not var.value:
            continue
        for member in [m for m in var.value.split(";") if m.strip()]:
            for path in expand_member(member):
                if path.suffix.lower() == DGNLIB_EXT:
                    info = _entry(path)
                    info.on_config = True
                    if not info.precedence_level:
                        info.precedence_level = var.level

    if tree:
        for lib in tree.dgnlibs:
            info = _entry(Path(lib))
            if not info.on_config:
                info.note = "present on disk but not referenced by the configuration"

    _mark_shadowing(list(infos.values()))
    return sorted(infos.values(), key=lambda i: i.path.lower())


def _mark_shadowing(infos: list[DgnLibInfo]) -> None:
    """Flag same-named libraries. ORD searches the list left to right, so the earliest
    member wins regardless of which configuration level contributed it."""
    by_name: dict[str, list[DgnLibInfo]] = {}
    for info in infos:
        by_name.setdefault(Path(info.path).name.lower(), []).append(info)

    for group in by_name.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda i: (not i.on_config, i.search_index))
        winner = ordered[0]
        for loser in ordered[1:]:
            loser.shadowed_by = winner.path
            winner.shadows = loser.path
