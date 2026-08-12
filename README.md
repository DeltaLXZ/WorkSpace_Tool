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

## Reading DGNLIBs directly

A `.dgnlib` is a binary DGN V8 container, but it is not opaque: it is an OLE2 compound
file whose payload streams are ordinary zlib behind a 16-byte header. Inflated, they
carry the EC XML schemas as UTF-16 documents and the library's names as
length-prefixed ASCII. That is enough to inventory what a library declares **without an
installed Bentley product and without consuming a licence**.

```powershell
# One library, a folder, or several -- folders are searched recursively
workspace-checker --read-dgnlib "D:\Workspaces\GDOT\Standards\Dgnlib"
workspace-checker --read-dgnlib Features.dgnlib PrintStyles.dgnlib
workspace-checker --read-dgnlib Features.dgnlib --names   # every name, not a sample
```

For each library this reports the stream count, every EC schema with its version, the
**named definitions** with their descriptions, and the loose names found.

Definitions are the useful part. They are stored as a 12-byte header — tag, sequence,
declared length — followed by a marker and the string, where sequence 1 is a name and
sequence 2 its description. The declared length must agree with the string length,
which is a strong enough check that no guessing is involved. One KYTC feature library
yields 824:

```
Bridge_Abutment                Prop Bridge Abutment
Bridge_Cap                     Prop Bridge Caps
Draft_Corr_Design              Design-Civil-Corridor Graphics
Util_Communications            SUE Communications
```

These are predominantly levels, but models and other named items share the encoding, so
they are reported as definitions rather than asserted to be levels.

Names containing a space are the human-authored ones — Feature Definitions, Element
Templates, template paths. The rest are EC property identifiers, available with
`--names`.

### What this does not do

This is an inventory, not a full DGN element parser. It cannot yet resolve the Feature
Definition to Feature Symbology to Element Template to Level chain, and **it does not
read symbology values** — colour, weight and style. The stream carries the field
identifiers (`level-color`, `level-style`, `level-description` appear literally), and
numeric fields sit immediately after each definition, but assigning meaning to those
offsets without a known-good export to check against would mean guessing. A wrong guess
would produce confidently incorrect audit results, which is worse than reporting
`NOT_EVALUATED`. One Level CSV exported from any workspace is enough to pin the offsets
down; until then symbology comes from supplied exports only.

## How the DGNLIB extraction works, and its limits

For the resolved standards chain, the checker asks an installed Bentley product to
perform the four exports and then parses those. If no product is installed the run does
**not** fail: the workspace and configuration audit still completes, and the standards
checks report `NOT_EVALUATED` with instructions to export manually.

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
`extraction.keyins`, and **ships empty**. An earlier release guessed at a
`civilstandards export …` sequence; a binary scan of the ORD 2024 install found no such
key-in, so it launched the product, consumed a licence and produced nothing. Rather
than ship a guess, extraction now refuses to launch until you supply a sequence
verified against your own product version. Key-in names differ between releases.

With no sequence configured, the workspace and configuration audit still runs in full
and the standards checks report `NOT_EVALUATED` — never an empty standard set reported
as clean. Export manually as below, or supply the four files directly.

Extraction results are cached per DGNLIB **and** per key-in sequence and product, so
correcting `extraction.keyins` or switching `--product` re-runs rather than replaying
the previous attempt. Set `extraction.cache` to `false` to disable caching entirely.

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
| DGNLIBs, no product | `--read-dgnlib` inventory: schemas and names, no resolved chain |

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

`%include` accepts a wildcard filespec and an optional trailing precedence clause, both
of which the resolver honours:

```
%include $(_USTN_WORKSPACEROOT)*.cfg level WorkSpace
```

Every match is read, in sorted order, at the declared level. A pattern that matches
nothing is reported as a missing include, naming the pattern rather than a phantom file.

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
  dgn/
    reader.py       offline DGN V8 reading (OLE2 + zlib + EC schemas)
  extract/
    locator.py      Bentley product discovery
    adapter.py      export driver
```
