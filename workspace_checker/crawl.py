"""Workspace tree crawl: classify folders, inventory files, locate standards artifacts."""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path

from .config import Settings
from .constants import (
    BULK_FOLDER_HINTS,
    CELL_EXTS,
    CFG_EXTS,
    DGNLIB_EXT,
    PEN_TABLE_EXTS,
    PLOT_CFG_EXTS,
    WORKSPACE_FOLDER_HINTS,
)
from .detect import classify
from .models import InventoryItem, WorkspaceTree

log = logging.getLogger(__name__)

_KIND_BY_EXT = {
    ".cfg": "config",
    ".dgnlib": "dgnlib",
    ".cel": "cell library",
    ".dgn": "design file",
    ".tbl": "pen table",
    ".pltcfg": "plot configuration",
    ".xml": "xml",
    ".csv": "csv",
    ".mvba": "vba macro",
    ".dll": "add-in",
    ".rsc": "resource",
    ".itl": "item type library",
    ".sha": "shared cell",
    ".pset": "print style",
    ".xin": "civil settings",
}

# Only these are worth hashing for the drift manifest.
_HASH_EXTS = {".cfg", ".dgnlib", ".cel", ".tbl", ".pltcfg", ".xml", ".csv", ".itl", ".rsc"}


def _sha1(path: Path, limit_bytes: int) -> str:
    try:
        if path.stat().st_size > limit_bytes:
            return ""
        digest = hashlib.sha1()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _is_bulk(rel_parts: tuple[str, ...]) -> bool:
    return any(part.lower() in BULK_FOLDER_HINTS for part in rel_parts)


def _folder_role(name: str) -> str:
    lowered = name.lower()
    for role, hints in WORKSPACE_FOLDER_HINTS.items():
        if lowered in hints:
            return role
    return ""


def crawl(root: str | os.PathLike, settings: Settings) -> WorkspaceTree:
    """Walk a workspace root, classifying standards artifacts and inventorying the rest."""
    root_path = Path(root).resolve()
    tree = WorkspaceTree(root=str(root_path))

    if not root_path.is_dir():
        tree.crawl_warnings.append(f"Not a directory: {root_path}")
        return tree

    skip = {s.lower() for s in settings.get("crawl", "skip_folders", default=[])}
    max_files = int(settings.get("crawl", "max_files", default=250000))
    do_hash = bool(settings.get("crawl", "hash_files", default=True))
    hash_limit = int(settings.get("crawl", "hash_size_limit_mb", default=250)) * 1024 * 1024

    for dirpath, dirnames, filenames in os.walk(root_path):
        current = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d.lower() not in skip]

        for name in dirnames:
            role = _folder_role(name)
            full = str(current / name)
            if role == "organization":
                tree.organizations.append(full)
            elif role == "workspace":
                tree.workspaces.extend(str(p) for p in (current / name).iterdir() if p.is_dir())
            elif role == "workset":
                tree.worksets.extend(str(p) for p in (current / name).iterdir() if p.is_dir())

        try:
            rel_parts = current.relative_to(root_path).parts
        except ValueError:
            rel_parts = ()
        bulk = _is_bulk(rel_parts)

        for filename in filenames:
            if tree.files_scanned >= max_files:
                tree.crawl_warnings.append(f"Stopped after {max_files} files")
                return tree

            path = current / filename
            try:
                stat = path.stat()
            except OSError:
                continue

            tree.files_scanned += 1
            tree.bytes_scanned += stat.st_size
            ext = path.suffix.lower()
            kind = _KIND_BY_EXT.get(ext, "other")

            if bulk:
                # Inventory only: never parsed, never hashed.
                tree.inventory.append(
                    InventoryItem(
                        path=str(path),
                        relpath=str(path.relative_to(root_path)),
                        kind=kind,
                        size=stat.st_size,
                        modified=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                        note="bulk folder - not analysed",
                    )
                )
                continue

            if ext in CFG_EXTS:
                tree.cfg_files.append(str(path))
            elif ext == DGNLIB_EXT:
                tree.dgnlibs.append(str(path))
            elif ext in CELL_EXTS:
                tree.cell_libs.append(str(path))
            elif ext == ".dgn" and "seed" in [p.lower() for p in rel_parts + (filename,)]:
                tree.seeds.append(str(path))
            elif ext in (".xml", ".csv"):
                role, _ = classify(path)
                if role:
                    tree.export_files.setdefault(role, []).append(str(path))
                    kind = f"{role} export"

            item = InventoryItem(
                path=str(path),
                relpath=str(path.relative_to(root_path)),
                kind=kind,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            )
            if do_hash and ext in _HASH_EXTS:
                item.sha1 = _sha1(path, hash_limit)
            tree.inventory.append(item)

    log.info(
        "Crawled %s: %d files, %d dgnlib, %d cfg, %d cell libs",
        root_path,
        tree.files_scanned,
        len(tree.dgnlibs),
        len(tree.cfg_files),
        len(tree.cell_libs),
    )
    return tree


def pick_cfg_entry_points(tree: WorkspaceTree) -> list[str]:
    """Choose the .cfg files worth processing as entry points.

    Everything reachable through %include is followed by the resolver, so only
    top-level files need to be listed here.
    """
    if not tree.cfg_files:
        return []

    included: set[str] = set()
    for path in tree.cfg_files:
        try:
            text = Path(path).read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("%include"):
                target = stripped[len("%include") :].strip().strip('"')
                included.add(Path(target).name.lower())

    entries = [p for p in tree.cfg_files if Path(p).name.lower() not in included]
    return entries or tree.cfg_files
