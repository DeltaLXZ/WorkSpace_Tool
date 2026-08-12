"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import load_settings
from .constants import EXIT_FAIL, EXIT_INTERNAL, EXIT_OK, EXIT_USAGE, EXIT_WARN
from .models import AuditResult, Severity, verdict_code
from .pipeline import run_audit
from .report_json import write_summary
from .report_xlsx import default_workbook_name, write_workbook
from .version import __product__, __version__

log = logging.getLogger("workspace_checker")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workspace-checker",
        description=(
            "Audit an OpenRoads/OpenBridge workspace: configuration wiring, standards "
            "content, and a full inventory. Drop in a workspace folder or exported "
            "FD/FS/ET/Level files."
        ),
    )
    parser.add_argument("inputs", nargs="*", help="Workspace folder(s) and/or export file(s).")
    parser.add_argument("--tag", help="Force the standard-set tag (skips auto-detection).")
    parser.add_argument("--out", default="output", help="Output directory (default: ./output).")
    parser.add_argument("--config", help="Path to healthcheck.config.json.")
    parser.add_argument("--cfg", nargs="*", default=None, help="Explicit .cfg entry point(s).")
    parser.add_argument(
        "--env", action="append", default=[], metavar="KEY=VALUE",
        help="Seed a configuration variable (repeatable).",
    )
    parser.add_argument("--env-file", help="Seed configuration variables from a KEY=VALUE file.")
    parser.add_argument(
        "--no-extract", action="store_true",
        help="Never launch a Bentley product; use only supplied export files.",
    )
    parser.add_argument(
        "--product",
        help="Which Bentley product to drive: a name fragment such as "
             "'OpenRoads Designer 2024.00', or a full path to the executable.",
    )
    parser.add_argument(
        "--list-products", action="store_true",
        help="List the Bentley products found on this machine and exit.",
    )
    parser.add_argument(
        "--read-dgnlib", nargs="+", metavar="DGNLIB",
        help="Read DGNLIBs directly and report what they declare, without launching "
             "any Bentley product. Accepts folders, which are searched recursively.",
    )
    parser.add_argument(
        "--names", action="store_true",
        help="With --read-dgnlib, list every name found rather than a sample.",
    )
    parser.add_argument("--json-only", action="store_true", help="Write only the JSON summary.")
    parser.add_argument("--gui", action="store_true", help="Open the drag-and-drop window.")
    parser.add_argument("--quiet", action="store_true", help="Errors only.")
    parser.add_argument("--verbose", action="store_true", help="Debug logging.")
    parser.add_argument("--version", action="version", version=f"{__product__} {__version__}")
    return parser


def _configure_logging(quiet: bool, verbose: bool) -> None:
    level = logging.ERROR if quiet else (logging.DEBUG if verbose else logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)-7s %(message)s")


def parse_env(pairs: list[str], env_file: str | None) -> dict[str, str]:
    env: dict[str, str] = {}
    if env_file:
        try:
            for line in Path(env_file).read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env[key.strip()] = value.strip()
        except OSError as exc:
            log.warning("Could not read --env-file %s: %s", env_file, exc)
    for pair in pairs:
        if "=" in pair:
            key, value = pair.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def emit_reports(result: AuditResult, out_dir: Path, settings, json_only: bool) -> list[Path]:
    written = [write_summary(result, out_dir / f"{result.tag}_health.json")]
    if not json_only:
        written.append(
            write_workbook(result, out_dir / default_workbook_name(result.tag), settings)
        )
    return written


def print_summary(result: AuditResult, written: list[Path]) -> None:
    """A short readable write-up so the console alone is useful."""
    counts = result.issue_counts()
    bar = "=" * 72
    print(f"\n{bar}\n{result.tag}: {result.verdict}\n{bar}")
    print(f"  content (standards) : {result.standards_verdict}")
    print(f"  wiring  (config)    : {result.config_verdict}")
    print(
        f"  {counts['fail']} failing, {counts['warn']} to review, "
        f"{counts['pass']} passing, {counts['not_evaluated']} not evaluated"
    )

    if result.config is not None and result.config.is_partial:
        print(
            "\n  NOTE: only part of the configuration chain was supplied, so wiring findings\n"
            "        are advisory. Re-run with --cfg <organization or workspace .cfg>, or\n"
            "        seed roots with --env _USTN_WORKSPACEROOT=... for a definitive answer."
        )

    for label, severity in (("MUST FIX", Severity.FAIL), ("REVIEW", Severity.WARN)):
        group = [c for c in result.all_checks if c.severity is severity]
        if not group:
            continue
        print(f"\n{label}")
        for check in group:
            print(f"  - {check.title}: {check.result}")
            for finding in check.findings[:3]:
                print(f"      {finding.summary}")
            if len(check.findings) > 3:
                print(f"      ... and {len(check.findings) - 3} more")
            if check.guidance:
                first = check.guidance.split(". ")[0].rstrip(".")
                print(f"      -> {first}.")

    skipped = [c for c in result.all_checks if c.severity is Severity.NOT_EVALUATED]
    if skipped:
        print("\nNOT EVALUATED")
        for check in skipped:
            print(f"  - {check.title}: {check.detail}")

    good = [c for c in result.all_checks if c.severity is Severity.PASS]
    if good:
        print(f"\nCONFIRMED GOOD ({len(good)})")
        for check in good:
            print(f"  - {check.title}: {check.result}")

    print("\nReports")
    for path in written:
        print(f"  {path}")
    print("  Open the Action_Plan sheet first; Config_Findings lists every wiring item.\n")


def _expand_dgnlibs(inputs: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            found.extend(sorted(path.rglob("*.dgnlib")))
        elif path.exists():
            found.append(path)
        else:
            log.warning("Not found: %s", raw)
    return found


def print_libraries(paths: list[Path], show_all_names: bool) -> int:
    """Report what each DGNLIB declares. Returns a process exit code."""
    from .dgn import read_library

    if not paths:
        print("No .dgnlib files found.", file=sys.stderr)
        return EXIT_USAGE

    failed = 0
    for path in paths:
        library = read_library(path)
        print(f"\n{path}")
        if not library.ok:
            print(f"  unreadable: {library.error}")
            failed += 1
            continue

        print(
            f"  {len(library.streams)} stream(s), "
            f"{len(library.schemas)} EC schema(s), {len(library.names)} name(s)"
        )
        if library.schemas:
            print("  Schemas")
            for schema in sorted(library.schemas, key=lambda s: s.name):
                print(f"    {schema.label}")
        if library.names:
            # Names with a space are the human-authored ones; the rest are EC
            # property identifiers, which are rarely what someone is looking for.
            human = [n for n in library.names if " " in n]
            shown = library.names if show_all_names else (human or library.names)
            print(f"  Names ({len(shown)} shown of {len(library.names)})")
            for name in shown if show_all_names else shown[:40]:
                print(f"    {name}")
            if not show_all_names and len(shown) > 40:
                print(f"    ... and {len(shown) - 40} more; pass --names for all")
        if library.unreadable:
            print("  Streams that could not be read")
            for note in library.unreadable:
                print(f"    {note}")

    return EXIT_FAIL if failed == len(paths) else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.quiet, args.verbose)

    if args.read_dgnlib:
        return print_libraries(_expand_dgnlibs(args.read_dgnlib), args.names)

    if args.list_products:
        from .extract.locator import describe_products

        print(describe_products())
        return EXIT_OK

    if args.gui or not args.inputs:
        from .gui import launch

        return launch(args.inputs)

    try:
        settings = load_settings(args.config)
        out_dir = Path(args.out)
        result = run_audit(
            inputs=args.inputs,
            settings=settings,
            tag=args.tag,
            cfg_files=args.cfg,
            env=parse_env(args.env, args.env_file),
            extract=not args.no_extract,
            product=args.product,
            out_dir=out_dir,
        )
        written = emit_reports(result, out_dir, settings, args.json_only)
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return EXIT_INTERNAL
    except Exception as exc:  # noqa: BLE001 - top level guard, reported to the user
        log.exception("Unexpected error")
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return EXIT_INTERNAL

    counts = result.issue_counts()
    if args.quiet:
        print(
            f"{result.tag}: {result.verdict} "
            f"({counts['fail']} fail, {counts['warn']} warn, "
            f"{counts['not_evaluated']} not evaluated)"
        )
        for path in written:
            print(f"  wrote {path}")
    else:
        print_summary(result, written)

    code = verdict_code(result.verdict)
    return {0: EXIT_OK, 1: EXIT_WARN, 2: EXIT_FAIL}.get(code, EXIT_USAGE)


if __name__ == "__main__":
    sys.exit(main())
