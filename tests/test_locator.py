import os

import pytest

from workspace_checker.extract.locator import (
    BentleyProduct,
    BentleyProductNotFound,
    describe_products,
    find_products,
    parse_version,
    select_product,
)

on_windows = pytest.mark.skipif(os.name != "nt", reason="Bentley products are Windows only")


class TestVersionParsing:
    def test_connect_edition_folder(self):
        assert parse_version("OpenRoads Designer CE 10.12") == ("10.12", (10, 12))

    def test_year_numbered_release(self):
        assert parse_version("OpenBridge Modeler 2024.00") == ("2024.00", (2024, 0))

    def test_bare_year(self):
        assert parse_version("MicroStation 2023") == ("2023", (2023,))

    def test_three_part_version(self):
        assert parse_version("OpenBridge Modeler CE 10.10.20") == ("10.10.20", (10, 10, 20))

    def test_no_version(self):
        assert parse_version("ProjectWise Drive") == ("", ())

    def test_year_sorts_above_connect_edition(self):
        assert parse_version("2024.00")[1] > parse_version("CE 10.12")[1]


class TestSelection:
    def _products(self):
        return [
            BentleyProduct("OpenRoads", "OpenRoads Designer CE 10.10", "10.10", "a.exe", "", (10, 10)),
            BentleyProduct("OpenRoads", "OpenRoads Designer 2024.00", "2024.00", "b.exe", "", (2024, 0)),
            BentleyProduct("MicroStation", "MicroStation 2023", "2023", "c.exe", "", (2023,)),
        ]

    def test_matches_by_name_fragment(self):
        product = self._products()[1]
        assert product.matches("2024.00")
        assert product.matches("openroads")
        assert product.matches("OpenRoads Designer 2024")
        assert not product.matches("openrail")

    def test_label_does_not_repeat_the_version(self):
        assert self._products()[1].label == "OpenRoads Designer 2024.00"


@on_windows
class TestInstalledProducts:
    def test_discovery_is_fast_and_cached(self):
        import time

        find_products(refresh=True)
        start = time.perf_counter()
        find_products()
        assert time.perf_counter() - start < 0.05

    def test_newest_version_of_a_family_comes_first(self):
        products = find_products()
        if not products:
            pytest.skip("No Bentley products installed")
        by_family: dict[str, list] = {}
        for product in products:
            by_family.setdefault(product.family, []).append(product)
        for family in by_family.values():
            keys = [p.version_key for p in family if p.version_key]
            assert keys == sorted(keys, reverse=True)

    def test_explicit_name_selects_that_product(self):
        products = find_products()
        if not products:
            pytest.skip("No Bentley products installed")
        wanted = products[-1]
        assert select_product(explicit=wanted.name).exe == wanted.exe

    def test_unknown_product_raises_with_actionable_guidance(self):
        with pytest.raises(BentleyProductNotFound) as exc:
            select_product(explicit="NotARealProduct 9999")
        assert "--product" in str(exc.value)
        assert "_CIVIL_STANDARDS_IMPORTEXPORT" in str(exc.value)

    def test_describe_lists_every_install(self):
        text = describe_products()
        assert "Bentley product" in text or text == "No Bentley products found."
