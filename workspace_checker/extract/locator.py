"""Discovery of installed Bentley CONNECT products.

A .dgnlib is a binary DGN V8 container with no open-source reader, so automatic
extraction of feature definitions, symbologies, templates and levels requires one of
these products to be installed. When none is found the caller must degrade to
user-supplied exports rather than fail outright.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

PRODUCT_EXES = {
    "openbridgemodeler.exe": "OpenBridge",
    "openbridgedesigner.exe": "OpenBridge",
    "openroadsdesigner.exe": "OpenRoads",
    "openraildesigner.exe": "OpenRail",
    "opensitedesigner.exe": "OpenSite",
    "microstation.exe": "MicroStation",
}

FAMILY_ORDER = ["OpenBridge", "OpenRoads", "OpenRail", "OpenSite", "MicroStation"]

# "OpenRoads Designer CE 10.12", "OpenBridge Modeler 2024.00", "MicroStation 2023"
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+|\b(?:19|20)\d{2}\b)")

# Executables sit at <root>/<Product Folder>/<SubDir>/<exe>. A full rglob of a Bentley
# install walks gigabytes and takes ~20 seconds, so the scan is depth-bounded.
_MAX_DEPTH = 3

_cache: list["BentleyProduct"] | None = None


@dataclass
class BentleyProduct:
    family: str
    name: str
    version: str
    exe: str
    root: str
    version_key: tuple[int, ...] = field(default=())

    @property
    def label(self) -> str:
        if not self.version or self.version in self.name:
            return self.name
        return f"{self.name} {self.version}"

    def matches(self, token: str) -> bool:
        token = token.strip().lower()
        if not token:
            return False
        return (
            token == self.family.lower()
            or token == self.version.lower()
            or token == self.exe.lower()
            or token in self.name.lower()
        )


class BentleyProductNotFound(Exception):
    """Raised when no Bentley product is available to perform extraction."""

    MESSAGE = (
        "No Bentley product (OpenRoads / OpenBridge / OpenRail / MicroStation) was found "
        "on this machine.\n"
        "A .dgnlib is a binary format that only these products can read, so the checker "
        "cannot extract standards automatically.\n\n"
        "Do one of the following:\n"
        "  1. Install or repair OpenRoads/OpenBridge/OpenRail on this machine, or\n"
        "  2. Point the checker at the executable with --product <path>, or\n"
        "  3. Export the four files yourself and point the checker at them:\n"
        "       - Explorer > OpenRoads Standards > Feature Definitions > Export -> <TAG>_FD.xml\n"
        "       - Explorer > Feature Symbologies > Export                  -> <TAG>_FS.xml\n"
        "       - Element Templates dialog > File > Export                 -> <TAG>_ET.xml\n"
        "       - Level Manager > Levels > Export > .csv                   -> <TAG>_Level.csv\n"
        "     (Set _CIVIL_STANDARDS_IMPORTEXPORT = 1 first, and open the DGNLIB itself so you "
        "capture the library standards rather than an in-file copy.)\n\n"
        "The workspace and configuration audit still runs without a Bentley product."
    )

    def __str__(self) -> str:
        return self.MESSAGE


def parse_version(text: str) -> tuple[str, tuple[int, ...]]:
    """Return the display version and a numeric sort key from a folder or file name."""
    match = _VERSION_RE.search(text or "")
    if not match:
        return "", ()
    display = match.group(1)
    return display, tuple(int(p) for p in display.split(".") if p.isdigit())


def _file_version(exe: Path) -> tuple[str, tuple[int, ...]]:
    """Read the Windows version resource, for installs whose folder carries no version."""
    if os.name != "nt":
        return "", ()
    try:
        import ctypes
        from ctypes import wintypes

        version = ctypes.windll.version
        size = version.GetFileVersionInfoSizeW(str(exe), None)
        if not size:
            return "", ()
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(exe), 0, size, buffer):
            return "", ()
        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return "", ()
        info = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
        most, least = info[2], info[3]
        parts = (most >> 16, most & 0xFFFF, least >> 16, least & 0xFFFF)
        return ".".join(str(p) for p in parts), parts
    except (OSError, AttributeError, ValueError):
        return "", ()


def _search_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(var)
        if not base:
            continue
        candidate = Path(base) / "Bentley"
        key = str(candidate).lower()
        if candidate.is_dir() and key not in seen:
            seen.add(key)
            roots.append(candidate)
    extra = os.environ.get("MSDIR")
    if extra and Path(extra).is_dir():
        roots.append(Path(extra))
    return roots


def _scan(root: Path, depth: int = 0) -> list[Path]:
    found: list[Path] = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return found
    for entry in entries:
        try:
            if entry.is_file():
                if entry.name.lower() in PRODUCT_EXES:
                    found.append(Path(entry.path))
            elif entry.is_dir() and depth < _MAX_DEPTH:
                found.extend(_scan(Path(entry.path), depth + 1))
        except OSError:
            continue
    return found


def _product_from_exe(exe: Path, root: Path | None = None) -> BentleyProduct:
    family = PRODUCT_EXES.get(exe.name.lower(), "MicroStation")
    name, version, key = exe.stem, "", ()

    # The installation folder name carries the marketing version; prefer it.
    for parent in exe.parents:
        if root is not None and (parent == root or parent in root.parents):
            break
        display, parsed = parse_version(parent.name)
        if display:
            name, version, key = parent.name, display, parsed
            break
        name = parent.name

    if not version:
        version, key = _file_version(exe)

    return BentleyProduct(family, name, version, str(exe), str(exe.parent), key)


def find_products(
    explicit: str | os.PathLike | None = None, refresh: bool = False
) -> list[BentleyProduct]:
    """Installed products, preferred family first and newest version first."""
    if explicit:
        product = _from_explicit(explicit)
        if product:
            return [product]
        log.warning("Requested product not found: %s", explicit)

    global _cache
    if _cache is not None and not refresh:
        return list(_cache)

    products: dict[str, BentleyProduct] = {}
    for root in _search_roots():
        for exe in _scan(root):
            key = str(exe).lower()
            if key not in products:
                products[key] = _product_from_exe(exe, root)

    ordered = sorted(
        products.values(),
        key=lambda p: (
            FAMILY_ORDER.index(p.family) if p.family in FAMILY_ORDER else 99,
            tuple(-part for part in p.version_key),
            p.name,
        ),
    )
    _cache = ordered
    log.debug("Found %d Bentley product(s)", len(ordered))
    return list(ordered)


def _from_explicit(explicit: str | os.PathLike) -> BentleyProduct | None:
    """Resolve --product: an executable path, an install folder, or a name fragment."""
    path = Path(explicit)
    if path.is_file():
        return _product_from_exe(path)
    if path.is_dir():
        for exe in _scan(path):
            return _product_from_exe(exe, path)
        return None

    matches = [p for p in find_products() if p.matches(str(explicit))]
    return matches[0] if matches else None


def select_product(
    preference: list[str] | None = None, explicit: str | os.PathLike | None = None
) -> BentleyProduct:
    """Pick the best available product, or raise BentleyProductNotFound."""
    if explicit:
        product = _from_explicit(explicit)
        if product:
            return product
        raise BentleyProductNotFound()

    products = find_products()
    if not products:
        raise BentleyProductNotFound()
    for family in preference or []:
        for product in products:
            if product.family.lower() == family.lower():
                return product
    return products[0]


def describe_products() -> str:
    products = find_products()
    if not products:
        return "No Bentley products found."
    width = max(len(p.family) for p in products)
    lines = [f"{len(products)} Bentley product(s) found; the first is the default:"]
    lines += [f"  {p.family.ljust(width)}  {p.name}  ->  {p.exe}" for p in products]
    return "\n".join(lines)
