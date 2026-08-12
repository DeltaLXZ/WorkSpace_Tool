"""Offline DGNLIB reading.

The real libraries are agency property and cannot ship as fixtures, so the parsing
rules are exercised against synthetic payloads built the way a DGN stream is: zlib
behind a 16-byte header, UTF-16 EC schema documents, and length-prefixed names.
"""

import zlib

import pytest

import struct

from workspace_checker.dgn.reader import (
    OLE_MAGIC,
    extract_definitions,
    extract_schemas,
    harvest_names,
    inflate,
    is_dgn_container,
    iter_records,
    read_library,
)

SCHEMA_XML = (
    '<?xml version="1.0" encoding="UTF-16"?>'
    '<ECSchema xmlns="http://www.bentley.com/schemas/Bentley.ECXML.2.0" '
    'schemaName="Bentley_Civil__Model_Geometry" nameSpacePrefix="bcmg" '
    'version="04.08" description="">'
    '<ECClass typeName="Active3dRule"/>'
    "</ECSchema>"
)


class TestContainerDetection:
    def test_ole_magic_is_required(self, tmp_path):
        good = tmp_path / "a.dgnlib"
        good.write_bytes(OLE_MAGIC + b"\x00" * 32)
        assert is_dgn_container(good)

    def test_plain_text_is_not_a_container(self, tmp_path):
        bad = tmp_path / "b.dgnlib"
        bad.write_text("Placeholder standing in for a binary DGN V8 library.")
        assert not is_dgn_container(bad)

    def test_missing_file_is_not_a_container(self, tmp_path):
        assert not is_dgn_container(tmp_path / "absent.dgnlib")


class TestInflate:
    def test_payload_behind_the_sixteen_byte_header(self):
        body = b"the quick brown fox " * 40
        stream = b"\x00" * 16 + zlib.compress(body)
        assert inflate(stream) == body

    def test_payload_with_no_header(self):
        body = b"levels and templates " * 40
        assert inflate(zlib.compress(body)) == body

    def test_uncompressed_stream_returns_none(self):
        assert inflate(b"\x01\x02\x03\x04" * 64) is None

    def test_short_stream_returns_none(self):
        assert inflate(b"\x00\x00") is None


class TestSchemaExtraction:
    def test_reads_name_and_version_from_utf16(self):
        payload = b"\x00\x00" + SCHEMA_XML.encode("utf-16-le") + b"\xff\xff"
        schemas = extract_schemas(payload)
        assert len(schemas) == 1
        assert schemas[0].name == "Bentley_Civil__Model_Geometry"

    def test_version_is_the_schema_not_the_xml_declaration(self):
        """The declaration says version="1.0"; the schema says 04.08."""
        payload = SCHEMA_XML.encode("utf-16-le")
        assert extract_schemas(payload)[0].version == "04.08"

    def test_payload_without_a_schema_yields_nothing(self):
        assert extract_schemas(b"\x00\x01\x02" * 100) == []


class TestNameHarvesting:
    @staticmethod
    def _prefixed(name: str) -> bytes:
        return bytes([len(name)]) + name.encode("ascii")

    def test_reads_a_length_prefixed_name(self):
        payload = b"\x00" + self._prefixed("Top Back of Curb") + b"\x00"
        assert "Top Back of Curb" in harvest_names(payload)

    def test_reads_a_name_whose_length_byte_is_itself_printable(self):
        # 38 is "&", so the prefix is absorbed into the printable run and the real
        # name starts one character in.
        name = "3D Cable Barrier v2 - Left Leading End"
        assert len(name) == 38 and chr(len(name)) == "&"
        payload = b"\x00" + self._prefixed(name) + b"\x00"
        assert name in harvest_names(payload)

    def test_rejects_a_run_whose_prefix_does_not_match(self):
        payload = b"\x00\x63" + b"Top Back of Curb" + b"\x00"
        assert harvest_names(payload) == []

    def test_rejects_mostly_punctuation(self):
        payload = b"\x00" + self._prefixed("....--..//..") + b"\x00"
        assert harvest_names(payload) == []

    def test_rejects_runs_below_the_minimum_length(self):
        payload = b"\x00" + self._prefixed("ab") + b"\x00"
        assert harvest_names(payload) == []


class TestRecords:
    @staticmethod
    def _record(sequence: int, text: str, declared: int | None = None) -> bytes:
        body = text.encode("ascii")
        if declared is None:
            declared = len(body) + 4
        return struct.pack("<III", 0x56D2100F, sequence, declared) + b"\xff\xfe\x01\x00" + body + b"\x00"

    def test_reads_a_self_consistent_record(self):
        payload = b"\x00" * 8 + self._record(1, "Bridge_Abutment")
        assert list(iter_records(payload)) == [(1, "Bridge_Abutment")]

    def test_rejects_a_record_whose_declared_length_disagrees(self):
        payload = b"\x00" * 8 + self._record(1, "Bridge_Abutment", declared=99)
        assert list(iter_records(payload)) == []

    def test_name_followed_by_description_is_one_definition(self):
        payload = (
            b"\x00" * 8
            + self._record(1, "Bridge_Abutment")
            + self._record(2, "Prop Bridge Abutment")
        )
        definitions = extract_definitions(payload)
        assert len(definitions) == 1
        assert definitions[0].name == "Bridge_Abutment"
        assert definitions[0].description == "Prop Bridge Abutment"

    def test_name_without_a_description_stands_alone(self):
        payload = b"\x00" * 8 + self._record(1, "Bridge_Cap") + self._record(1, "Bridge_Deck")
        definitions = extract_definitions(payload)
        assert [d.name for d in definitions] == ["Bridge_Cap", "Bridge_Deck"]
        assert all(d.description == "" for d in definitions)

    def test_orphan_description_is_not_a_definition(self):
        payload = b"\x00" * 8 + self._record(2, "Prop Bridge Abutment")
        assert extract_definitions(payload) == []


class TestReadLibrary:
    def test_non_container_is_reported_not_raised(self, tmp_path):
        target = tmp_path / "fake.dgnlib"
        target.write_text("not a DGN")
        library = read_library(target)
        assert not library.ok
        assert "OLE2" in library.error

    def test_missing_file_is_reported_not_raised(self, tmp_path):
        library = read_library(tmp_path / "absent.dgnlib")
        assert not library.ok


@pytest.mark.skipif(
    not is_dgn_container(
        r"C:\Projects\Basic_MSC\Standards\Dgnlib\Printing\PrintStyles.dgnlib"
    ),
    reason="No real DGNLIB available on this machine",
)
def test_reads_a_real_library_end_to_end():
    library = read_library(
        r"C:\Projects\Basic_MSC\Standards\Dgnlib\Printing\PrintStyles.dgnlib"
    )
    assert library.ok
    assert library.streams
    assert any(s.name for s in library.schemas)
