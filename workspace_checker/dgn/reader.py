"""Reads a DGN V8 library directly, without an installed Bentley product.

A ``.dgnlib`` is an OLE2 compound file. Its payload streams are ordinary zlib
streams sitting behind a 16-byte header, and the inflated bytes carry two things
this checker cares about:

* the **EC XML schemas**, stored as UTF-16 documents, which name the civil
  domain classes a library participates in; and
* **length-prefixed name strings** -- level names, element template names,
  feature definition names -- as a single leading byte holding the length
  followed by that many ASCII characters.

That is not a full DGN element parser and does not try to be. The binary element
schema is undocumented, so anything requiring geometry or the resolved
FD -> FS -> ET -> Level chain still needs a product export. What this module
provides is an inventory of what a library *declares*, obtained offline, which is
enough to answer "what is in this DGNLIB" without consuming a licence.
"""

from __future__ import annotations

import logging
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Payload streams carry a 16-byte header ahead of the zlib data. The other offsets
# are cheap to try and cover streams that store it differently.
_ZLIB_OFFSETS = (16, 0, 2, 4, 8)

_SCHEMA_RE = re.compile(r"<\?xml.*?</ECSchema>", re.S)
_SCHEMA_NAME_RE = re.compile(r'schemaName="([^"]+)"')
# Anchored to the ECSchema tag: an unanchored version="..." matches the XML
# declaration first and reports every schema as v1.0.
_SCHEMA_VERSION_RE = re.compile(r'<ECSchema[^>]*?\sversion="([^"]+)"')

# A name is a plausible standards identifier: printable, mostly letters, and not a
# fragment of binary that happens to fall in the ASCII range.
_NAME_CHARS = re.compile(r"[A-Za-z0-9 _\-&()./\\]+")
_MIN_NAME = 4
_MAX_NAME = 120

# Named definitions are stored as a 12-byte header -- tag, sequence, declared length --
# followed by this marker and a NUL-terminated ASCII string. The declared length counts
# the string plus the 4-byte marker, and checking that agreement is what separates a
# real record from a coincidental byte sequence.
_RECORD_MARK = b"\xff\xfe\x01\x00"
_RECORD_RE = re.compile(re.escape(_RECORD_MARK))
_HEADER = 12
_DLEN_OVERHEAD = 4
_MAX_RECORD = 512

# Within a definition, sequence 1 carries the name and sequence 2 the description.
_SEQ_NAME = 1
_SEQ_DESCRIPTION = 2

# Element template parameters are stored as BECXML, a tokenised EC instance. It opens
# with a string dictionary of 0xFA <length> <ascii> records, then a value stream that
# refers to those strings by index. Only the dictionary is decoded here: it is
# unambiguous, whereas the value stream needs the schema to interpret.
_BECXML = b"BECXML"
_BECXML_TOKEN = 0xFA
_BECXML_WINDOW = 1400
_ELEMENT_PARAMS = "Ustn_ElementParams"


@dataclass
class EmbeddedSchema:
    name: str
    version: str
    xml: str

    @property
    def label(self) -> str:
        return f"{self.name} v{self.version}" if self.version else self.name


@dataclass
class Definition:
    """A named definition and its description, as stored in the library."""

    name: str
    description: str = ""


@dataclass
class DgnLibrary:
    """What a single .dgnlib declares, read offline."""

    path: str
    streams: dict[str, int] = field(default_factory=dict)
    schemas: list[EmbeddedSchema] = field(default_factory=list)
    definitions: list[Definition] = field(default_factory=list)
    template_levels: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def schema_names(self) -> list[str]:
        return sorted({s.name for s in self.schemas})


class NotADgnContainer(Exception):
    """The file is not an OLE2 compound document, so it is not a DGN V8 file."""


def is_dgn_container(path: str | Path) -> bool:
    """True if the file starts with the OLE2 signature. Cheap enough to call per file."""
    try:
        with open(path, "rb") as fh:
            return fh.read(8) == OLE_MAGIC
    except OSError:
        return False


def inflate(data: bytes) -> bytes | None:
    """Inflate a payload stream, or None if it is not zlib at any known offset.

    Streams are truncated rather than rejected when they end mid-block: a partial
    inventory beats discarding the library.
    """
    for offset in _ZLIB_OFFSETS:
        if offset >= len(data):
            continue
        try:
            out = zlib.decompressobj().decompress(data[offset:])
        except zlib.error:
            continue
        if len(out) > len(data):
            return out
    return None


def extract_schemas(payload: bytes) -> list[EmbeddedSchema]:
    """Every EC XML schema document in an inflated stream.

    The documents declare ``encoding="UTF-16"`` and are stored that way, so decoding
    is UTF-16LE first with a latin-1 fallback for streams that are not.
    """
    schemas: list[EmbeddedSchema] = []
    for text in (_decode_utf16(payload), payload.decode("latin-1", "ignore")):
        for match in _SCHEMA_RE.finditer(text):
            xml = match.group()
            name = _SCHEMA_NAME_RE.search(xml)
            version = _SCHEMA_VERSION_RE.search(xml)
            schemas.append(
                EmbeddedSchema(
                    name=name.group(1) if name else "",
                    version=version.group(1) if version else "",
                    xml=xml,
                )
            )
        if schemas:
            break
    return schemas


def iter_records(payload: bytes):
    """Yield ``(sequence, text)`` for every self-consistent named record.

    The declared length is authoritative and gives the string length directly.
    Locating the end by searching for a NUL instead overshoots whenever the bytes
    after the string happen to be non-zero, which silently drops records -- it cost
    one level in every OpenBridge library tested. Requiring the slice to be clean
    ASCII with no embedded NUL is what rejects coincidental matches.
    """
    for match in _RECORD_RE.finditer(payload):
        head = match.start() - _HEADER
        if head < 0:
            continue
        _tag, sequence, declared = struct.unpack_from("<III", payload, head)
        length = declared - _DLEN_OVERHEAD
        if not 0 < length <= _MAX_RECORD:
            continue
        start = match.end()
        raw = payload[start : start + length]
        if len(raw) != length or b"\x00" in raw:
            continue
        try:
            yield sequence, raw.decode("ascii")
        except UnicodeDecodeError:
            continue


def extract_definitions(payload: bytes) -> list[Definition]:
    """Named definitions in an inflated stream.

    A name record immediately followed by a description record is one definition;
    a name with no description following stands alone. These are predominantly
    levels, but models and other named items share the encoding, so this is
    deliberately called a definition rather than a level.
    """
    records = list(iter_records(payload))
    definitions: list[Definition] = []
    index = 0
    while index < len(records):
        sequence, text = records[index]
        if sequence != _SEQ_NAME:
            index += 1
            continue
        if index + 1 < len(records) and records[index + 1][0] == _SEQ_DESCRIPTION:
            definitions.append(Definition(text, records[index + 1][1]))
            index += 2
        else:
            definitions.append(Definition(text))
            index += 1
    return definitions


def decode_becxml(blob: bytes) -> list[str]:
    """The string dictionary of a BECXML blob, in storage order.

    Each entry is ``0xFA <length> <ascii>``. Bytes that do not form a valid entry are
    part of the value stream and are skipped.

    This is the dictionary, **not** document order: a string used many times in the
    document appears once here. Reading a property's value by taking the token after
    its name therefore only works by coincidence, and must not be relied on.
    """
    strings: list[str] = []
    index = 0
    while index < len(blob):
        if blob[index] == _BECXML_TOKEN and index + 1 < len(blob):
            length = blob[index + 1]
            candidate = blob[index + 2 : index + 2 + length]
            if len(candidate) == length and all(32 <= c < 127 for c in candidate):
                strings.append(candidate.decode("ascii"))
                index += 2 + length
                continue
        index += 1
    return strings


def element_param_strings(payload: bytes) -> list[list[str]]:
    """The dictionary of every element-template parameter blob in a stream.

    An element template records the level it draws on, so these dictionaries contain
    level names. Which template names which level needs the value stream decoded, so
    this deliberately returns the raw dictionaries rather than implying a mapping.
    """
    blobs: list[list[str]] = []
    for match in re.finditer(_BECXML, payload):
        strings = decode_becxml(payload[match.start() : match.start() + _BECXML_WINDOW])
        if _ELEMENT_PARAMS in strings[:8]:
            blobs.append(strings)
    return blobs


def harvest_names(payload: bytes) -> list[str]:
    """Length-prefixed ASCII names in an inflated stream, in file order.

    Each candidate is a byte holding a length followed by exactly that many
    printable characters. Requiring the prefix to agree with the run length is what
    separates real names from binary that happens to be printable.
    """
    names: list[str] = []
    text_view = payload.decode("latin-1", "ignore")
    for match in _NAME_CHARS.finditer(text_view):
        start, run = match.start(), match.group()
        # The length byte is often itself printable ("&" is 38), in which case it is
        # absorbed into the run and the real name starts one character in.
        if start > 0 and payload[start - 1] == len(run):
            candidate = run
        elif len(run) > 1 and ord(run[0]) == len(run) - 1:
            candidate = run[1:]
        else:
            continue
        if not (_MIN_NAME <= len(candidate) <= _MAX_NAME):
            continue
        if not _plausible_name(candidate):
            continue
        names.append(candidate)
    return names


def _plausible_name(text: str) -> bool:
    letters = sum(c.isalpha() for c in text)
    return letters >= 3 and letters / len(text) > 0.5


def _decode_utf16(payload: bytes) -> str:
    # Schemas may start on either byte parity depending on the surrounding record.
    for start in (0, 1):
        chunk = payload[start:]
        text = chunk[: len(chunk) - (len(chunk) % 2)].decode("utf-16-le", "ignore")
        if "<ECSchema" in text:
            return text
    return ""


def read_library(path: str | Path) -> DgnLibrary:
    """Read one .dgnlib. Never raises: a failure is reported on the result."""
    import olefile

    target = Path(path)
    library = DgnLibrary(path=str(target))

    if not is_dgn_container(target):
        library.error = "not an OLE2 compound file, so not a DGN V8 library"
        return library

    try:
        ole = olefile.OleFileIO(str(target))
    except OSError as exc:
        library.error = f"cannot open: {exc}"
        return library

    seen_names: set[str] = set()
    try:
        for entry in ole.listdir():
            name = "/".join(entry)
            try:
                raw = ole.openstream(entry).read()
            except OSError as exc:
                library.unreadable.append(f"{name}: {exc}")
                continue
            payload = inflate(raw)
            if payload is None:
                library.streams[name] = len(raw)
                continue
            library.streams[name] = len(payload)
            library.schemas.extend(extract_schemas(payload))
            library.definitions.extend(extract_definitions(payload))
            library.template_levels.extend(element_param_strings(payload))
            for candidate in harvest_names(payload):
                if candidate not in seen_names:
                    seen_names.add(candidate)
                    library.names.append(candidate)
    finally:
        ole.close()

    # Names appearing in an element-template parameter blob are what that library's
    # templates refer to. Intersecting with the library's own definitions is what keeps
    # this usable without decoding the value stream, but it cannot separate a level from
    # a material or other named item the same template mentions: against a known-good ET
    # export this returned 45 names for 44 real levels, the extra being a material.
    # Treat it as the referenced set, not a verified level list.
    declared = {d.name for d in library.definitions}
    referenced: list[str] = []
    for strings in library.template_levels:
        for candidate in strings:
            if candidate in declared and candidate not in referenced:
                referenced.append(candidate)
    library.template_levels = referenced

    seen_definitions: set[tuple[str, str]] = set()
    unique_definitions: list[Definition] = []
    for definition in library.definitions:
        key = (definition.name, definition.description)
        if key not in seen_definitions:
            seen_definitions.add(key)
            unique_definitions.append(definition)
    library.definitions = unique_definitions

    # Schemas repeat across streams; keep one entry per name and version.
    unique: dict[tuple[str, str], EmbeddedSchema] = {}
    for schema in library.schemas:
        unique.setdefault((schema.name, schema.version), schema)
    library.schemas = list(unique.values())
    return library


def read_libraries(paths: list[str | Path]) -> list[DgnLibrary]:
    return [read_library(p) for p in paths]
