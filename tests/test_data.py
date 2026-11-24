"""Synthetic cases for CSV validation and checkpoint handling."""

import json
import tempfile
import unittest
from pathlib import Path

from data import (
    COLUMNS,
    REQUIRED_FIELDS,
    Checkpoint,
    DataError,
    german_amount,
    json_text,
    product_identity,
    read_csv,
    utc_now,
    validate_rows,
    write_csv,
)


def synthetic_record():
    row = {key: "" for key in COLUMNS}
    row.update(
        product_id="0012345",
        url="https://www.conrad.de/de/p/synthetic-0012345.html",
        brand="Balluff",
        title='Synthetic "Prüfung", Größe Ø 12',
        conrad_order_number="0012345",
        ean="0012345678901",
        collected_at=utc_now(),
        description='Synthetic only: "Maße"\nZweite Zeile, Weiß',
    )
    values = {
        "breadcrumbs_json": [],
        "image_urls_json": [],
        "overview_json": [],
        "datasheet_json": [],
        "selected_offer_json": {
            "seller": "Synthetic seller",
            "shipper": "Synthetic shipper",
            "vat_raw": "zzgl. MwSt.",
            "unit_price": "12.34",
        },
        "technical_specs_json": [
            {"name": "Maß", "value": "12 mm"},
            {"name": "Maß", "value": "13 mm"},
        ],
        "documents_json": [],
        "offers_json": [{"seller": "Synthetic seller"}],
        "warnings_json": [],
    }
    row.update({key: json_text(value) for key, value in values.items()})
    row["field_status_json"] = json_text(
        {
            key: "observed" if values.get(key, row[key]) else "absent"
            for key in REQUIRED_FIELDS
        }
    )
    return row


class DataTests(unittest.TestCase):
    def test_csv_preserves_unicode_multiline_quotes_ids_and_repeated_specs(self):
        row = synthetic_record()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.csv"
            write_csv(path, [row], expected=1)
            self.assertEqual(read_csv(path), [row])
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertEqual(
                json.loads(read_csv(path)[0]["technical_specs_json"])[1]["value"],
                "13 mm",
            )

    def test_identity_deduplicates_tracking_but_preserves_leading_zeros(self):
        self.assertEqual(
            product_identity(
                "https://www.conrad.de/de/p/synthetic-0012345.html?x=1#top"
            ),
            ("0012345", "https://www.conrad.de/de/p/synthetic-0012345.html"),
        )
        for url in [
            "https://example.com/de/p/x-123.html",
            "https://www.conrad.de/robots.txt",
        ]:
            with self.assertRaises(DataError):
                product_identity(url)

    def test_money_does_not_guess_decimal_or_vat(self):
        self.assertEqual(german_amount("1.234,56\xa0€"), "1234.56")
        self.assertEqual(german_amount("0,00 €"), "0.00")
        for amount in ["12.34", "ab 12,34 €", "12,34 - 13,45 €"]:
            with self.assertRaises(DataError):
                german_amount(amount)

    def test_count_brand_duplicate_and_unresolved_error_are_rejected(self):
        row = synthetic_record()
        for rows, count in [
            ([row], 100),
            ([row, row], 2),
            ([dict(row, brand="Other")], 1),
        ]:
            with self.assertRaises(DataError):
                validate_rows(rows, count)
        statuses = json.loads(row["field_status_json"])
        statuses["documents_json"] = "load_error"
        row["field_status_json"] = json_text(statuses)
        with self.assertRaises(DataError):
            validate_rows([row], 1)

    def test_checkpoint_survives_reopen_and_failed_page_keeps_success(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "progress.sqlite3"
            store = Checkpoint(path)
            row = synthetic_record()
            store.save(row)
            store.failed(row["product_id"], "load_error", "Synthetic timeout")
            store.close()
            store = Checkpoint(path)
            self.assertEqual(store.rows(), [row])
            store.close()


if __name__ == "__main__":
    unittest.main()
