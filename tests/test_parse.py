from workspace_checker.detect import classify, group_by_tag
from workspace_checker.parse_et import parse_color_index, parse_et
from workspace_checker.parse_fd import parse_fd, split_symbology_ref
from workspace_checker.parse_fs import parse_fs, short_relationship
from workspace_checker.parse_levels import parse_levels


class TestFeatureDefinitions:
    def test_parses_all_definitions(self, clean_dir):
        fds, warnings = parse_fd(clean_dir / "DEMO_FD.xml")
        assert warnings == []
        assert [f.name for f in fds] == ["Deck", "Abutment_steel_piles", "Supportline"]

    def test_fd_path_includes_provider_and_featurepath(self, clean_dir):
        fds, _ = parse_fd(clean_dir / "DEMO_FD.xml")
        by_name = {f.name: f for f in fds}
        assert by_name["Supportline"].fd_path == "SupportLine/Supportline"
        assert by_name["Abutment_steel_piles"].fd_path == "Supports/Abutment/Abutment_steel_piles"
        assert by_name["Deck"].item_type == "DeckEntityFeatureDefinition3d"

    def test_geometry_aspect_v2_duplicate_collapses(self, clean_dir):
        """OBM emits GeometryAspect and GeometryAspect_V2 for the same target."""
        fds, _ = parse_fd(clean_dir / "DEMO_FD.xml")
        deck = next(f for f in fds if f.name == "Deck")
        assert len(deck.refs) == 1

    def test_leading_aspect_token_is_dropped(self):
        assert split_symbology_ref(r"Solid\Abutments\Piles_steel>~Sol") == (
            "Solid",
            "Abutments",
            "Piles_steel",
        )
        assert split_symbology_ref(r"Linear\Auxiliary\Barriers\Barrier>~Lin") == (
            "Linear",
            r"Auxiliary\Barriers",
            "Barrier",
        )
        assert split_symbology_ref(r"Point\Marker>~Pnt") == ("Point", "", "Marker")


class TestFeatureSymbologies:
    def test_stype_is_part_of_the_key(self, clean_dir):
        """Same path+name under two aspect types must survive as two entries."""
        fs, _ = parse_fs(clean_dir / "DEMO_FS.xml")
        assert ("Solid", "Abutments", "Piles_steel") in fs
        assert ("Point", "Abutments", "Piles_steel") in fs
        assert len(fs) == 4

    def test_relationship_shortening(self):
        assert short_relationship("GeometricGeometryAspect__ThreeDElementTemplate") == "3D"
        assert short_relationship("SolidGeometryAspect__TopElementTemplateHolder") == "Top"
        assert short_relationship("Unknown__CustomElementTemplate") == "Custom"


class TestElementTemplates:
    def test_builds_backslash_ancestry_paths(self, clean_dir):
        et, dupes, warnings = parse_et(clean_dir / "DEMO_ET.xml")
        assert warnings == [] and dupes == []
        assert r"Auxiliary\Barriers\Barrier" in et
        assert r"Decks\Deck" in et

    def test_extracts_symbology_attributes(self, clean_dir):
        et, _, _ = parse_et(clean_dir / "DEMO_ET.xml")
        deck = et[r"Decks\Deck"]
        assert deck.level == "BR_Deck"
        assert deck.color == "11"
        assert deck.weight == "2"
        assert deck.material == "materials.dgnlib|Civil:Concrete"

    def test_color_index_is_the_last_token_before_the_colon(self):
        assert parse_color_index(r"0,1,11:[0,0,0]:\\\ ") == "11"
        assert parse_color_index("0,1,3:[0,0,0]") == "3"
        assert parse_color_index("") == ""


class TestLevels:
    def test_blank_line_between_section_and_header_is_skipped(self, clean_dir):
        levels, dupes, warnings = parse_levels(clean_dir / "DEMO_Level.csv")
        assert warnings == [] and dupes == []
        assert "BR_Deck" in levels

    def test_quoted_values_containing_commas(self, clean_dir):
        levels, _, _ = parse_levels(clean_dir / "DEMO_Level.csv")
        assert levels["BR_Deck"].bylevel_color == "11"
        assert levels["BR_Deck"].bylevel_weight == "2"
        assert levels["BR_Deck"].plot == "1"

    def test_stops_at_the_next_section(self, clean_dir):
        levels, _, _ = parse_levels(clean_dir / "DEMO_Level.csv")
        assert "Expression" not in levels
        assert len(levels) == 5

    def test_duplicates_are_reported(self, broken_dir):
        _, dupes, _ = parse_levels(broken_dir / "BAD_Level.csv")
        assert dupes == ["BR_Construction"]


class TestDetection:
    def test_role_and_tag_from_filename(self, clean_dir):
        assert classify(clean_dir / "DEMO_FD.xml") == ("FD", "DEMO")
        assert classify(clean_dir / "DEMO_Level.csv") == ("LEVEL", "DEMO")

    def test_grouping_resolves_one_set(self, clean_dir):
        groups, warnings = group_by_tag(sorted(clean_dir.iterdir()))
        assert warnings == []
        assert set(groups) == {"DEMO"}
        assert set(groups["DEMO"]) == {"FD", "FS", "ET", "LEVEL"}

    def test_embedded_dgnlib_name_when_filename_is_generic(self, clean_dir, tmp_path):
        generic = tmp_path / "export.xml"
        generic.write_bytes((clean_dir / "DEMO_FD.xml").read_bytes())
        groups, _ = group_by_tag([generic])
        assert "DEMO" in groups

    def test_forced_tag_wins(self, clean_dir):
        groups, _ = group_by_tag(sorted(clean_dir.iterdir()), forced="GDOT")
        assert set(groups) == {"GDOT"}
