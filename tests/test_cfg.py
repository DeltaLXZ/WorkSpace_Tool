from pathlib import Path

from workspace_checker.cfg.checks import run_config_checks
from workspace_checker.cfg.resolver import (
    CfgResolver,
    _split_include,
    _strip_comment,
    _tokenize,
)
from workspace_checker.cfg.roles import collect_dgnlibs, export_enabled, role_coverage
from workspace_checker.config import load_settings
from workspace_checker.crawl import crawl, pick_cfg_entry_points
from workspace_checker.models import Severity

CFG_FIXTURES = Path(__file__).parent / "fixtures" / "cfg"


def _resolve(folder: Path):
    settings = load_settings()
    tree = crawl(folder, settings)
    resolver = CfgResolver(settings, seed_env={"TESTROOT": str(folder)})
    model = resolver.process(pick_cfg_entry_points(tree))
    roles = role_coverage(model, settings)
    dgnlibs = collect_dgnlibs(model, tree, settings, roles)
    checks = {c.name: c for c in run_config_checks(model, roles, dgnlibs, tree, settings)}
    return model, roles, dgnlibs, checks


class TestResolverSemantics:
    def test_macro_expansion_and_include(self):
        model, _, _, _ = _resolve(CFG_FIXTURES / "good")
        root = model.get("_USTN_WORKSPACEROOT")
        assert root.endswith("\\")
        assert "dgnlib" in model.get("MS_DGNLIBLIST").lower()
        assert model.get("MS_CELLLIST"), "%include did not contribute MS_CELLLIST"

    def test_conditional_set_does_not_override(self):
        model, _, _, _ = _resolve(CFG_FIXTURES / "good")
        assert "Should\\Never\\Win" not in model.get("_USTN_WORKSPACEROOT")

    def test_trailing_separator_yields_no_phantom_member(self):
        model, _, _, _ = _resolve(CFG_FIXTURES / "good")
        members = [m for m in model.path_members if m.variable == "MS_CELLLIST"]
        assert len(members) == 1
        assert members[0].exists

    def test_if_and_ifndef_blocks_are_applied(self):
        model, _, _, _ = _resolve(CFG_FIXTURES / "good")
        assert model.get("MS_SEEDFILES")
        assert model.get("MS_PENTABLE")

    def test_definition_history_records_provenance(self):
        model, _, _, _ = _resolve(CFG_FIXTURES / "good")
        var = model.variables["_USTN_WORKSPACEROOT"]
        assert len(var.history) >= 2
        skipped = [d for d in var.history if not d.applied]
        assert skipped and "already defined" in skipped[0].note

    def test_comment_stripping_keeps_hash_inside_values(self):
        assert _strip_comment("VAR = a#b # trailing") == "VAR = a#b"
        assert _strip_comment("# whole line") == ""

    def test_apostrophe_in_path_does_not_swallow_the_comment(self):
        # An unmatched apostrophe used to open a quote that never closed, so the
        # comment stayed in the value and the path was reported dead.
        line = r"MS_DEF = C:\Users\It's Here\Standards\   # the share"
        assert _strip_comment(line) == "MS_DEF = C:\\Users\\It's Here\\Standards\\"
        # A genuinely quoted value still protects its hash.
        assert _strip_comment('MS_TAG = "C1#FF00" # colour') == 'MS_TAG = "C1#FF00"'

    def test_include_level_clause_is_not_part_of_the_filespec(self):
        assert _split_include(r"$(_USTN_WORKSPACEROOT)*.cfg level WorkSpace") == (
            r"$(_USTN_WORKSPACEROOT)*.cfg",
            "WorkSpace",
        )
        assert _split_include('"standards.cfg"') == ("standards.cfg", "")
        # "level" only counts as the clause when it trails the whole directive.
        assert _split_include(r"cfg\level\shared.cfg") == (r"cfg\level\shared.cfg", "")

    def test_tokenizer_handles_operators(self):
        assert _tokenize("defined(X) && !defined(Y)") == ["defined(X)", "&&", "!", "defined(Y)"]


class TestGoodWorkspace:
    def test_all_config_checks_pass(self):
        _, _, _, checks = _resolve(CFG_FIXTURES / "good")
        failures = {n: c.result for n, c in checks.items() if c.severity is Severity.FAIL}
        assert failures == {}

    def test_required_roles_are_satisfied(self):
        _, roles, _, _ = _resolve(CFG_FIXTURES / "good")
        assert all(r.satisfied for r in roles.values() if r.required)

    def test_export_is_enabled(self):
        model, _, _, checks = _resolve(CFG_FIXTURES / "good")
        assert export_enabled(model)[0] is True
        assert checks["export_enablement"].severity is Severity.PASS


class TestBadWorkspace:
    def test_dead_path_is_detected(self):
        _, _, _, checks = _resolve(CFG_FIXTURES / "bad")
        assert checks["dead_paths"].severity is Severity.FAIL
        assert any("X:\\Standards" in e for e in checks["dead_paths"].evidence)

    def test_mapped_drive_leakage_is_flagged(self):
        _, _, _, checks = _resolve(CFG_FIXTURES / "bad")
        assert checks["mapped_drive_paths"].severity is Severity.WARN

    def test_machine_local_path_is_flagged(self):
        _, _, _, checks = _resolve(CFG_FIXTURES / "bad")
        assert checks["machine_local_paths"].severity is Severity.WARN

    def test_include_cycle_is_caught_not_hung(self):
        model, _, _, checks = _resolve(CFG_FIXTURES / "bad")
        assert model.include_cycles
        assert model.missing_includes
        assert checks["include_graph"].severity is Severity.FAIL

    def test_user_level_overrides_organization(self):
        model, _, _, _ = _resolve(CFG_FIXTURES / "bad")
        var = model.variables["_CIVIL_STANDARDS_IMPORTEXPORT"]
        assert var.value == "0"
        assert var.level == "User"

    def test_conflicting_definitions_reported_with_winner_and_loser(self):
        _, _, _, checks = _resolve(CFG_FIXTURES / "bad")
        check = checks["conflicting_definitions"]
        assert check.severity is Severity.WARN
        assert any("_CIVIL_STANDARDS_IMPORTEXPORT" in e for e in check.evidence)

    def test_workset_copy_shadows_organization_library(self):
        _, _, dgnlibs, checks = _resolve(CFG_FIXTURES / "bad")
        shadowed = [d for d in dgnlibs if d.shadowed_by]
        assert shadowed
        assert checks["duplicate_dgnlib_basenames"].severity is Severity.WARN
        assert checks["workset_shadowing"].severity is Severity.WARN

    def test_export_disabled_is_flagged(self):
        _, _, _, checks = _resolve(CFG_FIXTURES / "bad")
        assert checks["export_enablement"].severity is Severity.WARN


class TestOperators:
    """List position is priority: ORD searches semicolon lists left to right."""

    def _resolve_text(self, tmp_path: Path, text: str):
        cfg = tmp_path / "ops.cfg"
        cfg.write_text(text, encoding="utf-8")
        return CfgResolver(load_settings()).process([str(cfg)])

    def test_equals_overwrites_last_one_wins(self, tmp_path):
        model = self._resolve_text(tmp_path, "MS_DEF = first\nMS_DEF = second\n")
        assert model.get("MS_DEF") == "second"

    def test_colon_only_sets_when_undefined(self, tmp_path):
        model = self._resolve_text(tmp_path, "MS_DEF = kept\nMS_DEF : ignored\n")
        assert model.get("MS_DEF") == "kept"

    def test_colon_does_set_when_undefined(self, tmp_path):
        model = self._resolve_text(tmp_path, "MS_DEF : default\n")
        assert model.get("MS_DEF") == "default"

    def test_less_than_prepends_for_higher_priority(self, tmp_path):
        model = self._resolve_text(
            tmp_path, "MS_CELLLIST = base\\\nMS_CELLLIST < priority\\\n"
        )
        assert model.get("MS_CELLLIST").split(";") == ["priority\\", "base\\"]

    def test_greater_than_appends_as_fallback(self, tmp_path):
        model = self._resolve_text(
            tmp_path, "MS_CELLLIST = base\\\nMS_CELLLIST > fallback\\\n"
        )
        assert model.get("MS_CELLLIST").split(";") == ["base\\", "fallback\\"]

    def test_prepend_then_append_keeps_search_order(self, tmp_path):
        model = self._resolve_text(
            tmp_path,
            "MS_DGNLIBLIST = org\\\nMS_DGNLIBLIST > tail\\\nMS_DGNLIBLIST < head\\\n",
        )
        assert model.get("MS_DGNLIBLIST").split(";") == ["head\\", "org\\", "tail\\"]

    def test_first_listed_library_wins_over_a_later_duplicate(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "levels.dgnlib").write_text("first")
        (tmp_path / "b" / "levels.dgnlib").write_text("second")
        cfg = tmp_path / "ops.cfg"
        cfg.write_text(
            f"MS_DGNLIBLIST = {tmp_path}\\a\\levels.dgnlib\n"
            f"MS_DGNLIBLIST > {tmp_path}\\b\\levels.dgnlib\n",
            encoding="utf-8",
        )
        settings = load_settings()
        model = CfgResolver(settings).process([str(cfg)])
        dgnlibs = collect_dgnlibs(model, None, settings)
        winner = [d for d in dgnlibs if not d.shadowed_by]
        loser = [d for d in dgnlibs if d.shadowed_by]
        assert len(winner) == 1 and len(loser) == 1
        assert winner[0].path.endswith("a\\levels.dgnlib")
        assert loser[0].path.endswith("b\\levels.dgnlib")


class TestUnresolvedVariables:
    """A partial delivery references roots defined outside it; that is not a dead path."""

    def _resolve_text(self, tmp_path: Path, text: str):
        cfg = tmp_path / "partial.cfg"
        cfg.write_text(text, encoding="utf-8")
        settings = load_settings()
        model = CfgResolver(settings).process([str(cfg)])
        roles = role_coverage(model, settings)
        dgnlibs = collect_dgnlibs(model, None, settings, roles)
        checks = {c.name: c for c in run_config_checks(model, roles, dgnlibs, None, settings)}
        return model, roles, checks

    def test_undefined_reference_is_recorded(self, tmp_path):
        model, _, _ = self._resolve_text(
            tmp_path, "MS_DGNLIBLIST > $(_USTN_WORKSETROOT)dgnlib\\\n"
        )
        assert model.variables["MS_DGNLIBLIST"].unresolved == ["_USTN_WORKSETROOT"]

    def test_truncated_path_is_not_reported_as_dead(self, tmp_path):
        _, _, checks = self._resolve_text(
            tmp_path, "MS_DGNLIBLIST > $(_USTN_WORKSETROOT)dgnlib\\\n"
        )
        assert checks["dead_paths"].severity is Severity.PASS
        assert checks["unresolved_variables"].severity is Severity.WARN
        assert "_USTN_WORKSETROOT" in " ".join(checks["unresolved_variables"].evidence)

    def test_role_coverage_is_unproven_not_failed(self, tmp_path):
        _, roles, checks = self._resolve_text(
            tmp_path, "MS_DGNLIBLIST > $(_USTN_WORKSETROOT)dgnlib\\\n"
        )
        assert roles["levels"].incomplete
        assert checks["role_coverage"].severity is Severity.WARN

    def test_a_genuinely_missing_path_still_fails(self, tmp_path):
        _, _, checks = self._resolve_text(
            tmp_path, "MS_DGNLIBLIST > C:\\Definitely\\Not\\Here\\x.dgnlib\n"
        )
        assert checks["dead_paths"].severity is Severity.FAIL
        assert checks["unresolved_variables"].severity is Severity.PASS

    def test_seeding_the_root_resolves_it(self, tmp_path):
        (tmp_path / "dgnlib").mkdir()
        (tmp_path / "dgnlib" / "levels.dgnlib").write_text("x")
        cfg = tmp_path / "partial.cfg"
        cfg.write_text("MS_DGNLIBLIST > $(_USTN_WORKSETROOT)dgnlib\\\n", encoding="utf-8")
        settings = load_settings()
        model = CfgResolver(
            settings, seed_env={"_USTN_WORKSETROOT": f"{tmp_path}\\"}
        ).process([str(cfg)])
        roles = role_coverage(model, settings)
        assert roles["levels"].satisfied

    def test_partial_delivery_does_not_fail_role_coverage(self, tmp_path):
        """A folder whose parent config lives elsewhere must not read as catastrophic."""
        _, _, checks = self._resolve_text(tmp_path, "SOME_APP_SETTING = 1\n")
        assert checks["role_coverage"].severity is Severity.WARN
        assert "unproven" in checks["role_coverage"].detail
        assert checks["unreferenced_dgnlib"].severity is Severity.NOT_EVALUATED

    def test_complete_config_still_fails_a_genuinely_unwired_role(self, tmp_path):
        _, _, checks = self._resolve_text(
            tmp_path, f"_USTN_WORKSPACEROOT = {tmp_path}\\\nSOME_APP_SETTING = 1\n"
        )
        assert checks["role_coverage"].severity is Severity.FAIL


class TestWildcardInclude:
    """Bentley documents "%include <filespec> [level <Level>]" with wildcards, e.g.
    "%include $(_USTN_WORKSPACEROOT)*.cfg level WorkSpace"."""

    @staticmethod
    def _build(root: Path, directive: str) -> tuple:
        inc = root / "Includes"
        inc.mkdir(parents=True, exist_ok=True)
        (inc / "a.cfg").write_text("MS_A = 1\n", encoding="utf-8")
        (inc / "b.cfg").write_text("MS_B = 2\n", encoding="utf-8")
        entry = root / "org.cfg"
        entry.write_text(directive, encoding="utf-8")
        settings = load_settings()
        resolver = CfgResolver(settings)
        return resolver.process([entry]), settings

    def test_wildcard_include_loads_every_match(self, tmp_path):
        model, _ = self._build(tmp_path, "%include Includes/*.cfg\n")
        assert model.get("MS_A") == "1"
        assert model.get("MS_B") == "2"
        assert model.missing_includes == []

    def test_trailing_level_clause_is_stripped_and_applied(self, tmp_path):
        model, _ = self._build(tmp_path, "%include Includes/*.cfg level WorkSet\n")
        assert model.get("MS_A") == "1"
        assert model.missing_includes == []
        assert model.variables["MS_A"].level == "WorkSet"

    def test_pattern_matching_nothing_is_still_reported(self, tmp_path):
        model, _ = self._build(tmp_path, "%include Includes/*.absent\n")
        assert len(model.missing_includes) == 1
        assert model.missing_includes[0].endswith("*.absent")


class TestCrawl:
    def test_finds_standards_artifacts(self):
        settings = load_settings()
        tree = crawl(CFG_FIXTURES / "good", settings)
        assert len(tree.dgnlibs) == 1
        assert len(tree.cell_libs) == 1
        assert len(tree.seeds) == 1
        assert len(tree.cfg_files) == 2

    def test_included_files_are_not_entry_points(self):
        settings = load_settings()
        tree = crawl(CFG_FIXTURES / "good", settings)
        entries = [Path(p).name for p in pick_cfg_entry_points(tree)]
        assert entries == ["org.cfg"]

    def test_inventory_hashes_standards_files_only(self):
        settings = load_settings()
        tree = crawl(CFG_FIXTURES / "good", settings)
        hashed = [i for i in tree.inventory if i.sha1]
        assert hashed and all(Path(i.path).suffix != ".dgn" for i in hashed)


class TestNoConfig:
    def test_missing_config_is_not_evaluated_not_failed(self):
        settings = load_settings()
        checks = run_config_checks(None, None, [], None, settings)
        assert checks and all(c.severity is Severity.NOT_EVALUATED for c in checks)
