"""Configuration loading: defaults merged with an optional healthcheck.config.json."""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
from fnmatch import fnmatch
from pathlib import Path

from .constants import DEFAULT_PRECEDENCE_LEVELS
from .models import Severity

log = logging.getLogger(__name__)

CONFIG_FILENAME = "healthcheck.config.json"

DEFAULTS: dict = {
    "severity_overrides": {
        "reference_integrity": "FAIL",
        "fd_coverage": "FAIL",
        "level_completeness": "FAIL",
        "bylevel_symbology": "WARN",
        "orphan_element_template": "WARN",
        "orphan_feature_symbology": "WARN",
        "duplicate_level_names": "WARN",
        "duplicate_element_template_paths": "WARN",
        "empty_level_reference": "WARN",
        "plot_flag_sanity": "WARN",
        "dead_paths": "FAIL",
        "unresolved_variables": "WARN",
        "unreadable_paths": "FAIL",
        "mapped_drive_paths": "WARN",
        "machine_local_paths": "WARN",
        "role_coverage": "FAIL",
        "export_enablement": "WARN",
        "duplicate_dgnlib_basenames": "WARN",
        "workset_shadowing": "WARN",
        "conflicting_definitions": "WARN",
        "include_graph": "FAIL",
        "orphan_dgnlib": "WARN",
        "unreferenced_dgnlib": "WARN",
    },
    "ignore": {
        "orphan_element_template_paths": [],
        "levels": ["Default"],
        "variables": [],
        "paths": [],
        "dgnlibs": [],
    },
    "verdict_policy": {
        "warnings_block_production": False,
    },
    "output": {
        "font": "Calibri",
        "brand_primary": "1F3864",
        "brand_accent": "2E5496",
        "footer": "WSP Digital Delivery",
    },
    "crawl": {
        "max_files": 250000,
        "hash_files": True,
        "hash_size_limit_mb": 250,
        "skip_folders": [".git", ".svn", "node_modules", "__pycache__"],
    },
    "config_verification": {
        "precedence_levels": DEFAULT_PRECEDENCE_LEVELS,
        "roles": {
            "levels": {"vars": ["MS_DGNLIBLIST", "MS_LEVEL_LIBRARY"], "required": True},
            "features": {
                "vars": ["MS_DGNLIBLIST", "CIVIL_CONTENTMANAGEMENTDGNLIBLIST"],
                "required": True,
            },
            "templates": {"vars": ["MS_DGNLIBLIST", "MS_ELEMENTTEMPLATESEEDLIST"], "required": True},
            "styles": {"vars": ["MS_DGNLIBLIST"], "required": False},
            "materials": {"vars": ["MS_MATERIAL_LIBRARY", "MS_MATPALETTE"], "required": False},
            "cells": {"vars": ["MS_CELLLIST", "MS_CELL"], "required": False},
        },
        "path_list_vars": [
            "MS_DGNLIBLIST",
            "MS_CELLLIST",
            "MS_CELL",
            "MS_SEEDFILES",
            "MS_MATERIAL_LIBRARY",
            "MS_MATPALETTE",
            "MS_LEVEL_LIBRARY",
            "MS_ELEMENTTEMPLATESEEDLIST",
            "CIVIL_CONTENTMANAGEMENTDGNLIBLIST",
            "MS_SYMBRSRC",
            "MS_DEF",
            "MS_PENTABLE",
            "MS_PLTFILE_NAME",
            "MS_GUIDGNLIBLIST",
        ],
        "require_export_enabled": True,
        "max_include_depth": 8,
        "prepend_operator": "<",
        "deep_dgnlib": False,
        "seed_env": {},
    },
    "extraction": {
        "enabled": True,
        "timeout_seconds": 300,
        "product_preference": ["OpenBridge", "OpenRoads", "OpenRail", "MicroStation"],
        "product": "",
        "cache": True,
    },
}


class Settings:
    """Merged configuration with convenience accessors."""

    def __init__(self, data: dict, source: str = "<defaults>"):
        self.data = data
        self.source = source

    # -- generic access ---------------------------------------------------- #
    def get(self, *path, default=None):
        node = self.data
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    # -- typed helpers ----------------------------------------------------- #
    def severity(self, check_name: str, default: Severity) -> Severity:
        raw = self.get("severity_overrides", check_name)
        return Severity.coerce(raw, default)

    def ignored(self, bucket: str, value: str) -> bool:
        patterns = self.get("ignore", bucket, default=[]) or []
        return any(fnmatch(value, p) or fnmatch(value.lower(), p.lower()) for p in patterns)

    @property
    def warnings_block(self) -> bool:
        return bool(self.get("verdict_policy", "warnings_block_production", default=False))

    @property
    def precedence_levels(self) -> list[str]:
        return list(
            self.get("config_verification", "precedence_levels", default=DEFAULT_PRECEDENCE_LEVELS)
        )

    @property
    def roles(self) -> dict:
        return self.get("config_verification", "roles", default={}) or {}

    @property
    def path_list_vars(self) -> list[str]:
        return [v.upper() for v in self.get("config_verification", "path_list_vars", default=[])]

    @property
    def font(self) -> str:
        return self.get("output", "font", default="Calibri")

    @property
    def footer(self) -> str:
        return self.get("output", "footer", default="")


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for key, val in (overlay or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _candidate_paths(explicit: str | os.PathLike | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path.cwd() / CONFIG_FILENAME)
    exe_dir = Path(getattr(sys, "_MEIPASS", Path(sys.argv[0]).resolve().parent))
    candidates.append(exe_dir / CONFIG_FILENAME)
    candidates.append(Path(sys.argv[0]).resolve().parent / CONFIG_FILENAME)
    return candidates


def load_settings(explicit: str | os.PathLike | None = None) -> Settings:
    """Load settings, first match wins; falls back to built-in defaults."""
    for path in _candidate_paths(explicit):
        try:
            if path.is_file():
                with path.open("r", encoding="utf-8-sig") as fh:
                    overlay = json.load(fh)
                log.info("Loaded configuration from %s", path)
                return Settings(_deep_merge(DEFAULTS, overlay), str(path))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Ignoring unreadable config %s: %s", path, exc)
    return Settings(copy.deepcopy(DEFAULTS))
