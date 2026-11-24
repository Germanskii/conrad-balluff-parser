"""Small parsing primitives. These are not a verified Conrad DOM adapter."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from data import DataError


def cell_text(cell: Tag) -> str:
    """Keep explicit line breaks while joining inline text nodes naturally."""
    clone = BeautifulSoup(str(cell), "html.parser")
    for br in clone.find_all("br"):
        br.replace_with("\n")
    text = clone.get_text(" ", strip=False).replace("\xa0", " ")
    return "\n".join(
        re.sub(r"[^\S\n]+", " ", line).strip() for line in text.splitlines()
    ).strip()


def key_value_table(table_html: str) -> list[dict[str, str]]:
    """Read a supplied two-column table; do not silently flatten merged cells."""
    soup = BeautifulSoup(table_html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise DataError("Expected a table, not a missing technical-data section")
    pairs = []
    for row in table.find_all("tr"):
        if row.find_parent("table") != table:
            continue
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        if all(c.name == "th" for c in cells):
            continue
        if len(cells) != 2 or any(
            c.get("colspan", "1") != "1" or c.get("rowspan", "1") != "1" for c in cells
        ):
            raise DataError(
                "Unsupported technical-data row; inspect it instead of losing cells"
            )
        name, value = map(cell_text, cells)
        if not name or not value:
            raise DataError("An empty technical-data cell needs inspection")
        pairs.append({"name": name, "value": value})
    if not pairs:
        raise DataError("Technical-data table contains no readable pairs")
    return pairs


def labeled_identifier(text: str, label: str, pattern: str) -> str:
    """Extract one explicitly labeled value; preserve leading zeros as text."""
    matches = re.findall(re.escape(label) + r"\s*(" + pattern + r")(?=\s|$)", text)
    unique = list(dict.fromkeys(matches))
    if len(unique) != 1:
        raise DataError(
            f"Expected one unambiguous value after {label!r}, found {len(unique)}"
        )
    return unique[0]


def identifiers(text: str) -> dict[str, str]:
    """Labels were observed in the assignment and the public example page."""
    return {
        "ean": labeled_identifier(text, "EAN:", r"\d{8,14}"),
        "manufacturer_part_number": labeled_identifier(
            text, "Hst.-Teile-Nr.:", r"[^\s]+"
        ),
        "conrad_order_number": labeled_identifier(
            text, "Bestell-Nr.:", r"\d+(?:\s*-\s*\d+)?"
        ),
    }
