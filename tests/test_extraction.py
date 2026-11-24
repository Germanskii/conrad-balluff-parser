"""Synthetic HTML tests for the reusable parsing primitives only."""

import unittest

from data import DataError
from extraction import identifiers, key_value_table


class ExtractionTests(unittest.TestCase):
    def test_repeated_spec_names_and_inline_units_are_preserved(self):
        html = """<table><tr><th>Property</th><th>Value</th></tr>
            <tr><td>Abm.</td><td>Ø <span>12</span> mm<br>Höhe 65 mm</td></tr>
            <tr><td>Schutzart</td><td>IP68</td></tr>
            <tr><td>Schutzart</td><td>IP69K</td></tr></table>"""
        self.assertEqual(
            key_value_table(html),
            [
                {"name": "Abm.", "value": "Ø 12 mm\nHöhe 65 mm"},
                {"name": "Schutzart", "value": "IP68"},
                {"name": "Schutzart", "value": "IP69K"},
            ],
        )

    def test_missing_or_malformed_table_is_an_error_not_an_absent_value(self):
        for html in [
            "<div>Loading</div>",
            "<table></table>",
            '<table><tr><td colspan="2">Merged</td></tr></table>',
            "<table><tr><td>Type</td><td></td></tr></table>",
        ]:
            with self.assertRaises(DataError):
                key_value_table(html)

    def test_identifier_labels_preserve_zeroes_and_order_suffix(self):
        values = identifiers(
            "EAN: 0012345678901\nHst.-Teile-Nr.: TEST001\nBestell-Nr.: 0012345 - 62\n"
        )
        self.assertEqual(
            values,
            {
                "ean": "0012345678901",
                "manufacturer_part_number": "TEST001",
                "conrad_order_number": "0012345 - 62",
            },
        )

    def test_conflicting_identifier_is_not_silently_selected(self):
        with self.assertRaises(DataError):
            identifiers(
                "EAN: 0012345678901\nEAN: 0098765432109\nHst.-Teile-Nr.: TEST001\nBestell-Nr.: 0012345"
            )


if __name__ == "__main__":
    unittest.main()
