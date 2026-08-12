"""Machine-readable audit summary for CI and trend tracking."""

from __future__ import annotations

import json
from pathlib import Path

from .models import AuditResult, verdict_code
from .version import __product__, __version__


def build_summary(result: AuditResult) -> dict:
    std = result.standard
    summary = {
        "tool": {"name": __product__, "version": __version__},
        "tag": result.tag,
        "generated": result.generated,
        "root": result.root,
        "inputs_present": sorted(std.inputs_present) if std else [],
        "counts": result.counts(),
        "verdict": result.verdict,
        "verdict_code": verdict_code(result.verdict),
        "standards_verdict": result.standards_verdict,
        "config_verdict": result.config_verdict,
        "counts_issues": result.issue_counts(),
        "checks": [c.as_dict() for c in result.all_checks],
        "parse_warnings": (std.parse_warnings if std else []),
    }

    if result.config is not None:
        summary["config"] = {
            "entry_points": result.config.entry_points,
            "variables": len(result.config.variables),
            "dead_paths": [
                {"variable": m.variable, "member": m.member,
                 "source": f"{Path(m.source_file).name}:{m.line}"}
                for m in result.config.path_members
                if not m.exists
            ][:200],
            "include_cycles": [[Path(c).name for c in cycle] for cycle in result.config.include_cycles],
            "missing_includes": result.config.missing_includes,
            "shadowing": [
                {"library": d.path, "shadowed_by": d.shadowed_by}
                for d in result.dgnlibs
                if d.shadowed_by
            ],
        }

    if result.tree is not None:
        summary["workspace"] = {
            "files_scanned": result.tree.files_scanned,
            "bytes_scanned": result.tree.bytes_scanned,
            "dgnlibs": len(result.tree.dgnlibs),
            "cell_libraries": len(result.tree.cell_libs),
            "seeds": len(result.tree.seeds),
            "manifest": [
                {"path": i.relpath, "sha1": i.sha1, "size": i.size}
                for i in result.tree.inventory
                if i.sha1
            ],
        }

    if result.extraction_log:
        summary["extraction"] = result.extraction_log
    return summary


def write_summary(result: AuditResult, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build_summary(result), indent=2), encoding="utf-8")
    return out
