"""Drives an installed Bentley product to export standards from a DGNLIB.

The export mechanism is deliberately data-driven: the key-in sequence lives in
``healthcheck.config.json`` under ``extraction.keyins`` so it can be corrected for a
specific product version without rebuilding the executable. If a run produces no
files, extraction reports a clear failure and the pipeline falls back to
user-supplied exports rather than inventing results.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..constants import ALL_ROLES
from ..detect import classify
from .locator import BentleyProduct, BentleyProductNotFound, select_product

log = logging.getLogger(__name__)

# Deliberately empty. A binary scan of the ORD 2024 install found no "civilstandards"
# key-in, so the previously guessed sequence was wrong. Until a mechanism is verified,
# extraction refuses to launch rather than consuming a licence to produce nothing.
DEFAULT_KEYINS: list[str] = []

NO_MECHANISM = (
    "Automatic extraction is not configured. No verified key-in sequence exists for "
    "exporting civil standards, so nothing was launched.\n"
    "Set 'extraction.keyins' in healthcheck.config.json if you have a sequence that "
    "works for your product version, or export the four files manually "
    "(FD / FS / ET XML and the Level CSV) and pass them in."
)


@dataclass
class ExtractionResult:
    dgnlib: str
    tag: str
    files: dict[str, str] = field(default_factory=dict)
    ok: bool = False
    message: str = ""
    command: str = ""
    from_cache: bool = False

    @property
    def roles(self) -> list[str]:
        return [r for r in ALL_ROLES if r in self.files]


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()[:16]


def _tag_for(dgnlib: Path) -> str:
    stem = dgnlib.stem
    for sep in ("_", "-", " "):
        if sep in stem:
            head = stem.split(sep, 1)[0]
            if head:
                return head
    return stem


class StandardsExtractor:
    """Extracts FD/FS/ET/Level exports from DGNLIBs using a Bentley product."""

    def __init__(
        self,
        settings: Settings,
        product: BentleyProduct | None = None,
        explicit: str | None = None,
    ):
        self.settings = settings
        self.log: list[str] = []
        self.product = product or select_product(
            settings.get("extraction", "product_preference", default=None),
            explicit
            or settings.get("extraction", "product", default="")
            or settings.get("extraction", "product_path", default="")
            or None,
        )
        self.timeout = int(settings.get("extraction", "timeout_seconds", default=300))
        self.keyins = settings.get("extraction", "keyins", default=DEFAULT_KEYINS)
        self.cache_enabled = bool(settings.get("extraction", "cache", default=True))
        self._cache_root = Path(tempfile.gettempdir()) / "workspace_checker_cache"

    # -- public ------------------------------------------------------------- #
    def extract_many(self, dgnlibs: list[str | os.PathLike], outdir: str | os.PathLike):
        results = []
        for lib in dgnlibs:
            try:
                results.append(self.extract(Path(lib), Path(outdir)))
            except OSError as exc:
                results.append(
                    ExtractionResult(str(lib), _tag_for(Path(lib)), message=f"{exc}")
                )
        return results

    def extract(self, dgnlib: Path, outdir: Path) -> ExtractionResult:
        tag = _tag_for(dgnlib)
        result = ExtractionResult(dgnlib=str(dgnlib), tag=tag)
        outdir.mkdir(parents=True, exist_ok=True)

        cached = self._from_cache(dgnlib, outdir, tag)
        if cached is not None:
            return cached

        if not self.keyins:
            result.message = NO_MECHANISM
            self.log.append(f"{dgnlib.name}: skipped, no verified extraction mechanism")
            return result

        work = outdir / f"_{tag}_{_sha1(dgnlib)}"
        work.mkdir(parents=True, exist_ok=True)

        command = self._build_command(dgnlib, work, tag)
        result.command = " ".join(command)
        log.info("Extracting %s via %s", dgnlib.name, self.product.label)

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-3:]
            if completed.returncode != 0:
                result.message = (
                    f"{self.product.name} exited with code {completed.returncode}. "
                    + " ".join(tail)
                ).strip()
        except subprocess.TimeoutExpired:
            result.message = f"{self.product.name} did not finish within {self.timeout}s"
        except OSError as exc:
            result.message = f"Could not start {self.product.name}: {exc}"

        result.files = self._collect(work)
        result.ok = bool(result.files)
        if not result.ok and not result.message:
            result.message = (
                f"{self.product.label} ran but produced no exports. Verify the key-in "
                "sequence in healthcheck.config.json under 'extraction.keyins' for this "
                "product version, or export the four files manually."
            )
        if result.ok:
            self._to_cache(dgnlib, work)
        self.log.append(
            f"{dgnlib.name}: " + (f"extracted {', '.join(result.roles)}" if result.ok else result.message)
        )
        return result

    def dry_run(self, dgnlib: Path, outdir: Path) -> str:
        return " ".join(self._build_command(Path(dgnlib), Path(outdir), _tag_for(Path(dgnlib))))

    # -- internals ---------------------------------------------------------- #
    def _build_command(self, dgnlib: Path, outdir: Path, tag: str) -> list[str]:
        command = [self.product.exe, str(dgnlib)]
        for keyin in self.keyins:
            command.append("-i" + keyin.format(outdir=str(outdir), tag=tag, dgnlib=str(dgnlib)))
        command.append("-wc")  # no workflow customisation, keeps startup lean
        return command

    def _collect(self, work: Path) -> dict[str, str]:
        files: dict[str, str] = {}
        for path in sorted(work.iterdir()) if work.is_dir() else []:
            if not path.is_file():
                continue
            role, _ = classify(path)
            if role and role not in files:
                files[role] = str(path)
        return files

    def _cache_dir(self, dgnlib: Path) -> Path:
        # The DGNLIB alone is not the whole input: the key-in sequence and the product
        # driving it both change the result. Keying on the DGNLIB only meant that
        # correcting extraction.keyins, or switching --product, silently replayed the
        # previous run -- exactly the loop someone is in while finding a sequence
        # that works.
        recipe = hashlib.sha1(
            "\n".join([*self.keyins, self.product.exe]).encode("utf-8", "replace")
        ).hexdigest()[:12]
        return self._cache_root / f"{dgnlib.stem}_{_sha1(dgnlib)}_{recipe}"

    def _from_cache(self, dgnlib: Path, outdir: Path, tag: str) -> ExtractionResult | None:
        if not self.cache_enabled:
            return None
        cache = self._cache_dir(dgnlib)
        if not cache.is_dir():
            return None
        files = self._collect(cache)
        if not files:
            return None
        result = ExtractionResult(str(dgnlib), tag, files=files, ok=True, from_cache=True)
        result.message = "reused cached extraction"
        self.log.append(f"{dgnlib.name}: reused cached extraction")
        return result

    def _to_cache(self, dgnlib: Path, work: Path) -> None:
        if not self.cache_enabled:
            return
        cache = self._cache_dir(dgnlib)
        try:
            if cache.exists():
                shutil.rmtree(cache)
            shutil.copytree(work, cache)
        except OSError as exc:
            log.debug("Could not cache extraction for %s: %s", dgnlib.name, exc)


def try_extract(
    dgnlibs: list[str],
    outdir: str | os.PathLike,
    settings: Settings,
    product: str | None = None,
) -> tuple[list[ExtractionResult], list[str]]:
    """Extract from every DGNLIB, returning results and a human-readable log.

    Never raises: a missing Bentley product is reported as a log entry so the rest of
    the audit can proceed.
    """
    if not dgnlibs:
        return [], ["No DGNLIBs to extract from."]
    if not settings.get("extraction", "enabled", default=True):
        return [], ["Extraction disabled by configuration."]
    if not settings.get("extraction", "keyins", default=DEFAULT_KEYINS):
        return [], NO_MECHANISM.splitlines()

    try:
        extractor = StandardsExtractor(settings, explicit=product)
    except BentleyProductNotFound as exc:
        return [], str(exc).splitlines()

    results = extractor.extract_many(dgnlibs, outdir)
    header = [f"Using {extractor.product.label} at {extractor.product.exe}"]
    return results, header + extractor.log
