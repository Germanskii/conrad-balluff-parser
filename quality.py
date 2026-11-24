"""Revisit three exported cards and capture only public product areas for review."""

import json
from pathlib import Path

from collector import navigate
from data import DataError, read_csv, utc_now, validate_rows
from project_browser import (
    LOCAL,
    attached_browser,
    close_account_menu,
    require_profile_proof,
    verify_account_menu,
)


def review(browser: str):
    rows = read_csv(Path("output/balluff.csv"))
    validate_rows(rows, 100)
    reports = []
    root = LOCAL / "review"
    root.mkdir(exist_ok=True)
    with attached_browser(browser) as (_, context, manifest):
        require_profile_proof(browser, manifest)
        page = next(
            p for p in context.pages if p.url.startswith("https://www.conrad.de/")
        )
        for index in [0, 49, 99]:
            row = rows[index]
            navigate(page, row["url"])
            verify_account_menu(page)
            close_account_menu(page)
            selected = json.loads(row["selected_offer_json"])
            actual = {
                "title": page.locator("#ProductTitle").inner_text(),
                "ean": page.locator("#eanCode")
                .inner_text()
                .replace("EAN:", "", 1)
                .strip(),
                "manufacturer_part_number": page.locator("#manufacturerCode")
                .inner_text()
                .replace("Hst.-Teile-Nr.:", "", 1)
                .strip(),
                "total_price_raw": page.locator("#productPriceUnitPrice")
                .inner_text()
                .strip(),
                "vat_raw": page.locator("#productPriceTaxInformation")
                .inner_text()
                .strip(),
                "technical_rows": page.locator(
                    "#technicalDetails dl.productTechData__list"
                ).count(),
            }
            expected = {
                "title": row["title"],
                "ean": row["ean"],
                "manufacturer_part_number": row["manufacturer_part_number"],
                "total_price_raw": selected["total_price_raw"],
                "vat_raw": selected["vat_raw"],
                "technical_rows": len(json.loads(row["technical_specs_json"])),
            }
            checks = {key: actual[key] == expected[key] for key in expected}
            if not all(checks.values()):
                raise DataError(
                    f"Live sample differs from exported data: {row['product_id']}, {checks}"
                )
            for suffix, selector in [
                ("product", ".productDetails__productDescription"),
                ("offer", "#buybox"),
            ]:
                page.locator(selector).screenshot(
                    path=str(root / f"{row['product_id']}-{suffix}.png"), timeout=20000
                )
            reports.append(
                {
                    "row_number": index + 1,
                    "product_id": row["product_id"],
                    "url": row["url"],
                    "observed": actual,
                    "checks": checks,
                }
            )
            print(
                f"Live review passed: row {index + 1}, product {row['product_id']}",
                flush=True,
            )
    result = {
        "reviewed_at": utc_now(),
        "method": "Fresh authenticated DOM comparison with exported CSV",
        "samples": reports,
        "visual_review": "pending review of local product-only screenshots",
        "limitations": "Excluded marketplace offers are retained from embedded product state; their unavailable B2B panel cannot be visually verified in this account.",
    }
    Path("output/quality-checks.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
