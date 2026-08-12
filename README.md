# Workspace Checker

Audits an OpenRoads / OpenBridge / OpenRail workspace and reports whether it is fit to
hand to a design team. Drop a workspace folder on it and it will:

- **Crawl the tree** and inventory every DGNLIB, cell library, seed, pen table, plot
  configuration and export file, with hashes for drift comparison.
- **Resolve the MicroStation configuration** the way the product would: assignment
  operators, macro expansion, `%include` graphs, conditional blocks and precedence
  levels, then report dead paths, shadowing, portability leaks and unmet DGNLIB roles.
- **Resolve the standards chain** Feature Definition to Feature Symbology to Element
  Template to Level, and flag every link that does not resolve.
- **Emit** a styled multi-sheet Excel review workbook and a machine-readable JSON
  summary, with an exit code suitable for CI gating.

## Output

Two files per run, written to `--out`:

- `<TAG>_Workspace_Health_Review.xlsx`
- `<TAG>_health.json`

Read the workbook in this order:

| Sheet | What it is for |
|---|---|
| **Action_Plan** | Start here. Plain language: what must be fixed, what to review, what could not be judged and why, and what is confirmed good. Every finding carries a **What to do** line. |
| **Config_Findings** | One row per individual wiring problem, with the variable, resolved value, source file and line, and the remedy. Filterable. |
| Config_Health | The wiring checks rolled up, one row each. |
| Config_Variables / Config_Paths / Config_DGNLIBs | The evidence behind the wiring checks. |
| FD_ET_Level / FD_Combined | The resolved symbology chain. |
| ElementTemplate_Lookup / Level_Lookup / FS_Lookup | Standards reference tables. |
| Integrity_Checks | Every unresolved link. Empty means clean. |
| Inventory | Everything found under the workspace root. |

The console prints the same summary, so a run is useful without opening Excel. Add
`--quiet` for one-line output in scripts.

### Partial deliveries

A folder handed over for review often does not include the Organization or WorkSpace
`.cfg` that defines `MS_DGNLIBLIST` and the `_USTN_*` roots. The checker detects this and
reports **Configuration scope: PARTIAL**. In that state wiring findings are advisory:
missing roles are reported as *unproven* rather than *absent*, and paths built from
undefined variables are listed under **Unresolved configuration variables** rather than
counted as dead. Supply the rest of the chain for a definitive answer:

```powershell
workspace-checker "D:\Delivery" --cfg "D:\Org\Standards.cfg"
workspace-checker "D:\Delivery" --env _USTN_WORKSPACEROOT="D:\Workspaces\"
```

## The window

Running with no arguments, or double-clicking the executable, opens a small window.

- **Add workspace folder** — scans a whole tree: configuration, DGNLIBs, cell libraries,
  seeds, and any exports already inside it. This is the normal way to use the tool.
- **Add exported FD / FS / ET / Level files** — for exports that live *outside* the
  workspace, for example a set you produced by hand into a scratch folder. Roles are
  detected from file contents rather than filenames, and the window reports what it
  recognised and what is still missing.
- **Bentley product** — which installed version to drive, if extraction is enabled.

You can also drag a workspace folder straight onto the executable in Explorer.

## Install and run

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m workspace_checker.cli --help
```

Common invocations:

```powershell
# Whole workspace
workspace-checker "D:\Workspaces\GDOT" --out .\output

# Just the four exports you already have
workspace-checker GDOT_FD.xml GDOT_FS.xml GDOT_ET.xml GDOT_Level.csv

# Workspace audit only, never launch a Bentley product
workspace-checker "D:\Workspaces\GDOT" --no-extract

# CI: fail the build on any FAIL
workspace-checker "D:\Workspaces\GDOT" --json-only ; if ($LASTEXITCODE -ge 2) { throw }
```

Running with no arguments opens the window.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Production ready |
| 1 | Usable with warnings |
| 2 | Not production-ready (one or more FAIL) |
| 3 | Bad usage or no valid inputs |
| 4 | Unexpected internal error |

## How the DGNLIB extraction works, and its limits

A `.dgnlib` is a binary DGN V8 container. There is no open-source reader for it, so the
checker asks an installed Bentley product to perform the four exports and then parses
those. If no product is installed the run does **not** fail: the workspace and
configuration audit still completes, and the standards checks report `NOT_EVALUATED`
with instructions to export manually.

### Choosing which product to drive

Most standards machines have several versions installed side by side.

```powershell
workspace-checker --list-products
workspace-checker "D:\Workspaces\GDOT" --product "OpenRoads Designer 2024.00"
workspace-checker "D:\Workspaces\GDOT" --product "C:\Program Files\Bentley\...\OpenRoadsDesigner.exe"
```

`--product` accepts a name fragment, a version, a family name, an install folder, or a
full path to the executable. With no flag the checker picks the newest version of the
first family in `extraction.product_preference`. The window offers the same list as a
dropdown. Discovery is depth-bounded and cached, so it costs a fraction of a second
rather than walking the whole Bentley install tree.

The key-in sequence used to drive the product lives in `healthcheck.config.json` under
`extraction.keyins`. **Verify it against your product version before relying on it** —
key-in names differ between releases, and the shipped default is a starting point, not
a guarantee. If a run produces no files the tool says so explicitly rather than
reporting an empty standard set as clean.

### Exporting manually

1. **File > Settings > Configuration > Configuration Variables > New** →
   `_CIVIL_STANDARDS_IMPORTEXPORT = 1`. Restart if the menu does not refresh.
2. **Explorer > OpenRoads Standards > Feature Definitions** → right-click > Export → `<TAG>_FD.xml`
3. **Feature Symbologies** → right-click > Export → `<TAG>_FS.xml`
4. **Element Templates dialog > File > Export** → `<TAG>_ET.xml`
5. **Level Manager > Levels > Export > .csv** → `<TAG>_Level.csv`

Open the DGNLIB itself as the active file so you capture the *library* standards rather
than an in-file copy, and export both sets from the same product version if you intend
to compare them.

## Graceful degradation

No input is mandatory. Every check returns `PASS`, `WARN`, `FAIL` or `NOT_EVALUATED`,
and `NOT_EVALUATED` never worsens the verdict.

| Provided | Behaviour |
|----------|-----------|
| FD + FS + ET + Levels | Full standards run |
| FD + FS + ET | ByLevel columns blank, level checks not evaluated |
| FD + FS | Resolves to template paths only |
| FD only | Inventory and paths only |
| Workspace, no exports | Configuration and inventory audit only |
| Exports, no workspace | Standards audit only |

## Configuration

Copy `healthcheck.config.sample.json` to `healthcheck.config.json` next to the
executable, or pass `--config <path>`. Search order is `--config`, then the current
directory, then the executable's directory. Every key is optional.

Useful knobs:

- `severity_overrides` — remap any check, e.g. set `orphan_element_template` to `INFO`
  if the agency intentionally ships spare templates.
- `ignore` — `fnmatch` globs to suppress known-benign items.
- `verdict_policy.warnings_block_production` — promote every WARN to blocking.
- `config_verification.roles` — the variable-to-role map, since agencies differ.
- `output.*` — branding for the workbook.

### A note on `<` and `>`

Path variables such as `MS_DGNLIBLIST`, `MS_DEF`, `MS_CELLLIST` and `MS_SEEDFILES` are
semicolon-separated lists searched **left to right**, so position in the list is
priority. The resolver implements:

| Operator | Name | Effect |
|----------|------|--------|
| `=` | Set | Overwrites any previous value. Last one wins. |
| `:` | Set if undefined | Applied only when the variable has no value yet. A default. |
| `<` | Prepend | Inserts at the front of the list, so it is searched first and wins. |
| `>` | Append | Adds to the end of the list, so it acts as a fallback. |

Because the first match wins, the **Ambiguous DGNLIB names** check resolves duplicates
by list position rather than by configuration level, and names the copy that actually
loses. `config_verification.prepend_operator` exists only for the unlikely case of a
product build that behaves differently.

A trailing `\` is treated as a line continuation only when it is preceded by
whitespace, so path values ending in a separator are not swallowed.

## Building the executable

```powershell
.\build.ps1
```

This produces `dist\WorkspaceChecker\WorkspaceChecker.exe` plus a zip. A one-folder
build is used deliberately: `--onefile` self-extracts to `%TEMP%` on every launch,
which is exactly the behaviour agency endpoint protection quarantines. Sign the
executable if your organization requires it.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The fixtures deliberately reproduce field failure modes: the blank line before the
level CSV header, duplicated `GeometryAspect` / `GeometryAspect_V2` references, the
same symbology path under two aspect types, dead paths after a server migration, a
WorkSet copy shadowing an organization library, an `%include` cycle, mapped-drive
leakage, and a trailing list separator that must not create a phantom member.

## Layout

```
workspace_checker/
  cli.py            argparse, exit codes
  gui.py            Tkinter window
  pipeline.py       orchestration
  crawl.py          workspace tree walk and inventory
  detect.py         export role and tag detection
  parse_fd|fs|et|levels.py
  resolve.py        chain resolution
  health.py         standards checks
  config.py         settings load and merge
  report_xlsx.py    workbook writer
  report_json.py    JSON summary
  cfg/
    resolver.py     MicroStation configuration semantics
    roles.py        DGNLIB role coverage
    checks.py       wiring checks
  extract/
    locator.py      Bentley product discovery
    adapter.py      export driver
```
