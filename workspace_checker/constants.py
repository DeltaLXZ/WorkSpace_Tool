"""Reference constants: XML namespaces, Bentley enumeration maps, and the report palette."""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# XML namespaces used by the Bentley standards exports
# --------------------------------------------------------------------------- #
CM_NS = "{Bentley-ContentManagement}"
UP_NS = "{Ustn_ElementParams.01.00}"

# --------------------------------------------------------------------------- #
# Symbology aspect types
# --------------------------------------------------------------------------- #
SUFFIX_MAP = {
    "Pnt": "Point",
    "Lin": "Linear",
    "Sol": "Solid",
    "Srf": "Surface",
    "Prf": "Profile",
}

STYPES = ("Point", "Linear", "Solid", "Surface", "Profile")

# --------------------------------------------------------------------------- #
# ElementTemplateRef relationship -> short label
# --------------------------------------------------------------------------- #
REL_MAP = {
    "GeometricGeometryAspect__DefaultElementTemplate": "Default",
    "GeometricGeometryAspect__PlanElementTemplate": "Plan",
    "GeometricGeometryAspect__ThreeDElementTemplate": "3D",
    "GeometricGeometryAspect__CrossSectionElementTemplate": "CrossSection",
    "LinearGeometryAspect__ProfileIntersectionElementTemplate": "ProfileIntersection",
    "SolidGeometryAspect__TopElementTemplateHolder": "Top",
    "SolidGeometryAspect__BottomElementTemplateHolder": "Bottom",
}

# --------------------------------------------------------------------------- #
# Input roles
# --------------------------------------------------------------------------- #
ROLE_FD = "FD"
ROLE_FS = "FS"
ROLE_ET = "ET"
ROLE_LEVEL = "LEVEL"
ALL_ROLES = (ROLE_FD, ROLE_FS, ROLE_ET, ROLE_LEVEL)

# Filename patterns used for role/tag detection (see detect.py).
ROLE_PATTERNS = {
    ROLE_FD: r"(?i)^(?P<tag>.+?)[_\-\s]*fd\.xml$",
    ROLE_FS: r"(?i)^(?P<tag>.+?)[_\-\s]*fs\.xml$",
    ROLE_ET: r"(?i)^(?P<tag>.+?)[_\-\s]*et\.xml$",
    ROLE_LEVEL: r"(?i)^(?P<tag>.+?)[_\-\s]*levels?\.csv$",
}

# --------------------------------------------------------------------------- #
# Workspace / configuration
# --------------------------------------------------------------------------- #
DGNLIB_EXT = ".dgnlib"
CELL_EXTS = (".cel",)
SEED_EXTS = (".dgn",)
CFG_EXTS = (".cfg",)
PEN_TABLE_EXTS = (".tbl",)
PLOT_CFG_EXTS = (".pltcfg",)

# MicroStation configuration precedence, lowest -> highest.
DEFAULT_PRECEDENCE_LEVELS = [
    "System",
    "Application",
    "Organization",
    "WorkSpace",
    "WorkSet",
    "Role",
    "User",
    "CommandLine",
]

# Folder-name heuristics for classifying a workspace tree.
WORKSPACE_FOLDER_HINTS = {
    "organization": ("organization", "organization-civil", "organizationcivil"),
    "workspace": ("workspaces", "workspace"),
    "workset": ("worksets", "workset"),
    "standards": ("standards", "dgnlib", "dgnlibs"),
    "cell": ("cell", "cells"),
    "seed": ("seed", "seeds"),
    "data": ("data",),
    "sheets": ("sheets", "sheet"),
    "macros": ("macros", "vba"),
    "symbology": ("symb", "symbology", "linestyle", "linestyles"),
}

# Folders that are never standards content - inventory only, never parsed.
BULK_FOLDER_HINTS = ("dgn", "design", "work", "wip", "out", "output", "backup", "archive", "temp")

# --------------------------------------------------------------------------- #
# Report palette
# --------------------------------------------------------------------------- #
FONT_NAME = "Calibri"
BRAND_NAVY = "1F3864"
BRAND_HDR = "2E5496"
BAND_LIGHT = "D9E1F2"
ZEBRA_FILL = "F2F5FB"

GROUP_TINTS = {
    "FD": "2E5496",
    "FS": "2F6C4F",
    "ET": "7A4E2E",
    "LV": "5B3A7A",
    "CFG": "1F6F8B",
    "INV": "6B4C7A",
    "MISC": "44546A",
}

SEV_COLORS = {
    "PASS": ("E2EFDA", "375623"),
    "INFO": ("E7E6E6", "3B3838"),
    "WARN": ("FFF2CC", "833C00"),
    "FAIL": ("FBE4E4", "C0392B"),
    "NOT_EVALUATED": ("F2F2F2", "808080"),
}

MISSING_FILL = "FCE4D6"
MISSING_FONT = "9C4200"

CHANGE_COLORS = {
    "Added": ("E2EFDA", "375623"),
    "Removed": ("FBE4E4", "C0392B"),
    "Changed": ("FFF2CC", "833C00"),
}

MAX_COL_WIDTH = 52
MIN_COL_WIDTH = 10

# --------------------------------------------------------------------------- #
# Verdicts
# --------------------------------------------------------------------------- #
VERDICT_READY = "USABLE - PRODUCTION READY"
VERDICT_WARN = "USABLE - WITH WARNINGS"
VERDICT_FAIL = "NOT PRODUCTION-READY"

VERDICT_COLORS = {
    VERDICT_READY: SEV_COLORS["PASS"],
    VERDICT_WARN: SEV_COLORS["WARN"],
    VERDICT_FAIL: SEV_COLORS["FAIL"],
}

# --------------------------------------------------------------------------- #
# Exit codes
# --------------------------------------------------------------------------- #
EXIT_OK = 0
EXIT_WARN = 1
EXIT_FAIL = 2
EXIT_USAGE = 3
EXIT_INTERNAL = 4
