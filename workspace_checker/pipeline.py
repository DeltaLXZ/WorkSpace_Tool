"""Orchestration: crawl, resolve configuration, extract, resolve standards, check, report."""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .cfg.checks import run_config_checks
from .cfg.resolver import CfgResolver
from .cfg.roles import collect_dgnlibs, role_coverage
from .config import Settings
from .crawl import crawl, pick_cfg_entry_points
from .detect import group_by_tag, resolve_tag
from .extract.adapter import try_extract
from .health import run_standards_checks
from .models import AuditResult, WorkspaceTree
from .resolve import load_standard_set, resolve

log = logging.getLogger(__name__)

Progress = "callable | None"


def _note(progress, message: str) -> None:
    log.info(message)
    if progress:
        progress(message)


def _split_inputs(inputs: list[str]) -> tuple[list[Path], list[Path]]:
    folders, files = [], []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            folders.append(path)
        elif path.is_file():
            files.append(path)
        else:
            log.warning("Input does not exist, ignoring: %s", raw)
    return folders, files


def run_audit(
    inputs: list[str],
    settings: Settings,
    tag: str | None = None,
    cfg_files: list[str] | None = None,
    env: dict[str, str] | None = None,
    extract: bool = True,
    product: str | None = None,
    out_dir: str | os.PathLike | None = None,
    progress=None,
) -> AuditResult:
    """Run the full audit over folders and/or loose export files."""
    folders, loose_files = _split_inputs(inputs)
    root = str(folders[0]) if folders else ""

    # 1 - crawl -------------------------------------------------------------- #
    tree: WorkspaceTree | None = None
    if folders:
        _note(progress, f"Scanning workspace: {folders[0]}")
        tree = crawl(folders[0], settings)
        for extra in folders[1:]:
            other = crawl(extra, settings)
            tree.cfg_files += other.cfg_files
            tree.dgnlibs += other.dgnlibs
            tree.cell_libs += other.cell_libs
            tree.seeds += other.seeds
            tree.inventory += other.inventory
            tree.files_scanned += other.files_scanned
            for role, paths in other.export_files.items():
                tree.export_files.setdefault(role, []).extend(paths)
        _note(
            progress,
            f"Found {tree.files_scanned} files, {len(tree.dgnlibs)} DGNLIB, "
            f"{len(tree.cfg_files)} config files",
        )

    # 2 - configuration ------------------------------------------------------- #
    entry_points = list(cfg_files or [])
    if not entry_points and tree:
        entry_points = pick_cfg_entry_points(tree)

    model = None
    roles = None
    dgnlibs = []
    if entry_points:
        _note(progress, f"Resolving configuration from {len(entry_points)} entry point(s)")
        resolver = CfgResolver(settings, seed_env=env)
        model = resolver.process(entry_points)
        roles = role_coverage(model, settings)
        dgnlibs = collect_dgnlibs(model, tree, settings, roles)
    elif tree and tree.dgnlibs:
        dgnlibs = collect_dgnlibs_without_config(tree)

    # 3 - extraction ---------------------------------------------------------- #
    extraction_log: list[str] = []
    export_files: dict[str, list[Path]] = defaultdict(list)
    for role, paths in (tree.export_files if tree else {}).items():
        export_files[role].extend(Path(p) for p in paths)

    grouped_loose, loose_warnings = group_by_tag(loose_files, forced=tag)
    for role_map in grouped_loose.values():
        for role, paths in role_map.items():
            export_files[role].extend(paths)

    missing_roles = [r for r in ("FD", "FS", "ET", "LEVEL") if r not in export_files]
    libraries = [d.path for d in dgnlibs if d.exists] or (tree.dgnlibs if tree else [])
    if extract and missing_roles and libraries:
        _note(progress, f"Extracting standards from {len(libraries)} DGNLIB(s)")
        work = Path(out_dir or ".") / "_extracted"
        results, extraction_log = try_extract(libraries, work, settings, product)
        for result in results:
            for role, path in result.files.items():
                export_files[role].append(Path(path))
        for line in extraction_log:
            _note(progress, line)
    elif missing_roles and not libraries:
        extraction_log.append(
            "No DGNLIBs found to extract from; supply exported FD/FS/ET/Level files."
        )

    # 4 - standards ------------------------------------------------------------ #
    filename_tags = {role: "" for role in export_files}
    resolved_tag, tag_warnings = resolve_tag(
        {r: list(p) for r, p in export_files.items()},
        filename_tags,
        forced=tag,
        folder_hint=Path(root).name if root else "",
    )
    _note(progress, f"Standard set tag: {resolved_tag}")

    std = load_standard_set(resolved_tag, {r: list(p) for r, p in export_files.items()})
    std.parse_warnings.extend(loose_warnings + tag_warnings)
    rows, issues = resolve(std)
    _note(progress, f"Resolved {len(rows)} chain row(s), {len(issues)} integrity issue(s)")

    standards_checks = run_standards_checks(std, rows, issues, settings)
    config_checks = run_config_checks(model, roles, dgnlibs, tree, settings)

    result = AuditResult(
        tag=resolved_tag,
        generated=datetime.now().isoformat(timespec="seconds"),
        root=root,
        standard=std,
        rows=rows,
        issues=issues,
        standards_checks=standards_checks,
        config_checks=config_checks,
        tree=tree,
        config=model,
        dgnlibs=dgnlibs,
        extraction_log=extraction_log,
        warnings_block=settings.warnings_block,
    )
    _note(progress, f"Verdict: {result.verdict}")
    return result


def collect_dgnlibs_without_config(tree: WorkspaceTree):
    """Inventory DGNLIBs found on disk when there is no configuration to wire them."""
    from .models import DgnLibInfo

    root = Path(tree.root)
    out = []
    for lib in tree.dgnlibs:
        path = Path(lib)
        try:
            relpath = str(path.relative_to(root))
        except ValueError:
            relpath = str(path)
        out.append(
            DgnLibInfo(
                path=str(path),
                relpath=relpath,
                exists=path.is_file(),
                size=path.stat().st_size if path.is_file() else 0,
                note="no configuration provided; wiring not verified",
            )
        )
    return out
