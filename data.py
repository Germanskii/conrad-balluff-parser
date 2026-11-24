"""Lossless CSV serialization, validation, and resumable local checkpoints."""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

COLUMNS = [
    "product_id",
    "url",
    "brand",
    "title",
    "breadcrumbs_json",
    "image_urls_json",
    "overview_json",
    "datasheet_json",
    "ean",
    "manufacturer_part_number",
    "conrad_order_number",
    "selected_offer_json",
    "highlights",
    "technical_specs_json",
    "documents_json",
    "description",
    "notes",
    "offers_json",
    "collected_at",
    "field_status_json",
    "warnings_json",
]
JSON_COLUMNS = {
    "breadcrumbs_json": list,
    "image_urls_json": list,
    "overview_json": list,
    "datasheet_json": list,
    "selected_offer_json": dict,
    "technical_specs_json": list,
    "documents_json": list,
    "offers_json": list,
    "field_status_json": dict,
    "warnings_json": list,
}
REQUIRED_FIELDS = [
    c
    for c in COLUMNS
    if c
    not in {
        "product_id",
        "url",
        "brand",
        "collected_at",
        "field_status_json",
        "warnings_json",
    }
]
STATUSES = {"observed", "absent", "load_error", "parse_error"}
PRODUCT_PATH = re.compile(r"^/de/p/[^/]+-(\d+)\.html$")


class DataError(ValueError):
    """A record cannot safely be included in a deliverable."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def product_identity(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    match = PRODUCT_PATH.fullmatch(parts.path)
    if (
        parts.scheme != "https"
        or parts.hostname != "www.conrad.de"
        or not match
        or parts.port
    ):
        raise DataError(f"Not a Conrad Germany product URL: {url}")
    return match[1], urlunsplit(("https", "www.conrad.de", parts.path, "", ""))


def german_amount(raw: str) -> str:
    """Normalize an explicit German monetary amount, without inferring VAT."""
    cleaned = raw.replace("\xa0", " ").replace("\u202f", " ").strip()
    match = re.fullmatch(r"(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})\s*(?:€|EUR)?", cleaned)
    if not match:
        raise DataError(f"Ambiguous German monetary amount: {raw!r}")
    return str(Decimal(match[1].replace(".", "") + "." + match[2]))


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def validate_record(row: dict) -> None:
    if set(row) != set(COLUMNS):
        raise DataError(
            f"Unexpected schema: missing={set(COLUMNS) - set(row)}, extra={set(row) - set(COLUMNS)}"
        )
    if any(not isinstance(value, str) for value in row.values()):
        raise DataError("Every CSV cell must be a string; keep identifiers as text")
    identity, canonical = product_identity(row["url"])
    if identity != row["product_id"] or canonical != row["url"]:
        raise DataError("URL and product identifier disagree")
    if row["brand"].strip().casefold() != "balluff":
        raise DataError("Manufacturer is not verified as Balluff")
    parsed = {}
    for key, expected_type in JSON_COLUMNS.items():
        try:
            parsed[key] = json.loads(row[key])
        except (ValueError, TypeError) as exc:
            raise DataError(f"Invalid JSON in {key}") from exc
        if not isinstance(parsed[key], expected_type):
            raise DataError(f"Incorrect JSON type in {key}")
    statuses = parsed["field_status_json"]
    if set(statuses) != set(REQUIRED_FIELDS) or not set(statuses.values()) <= STATUSES:
        raise DataError("Every requested field needs an explicit supported status")
    failed = [
        key
        for key, status in statuses.items()
        if status in {"load_error", "parse_error"}
    ]
    if failed:
        raise DataError(f"Unresolved extraction errors: {failed}")
    for key, status in statuses.items():
        value = parsed.get(key, row[key])
        if (status == "absent") != (not bool(value)):
            raise DataError(f"Field value and status disagree: {key}")
    for key in [
        "title",
        "conrad_order_number",
        "technical_specs_json",
        "selected_offer_json",
    ]:
        if statuses[key] != "observed":
            raise DataError(f"Essential field is missing: {key}")
    offer = parsed["selected_offer_json"]
    if not offer.get("seller") or not offer.get("shipper") or not offer.get("vat_raw"):
        raise DataError(
            "Selected offer needs explicit seller, shipper, and VAT context"
        )
    if not parsed["offers_json"]:
        raise DataError("Missing structured offer data")
    try:
        datetime.fromisoformat(row["collected_at"])
    except ValueError as exc:
        raise DataError("Invalid collection timestamp") from exc


def validate_rows(rows: list[dict], expected: int = 100) -> dict:
    if len(rows) != expected:
        raise DataError(f"Expected {expected} products, found {len(rows)}")
    seen = set()
    for row in rows:
        validate_record(row)
        if row["product_id"] in seen:
            raise DataError(f"Duplicate product: {row['product_id']}")
        seen.add(row["product_id"])
    missing = {
        field: sum(json.loads(r["field_status_json"])[field] == "absent" for r in rows)
        for field in REQUIRED_FIELDS
    }
    return {
        "products": len(rows),
        "unique_products": len(seen),
        "brand": "Balluff",
        "missing_by_field": missing,
        "validated_at": utc_now(),
    }


def write_csv(path: Path, rows: list[dict], expected: int = 100) -> dict:
    report = validate_rows(rows, expected)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    # Re-read with the same public reader used by the validate command.
    restored = read_csv(temporary)
    if restored != rows:
        raise DataError("CSV round-trip changed the data")
    validate_rows(restored, expected)
    temporary.replace(path)
    return report


def read_csv(path: Path) -> list[dict]:
    csv.field_size_limit(10_000_000)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != COLUMNS:
            raise DataError("CSV columns do not match the documented schema")
        return list(reader)


class Checkpoint:
    """SQLite commits each completed product; failed pages never replace good rows."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                product_id TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS failures (
                product_id TEXT, category TEXT, message TEXT, recorded_at TEXT
            );
        """)

    def save(self, row: dict):
        validate_record(row)
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO products VALUES (?, ?)",
                (row["product_id"], json_text(row)),
            )

    def failed(self, product_id: str, category: str, message: str):
        with self.connection:
            self.connection.execute(
                "INSERT INTO failures VALUES (?, ?, ?, ?)",
                (product_id, category, message, utc_now()),
            )

    def rows(self) -> list[dict]:
        return [
            json.loads(r[0])
            for r in self.connection.execute(
                "SELECT payload FROM products ORDER BY rowid"
            )
        ]

    def close(self):
        self.connection.close()
