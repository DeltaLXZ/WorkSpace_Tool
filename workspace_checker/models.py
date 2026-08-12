"""Data model for parsed standards exports, workspace structure and check results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .constants import (
    ALL_ROLES,
    VERDICT_FAIL,
    VERDICT_READY,
    VERDICT_WARN,
)


class Severity(str, Enum):
    """Check outcome. Ordering is by ``rank``, not declaration order."""

    NOT_EVALUATED = "NOT_EVALUATED"
    PASS = "PASS"
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @classmethod
    def coerce(cls, value: "str | Severity | None", default: "Severity") -> "Severity":
        if isinstance(value, cls):
            return value
        if value is None:
            return default
        try:
            return cls(str(value).strip().upper())
        except ValueError:
            return default


_SEVERITY_RANK = {
    Severity.NOT_EVALUATED: 0,
    Severity.PASS: 1,
    Severity.INFO: 2,
    Severity.WARN: 3,
    Severity.FAIL: 4,
}

# If none of these are defined, only part of the configuration chain was supplied.
ROOT_VARS = (
    "_USTN_WORKSPACEROOT",
    "_USTN_WORKSETROOT",
    "_USTN_ORGANIZATION",
    "_USTN_WORKSPACESROOT",
)


@dataclass
class Finding:
    """One concrete item behind a check result, so it can be filtered and acted on."""

    check: str
    severity: "Severity"
    item: str
    detail: str = ""
    source_file: str = ""
    line: int = 0

    @property
    def origin(self) -> str:
        if not self.source_file:
            return ""
        return f"{Path(self.source_file).name}:{self.line}" if self.line else Path(self.source_file).name

    @property
    def summary(self) -> str:
        parts = [self.item]
        if self.detail:
            parts.append(f"-> {self.detail}")
        if self.origin:
            parts.append(f"({self.origin})")
        return "  ".join(parts)


@dataclass
class CheckResult:
    """One health-check outcome."""

    name: str
    title: str
    severity: Severity
    result: str
    detail: str = ""
    evidence: list[str] = field(default_factory=list)
    layer: str = "standards"
    guidance: str = ""
    findings: list[Finding] = field(default_factory=list)

    def __post_init__(self):
        if self.findings and not self.evidence:
            self.evidence = [f.summary for f in self.findings[:25]]

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "severity": self.severity.value,
            "result": self.result,
            "detail": self.detail,
            "guidance": self.guidance,
            "evidence": self.evidence[:50],
            "layer": self.layer,
            "findings": [
                {
                    "item": f.item,
                    "detail": f.detail,
                    "source": f.origin,
                }
                for f in self.findings[:200]
            ],
        }


def roll_up(checks: list[CheckResult], warnings_block: bool = False) -> str:
    """Reduce a list of check results to a single workspace verdict."""
    worst = max((c.severity.rank for c in checks), default=Severity.PASS.rank)
    if worst >= Severity.FAIL.rank:
        return VERDICT_FAIL
    if worst >= Severity.WARN.rank:
        return VERDICT_FAIL if warnings_block else VERDICT_WARN
    return VERDICT_READY


def verdict_code(verdict: str) -> int:
    return {VERDICT_READY: 0, VERDICT_WARN: 1, VERDICT_FAIL: 2}.get(verdict, 4)


# --------------------------------------------------------------------------- #
# Standards content
# --------------------------------------------------------------------------- #
@dataclass
class FDRef:
    stype: str
    fs_featurepath: str
    fs_name: str
    raw: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.stype, self.fs_featurepath, self.fs_name)


@dataclass
class FeatureDefinition:
    name: str
    provider: str = ""
    featurepath: str = ""
    item_type: str = ""
    description: str = ""
    refs: list[FDRef] = field(default_factory=list)
    source_file: str = ""

    @property
    def fd_path(self) -> str:
        parts = [p for p in (self.provider, self.featurepath, self.name) if p]
        return "/".join(parts)


@dataclass
class ETRef:
    relationship: str
    et_path: str


@dataclass
class FeatureSymbology:
    stype: str
    featurepath: str
    name: str
    refs: list[ETRef] = field(default_factory=list)
    source_file: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.stype, self.featurepath, self.name)


@dataclass
class ElementTemplate:
    et_path: str
    name: str
    level: str = ""
    color: str = ""
    linestyle: str = ""
    weight: str = ""
    material: str = ""
    textstyle: str = ""
    element_class: str = ""
    transparency: str = ""
    source_file: str = ""


@dataclass
class Level:
    name: str
    bylevel_color: str = ""
    bylevel_weight: str = ""
    bylevel_style: str = ""
    plot: str = ""
    global_display: str = ""
    global_freeze: str = ""
    number: str = ""
    description: str = ""
    source_file: str = ""


@dataclass
class ResolvedRow:
    """One flattened FD aspect -> element template -> level row."""

    fd_name: str
    fd_item_type: str
    fd_path: str
    stype: str
    fs_featurepath: str
    fs_name: str
    et_relationship: str = ""
    et_path: str = ""
    et_name: str = ""
    level: str = ""
    bylevel_color: str = ""
    bylevel_weight: str = ""
    bylevel_style: str = ""
    et_color: str = ""
    et_weight: str = ""
    et_linestyle: str = ""
    material: str = ""
    textstyle: str = ""
    status: str = "OK"


@dataclass
class IntegrityIssue:
    fd_name: str
    stype: str
    fs_featurepath: str
    fs_name: str
    issue: str
    detail: str = ""


@dataclass
class StandardSet:
    """Everything parsed for one standards tag."""

    tag: str
    fds: list[FeatureDefinition] = field(default_factory=list)
    fs: dict[tuple[str, str, str], FeatureSymbology] = field(default_factory=dict)
    et: dict[str, ElementTemplate] = field(default_factory=dict)
    levels: dict[str, Level] = field(default_factory=dict)
    inputs_present: set[str] = field(default_factory=set)
    input_files: dict[str, str] = field(default_factory=dict)
    parse_warnings: list[str] = field(default_factory=list)
    duplicate_levels: list[str] = field(default_factory=list)
    duplicate_et_paths: list[str] = field(default_factory=list)

    def has(self, *roles: str) -> bool:
        return all(r in self.inputs_present for r in roles)

    @property
    def missing_inputs(self) -> list[str]:
        return [r for r in ALL_ROLES if r not in self.inputs_present]


# --------------------------------------------------------------------------- #
# Workspace / configuration
# --------------------------------------------------------------------------- #
@dataclass
class Definition:
    """A single assignment of a configuration variable, with provenance."""

    variable: str
    operator: str
    raw_value: str
    value: str
    level: str
    source_file: str
    line: int
    applied: bool = True
    note: str = ""
    unresolved: list[str] = field(default_factory=list)

    @property
    def origin(self) -> str:
        return f"{Path(self.source_file).name}:{self.line}"


@dataclass
class ConfigVar:
    name: str
    value: str = ""
    level: str = ""
    locked: bool = False
    history: list[Definition] = field(default_factory=list)

    @property
    def members(self) -> list[str]:
        return [m for m in self.value.split(";") if m.strip()]

    @property
    def unresolved(self) -> list[str]:
        """Variables referenced by the winning definitions that were never defined."""
        names: set[str] = set()
        for definition in self.history:
            if definition.applied:
                names.update(definition.unresolved)
        return sorted(names)


@dataclass
class PathMember:
    variable: str
    member: str
    resolved: str
    exists: bool
    role: str = ""
    source_file: str = ""
    line: int = 0
    note: str = ""
    unresolved: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.unresolved:
            return "UNRESOLVED"
        return "OK" if self.exists else "MISSING"


@dataclass
class InventoryItem:
    path: str
    relpath: str
    kind: str
    size: int = 0
    modified: str = ""
    sha1: str = ""
    referenced_by: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def name(self) -> str:
        return Path(self.path).name


@dataclass
class DgnLibInfo:
    path: str
    relpath: str
    exists: bool = True
    size: int = 0
    sha1: str = ""
    product_version: str = ""
    roles: list[str] = field(default_factory=list)
    precedence_level: str = ""
    search_index: int = 0
    on_config: bool = False
    shadowed_by: str = ""
    shadows: str = ""
    probe_levels: int = 0
    probe_templates: int = 0
    note: str = ""


@dataclass
class WorkspaceTree:
    root: str
    organizations: list[str] = field(default_factory=list)
    workspaces: list[str] = field(default_factory=list)
    worksets: list[str] = field(default_factory=list)
    cfg_files: list[str] = field(default_factory=list)
    dgnlibs: list[str] = field(default_factory=list)
    cell_libs: list[str] = field(default_factory=list)
    seeds: list[str] = field(default_factory=list)
    export_files: dict[str, list[str]] = field(default_factory=dict)
    inventory: list[InventoryItem] = field(default_factory=list)
    skipped_folders: list[str] = field(default_factory=list)
    files_scanned: int = 0
    bytes_scanned: int = 0
    crawl_warnings: list[str] = field(default_factory=list)

    @property
    def tag_hint(self) -> str:
        return Path(self.root).name


@dataclass
class ConfigModel:
    """Result of resolving a configuration tree."""

    variables: dict[str, ConfigVar] = field(default_factory=dict)
    path_members: list[PathMember] = field(default_factory=list)
    include_graph: dict[str, list[str]] = field(default_factory=dict)
    include_cycles: list[list[str]] = field(default_factory=list)
    missing_includes: list[str] = field(default_factory=list)
    max_include_depth: int = 0
    parse_warnings: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)

    def get(self, name: str) -> str:
        var = self.variables.get(name.upper())
        return var.value if var else ""

    @property
    def roots_defined(self) -> list[str]:
        return [name for name in ROOT_VARS if self.variables.get(name, None) and self.get(name)]

    @property
    def is_partial(self) -> bool:
        """True when no workspace root is defined, i.e. only part of the config was supplied."""
        return not self.roots_defined


@dataclass
class AuditResult:
    """The complete output of one audit run."""

    tag: str
    generated: str
    root: str = ""
    standard: StandardSet | None = None
    rows: list[ResolvedRow] = field(default_factory=list)
    issues: list[IntegrityIssue] = field(default_factory=list)
    standards_checks: list[CheckResult] = field(default_factory=list)
    config_checks: list[CheckResult] = field(default_factory=list)
    tree: WorkspaceTree | None = None
    config: ConfigModel | None = None
    dgnlibs: list[DgnLibInfo] = field(default_factory=list)
    extraction_log: list[str] = field(default_factory=list)
    warnings_block: bool = False

    @property
    def all_checks(self) -> list[CheckResult]:
        return self.standards_checks + self.config_checks

    @property
    def standards_verdict(self) -> str:
        return roll_up(self.standards_checks, self.warnings_block)

    @property
    def config_verdict(self) -> str:
        return roll_up(self.config_checks, self.warnings_block)

    @property
    def verdict(self) -> str:
        return roll_up(self.all_checks, self.warnings_block)

    def counts(self) -> dict[str, int]:
        std = self.standard
        return {
            "fd": len(std.fds) if std else 0,
            "fd_et_level_rows": len(self.rows),
            "element_templates": len(std.et) if std else 0,
            "levels": len(std.levels) if std else 0,
            "feature_symbologies": len(std.fs) if std else 0,
            "dgnlibs": len(self.dgnlibs),
            "cfg_files": len(self.tree.cfg_files) if self.tree else 0,
            "inventory_items": len(self.tree.inventory) if self.tree else 0,
        }

    def issue_counts(self) -> dict[str, int]:
        out = {"fail": 0, "warn": 0, "info": 0, "pass": 0, "not_evaluated": 0}
        for c in self.all_checks:
            out[c.severity.value.lower()] += 1
        return out
