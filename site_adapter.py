"""Conrad product extraction from observed DOM and product-scoped Nuxt data."""

from __future__ import annotations

import re
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

from browser_session import check_security, logout_is_visible
from data import (
    COLUMNS,
    REQUIRED_FIELDS,
    DataError,
    german_amount,
    json_text,
    product_identity,
    utc_now,
    validate_record,
)
from nuxt_data import ProductPayload
from project_browser import close_account_menu, verify_account_menu


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def public_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.hostname not in {
        "www.conrad.de",
        "asset.conrad.com",
    }:
        raise DataError("Unexpected product asset/link origin")
    if parts.username or parts.password or parts.query:
        raise DataError(
            "Product links with credentials or unreviewed query parameters are not exported"
        )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def price_fields(price: dict) -> dict:
    """Keep source field names and tax context; never recalculate net/gross prices."""
    monetary = {
        "unitPrice",
        "totalPrice",
        "totalPriceWithStandardPrecision",
        "normalPrecisionUnitPrice",
        "unitPricePerPiece",
        "minShippingCosts",
        "netMinShippingCosts",
        "crossedOutUnitPrice",
        "crossedOutPriceTotal",
        "crossedOutUnitPriceDifference",
        "pricePerUnit",
        "totalAmount",
        "savedAmount",
        "normalPrecisionPricePerUnit",
        "grossUnitPrice",
        "grossTotalPrice",
        "netUnitPrice",
        "netTotalPrice",
    }
    result = {}
    allowed = monetary | {
        "currency",
        "vatPercentage",
        "taxSchema",
        "freeShipping",
        "priceScales",
        "imprecisePrice",
        "normalPrecisionUnitPriceDivisor",
        "normalPrecisionPricePerUnitDivisor",
        "scaleFrom",
        "savedPercentage",
        "crossedOutUnitPriceDifferencePercentage",
    }
    for key, value in price.items():
        if key not in allowed:
            continue
        if key in monetary and value is not None:
            result[key] = format(Decimal(str(value)), "f")
        elif key == "imprecisePrice":
            result[key] = price_fields(value)
        elif key == "priceScales":
            result[key] = [price_fields(row) for row in value]
        else:
            result[key] = value
    return result


def public_offer(offer: dict, product_id: str, quantity: str) -> dict:
    if str(offer.get("productId", "")).lstrip("0") != product_id.lstrip("0"):
        raise DataError("An offer belongs to a different product")
    return {
        "source": "embedded_product_state",
        "offer_id": str(offer["offerId"]),
        "product_number": str(offer["productId"]),
        "seller_id": str(offer["sellerId"]),
        "seller": offer["sellerName"],
        "shipper": None,
        "party_note": "sellerName from source; no separate shipper label is exposed for this offer",
        "exclusion_status": offer["exclusionStatus"],
        "quantity": quantity,
        "minimum_order_quantity": offer.get("minimumOrderQuantity"),
        "multiplier": offer.get("multiplier"),
        "price": price_fields(offer["price"]),
        "availability": {
            key: offer.get("availability", {}).get(key)
            for key in [
                "stockQuantity",
                "availabilityState",
                "promisedDeliveryDate",
                "isShowStock",
                "notAvailableInRequestedQuantity",
            ]
        },
    }


def read_consistent_offer_state(page, product_id: str, attempts: int = 40):
    """Read only public product Pinia fields; wait at most 40 bounded polls."""
    last = {}
    for attempt in range(attempts):
        check_security(page)
        snapshot = page.evaluate(
            """id => {
            const app = (document.querySelector('#__nuxt') || document.querySelector('[data-v-app]'))?.__vue_app__;
            const nuxt = app?.$nuxt;
            const state = nuxt?.$pinia?.state?.value?.['productOffers:' + id];
            const priceKeys = ['unitPrice','totalPrice','totalPriceWithStandardPrecision',
                'normalPrecisionUnitPrice','unitPricePerPiece','minShippingCosts','netMinShippingCosts',
                'crossedOutUnitPrice','crossedOutPriceTotal','crossedOutUnitPriceDifference',
                'pricePerUnit','totalAmount','savedAmount','normalPrecisionPricePerUnit',
                'grossUnitPrice','grossTotalPrice','netUnitPrice','netTotalPrice','currency',
                'vatPercentage','taxSchema','freeShipping','normalPrecisionUnitPriceDivisor',
                'normalPrecisionPricePerUnitDivisor','scaleFrom','savedPercentage',
                'crossedOutUnitPriceDifferencePercentage'];
            const price = p => p ? Object.assign(Object.fromEntries(priceKeys.filter(k => k in p).map(k => [k,p[k]])),
                p.priceScales ? {priceScales: p.priceScales.map(price)} : {},
                p.imprecisePrice ? {imprecisePrice: price(p.imprecisePrice)} : {}) : null;
            const offer = o => o ? {
                productId:o.productId, offerId:o.offerId, sellerId:o.sellerId, sellerName:o.sellerName,
                exclusionStatus:o.exclusionStatus, minimumOrderQuantity:o.minimumOrderQuantity,
                multiplier:o.multiplier, price:price(o.price), availability: Object.fromEntries(
                    ['stockQuantity','availabilityState','promisedDeliveryDate','isShowStock',
                     'notAvailableInRequestedQuantity'].filter(k => k in (o.availability || {})).map(k => [k,o.availability[k]]))
            } : null;
            return {
                quantity: document.querySelector('#productUnitsInputPriceAvailabilityHierarchy')?.value ?? null,
                total: document.querySelector('#productPriceUnitPrice')?.innerText?.trim() ?? '',
                tax: document.querySelector('#productPriceTaxInformation')?.innerText?.trim() ?? '',
                state: state ? {
                    quantities: {quantityAtLastTotalPrice:state.quantities?.quantityAtLastTotalPrice ?? null},
                    currentOffer:offer(state.currentOffer),
                    collections:{offers:(state.collections?.offers || []).map(offer)}
                } : null
            };
        }""",
            product_id,
        )
        state = snapshot["state"]
        quantity = snapshot["quantity"]
        embedded = state["quantities"]["quantityAtLastTotalPrice"] if state else None
        last = {
            "dom_quantity": quantity,
            "dom_type": type(quantity).__name__,
            "embedded_quantity": embedded,
            "embedded_type": type(embedded).__name__,
        }
        current = state.get("currentOffer") if state else None
        valid_quantity = (
            isinstance(quantity, str)
            and quantity.isdigit()
            and int(quantity) > 0
            and type(embedded) is int
            and int(quantity) == embedded
        )
        if valid_quantity and current and current.get("price"):
            try:
                same_price = Decimal(german_amount(snapshot["total"])) == Decimal(
                    str(current["price"]["totalPrice"])
                )
            except (DataError, ValueError, ArithmeticError):
                same_price = False
            schema = current["price"].get("taxSchema")
            tax = snapshot.get("tax", "")
            same_tax = (schema == "GROSS" and "inkl. MwSt." in tax) or (
                schema == "NET" and "zzgl." in tax
            )
            if same_price and same_tax:
                return state, quantity, snapshot["total"]
        if attempt + 1 < attempts:
            page.wait_for_timeout(250)
    raise DataError(
        "Selected quantity and live price context did not agree after bounded waiting: "
        + json_text(last)
    )


def read_product(page) -> dict:
    product_id, url = product_identity(page.url)
    page.locator("#ProductTitle").wait_for(state="visible", timeout=20000)
    page.locator("#productUnitsInputPriceAvailabilityHierarchy").wait_for(
        state="attached", timeout=20000
    )
    check_security(page)
    verify_account_menu(page)
    # Close only the account flyout so it cannot cover the public product.
    if logout_is_visible(page):
        close_account_menu(page)
    payload = ProductPayload(page.locator("#__NUXT_DATA__").text_content())
    details = payload.named(f"product:details:{product_id}")
    product = details["product"]
    if str(product["shortProductNumber"]) != product_id:
        raise DataError("URL and embedded product identity disagree")
    brand = product["brand"]["displayName"]
    logo_brand = page.locator(".productImageAndData__makerLogo img").get_attribute(
        "alt"
    )
    if brand.casefold() != "balluff" or (logo_brand or "").casefold() != "balluff":
        raise DataError(
            "Both the embedded brand and the displayed manufacturer logo must be Balluff"
        )
    row = {key: "" for key in COLUMNS}
    title = page.locator("#ProductTitle").inner_text()
    if clean_text(title) != clean_text(product["productShortInformation"]["title"]):
        raise DataError("DOM title and embedded product title disagree")
    ids = {}
    for key, selector, label in [
        ("ean", "#eanCode", "EAN:"),
        ("manufacturer_part_number", "#manufacturerCode", "Hst.-Teile-Nr.:"),
        ("conrad_order_number", "#productCode", "Bestell-Nr.:"),
    ]:
        element = page.locator(selector)
        ids[key] = (
            clean_text(element.inner_text().replace(label, "", 1))
            if element.count()
            else ""
        )
    if ids["ean"] and ids["ean"] != str(product["productFullInformation"]["eanCode"]):
        raise DataError("DOM and source EAN disagree")
    if ids["conrad_order_number"].split(" - ")[0] != product_id:
        raise DataError("Displayed Conrad number does not match the URL")
    row.update(
        product_id=product_id,
        url=url,
        brand=brand,
        title=title,
        collected_at=utc_now(),
        **ids,
    )
    breadcrumbs = page.locator(".productDetails__breadcrumbsContainer a").evaluate_all(
        "es=>es.map(e=>({name:e.innerText.trim(),url:e.href}))"
    )
    for crumb in breadcrumbs:
        crumb["url"] = public_url(crumb["url"])
    images = list(
        dict.fromkeys(
            public_url(image["imageUrl"]) for image in product["productImages"]
        )
    )
    overview = page.locator(
        ".productImageAndData__dataTechnical > div > div.flex.justify-between"
    ).evaluate_all(
        "es=>es.map(e=>({name:e.children[0].innerText.trim(),value:e.children[1].innerText.trim()}))"
    )
    if not overview and not page.locator(".productImageAndData__data").count():
        raise DataError(
            "No reviewed overview layout: absence cannot be established safely"
        )
    specs = page.locator("#technicalDetails dl.productTechData__list").evaluate_all(
        "es=>es.map(e=>({name:e.querySelector('dt').innerText.trim(),value:e.querySelector('.productTechData__definitionDescription').innerText.trim()}))"
    )
    source_specs = product["productFullInformation"]["technicalAttributes"]
    if len(specs) != len(source_specs) or [x["name"] for x in specs] != [
        x["attributeName"] for x in source_specs
    ]:
        raise DataError("The full technical table did not match the product payload")
    documents = [
        {
            "category": doc["typeName"],
            "title": doc["title"],
            "language": doc["language"],
            "url": public_url(doc["fullUrl"]),
        }
        for doc in product.get("productMedia", {}).get("manuals", [])
    ]
    datasheets = [doc for doc in documents if doc["category"] == "Datenblatt"]
    state, quantity, total_raw = read_consistent_offer_state(page, product_id)
    current = state["currentOffer"]
    if current["exclusionStatus"] != "INCLUDED":
        raise DataError("The selected offer is not available to this account")
    if Decimal(german_amount(total_raw)) != Decimal(
        str(current["price"]["totalPrice"])
    ):
        raise DataError(
            "Visible selected total differs from the embedded offer; refusing stale prices"
        )
    tax_raw = page.locator("#productPriceTaxInformation").inner_text().strip()
    schema = current["price"]["taxSchema"]
    if (schema == "GROSS" and "inkl. MwSt." not in tax_raw) or (
        schema == "NET" and "zzgl." not in tax_raw
    ):
        raise DataError("Visible VAT mode and embedded selected price disagree")
    seller_info = page.locator("#productSellerInfo").inner_text().strip()
    party_match = re.search(r"Verkauf und Versand durch\s+(.+)", seller_info)
    if not party_match:
        raise DataError(
            "Unreviewed seller/shipper layout; do not infer the party roles"
        )
    party = party_match[1].strip()
    if party != current["sellerName"]:
        raise DataError("Visible selected seller and source offer disagree")
    selected = public_offer(current, product_id, quantity)
    selected.update(
        source="visible_buybox_and_embedded_product_state",
        seller=party,
        shipper=party,
        party_note="Both seller and shipper are explicitly named in the visible combined label",
        seller_shipper_raw=party_match[0],
        quantity=quantity,
        total_price_raw=total_raw,
        unit_price_label_raw=page.locator('[data-e2e="total-price-label"]')
        .inner_text()
        .strip(),
        vat_raw=tax_raw,
        shipping_raw=tax_raw.split("&", 1)[-1].strip(),
        stock_raw=page.locator("#currentOfferAvailability").inner_text().strip()
        if page.locator("#currentOfferAvailability").count()
        else "",
        stock_display_status="observed"
        if page.locator("#currentOfferAvailability").count()
        else "absent",
        delivery_raw=page.locator("#fastTrackDelivery").inner_text().strip()
        if page.locator("#fastTrackDelivery").count()
        else "",
        price_tiers_status="observed"
        if current["price"].get("priceScales")
        else "absent",
        price_tiers=price_fields(current["price"]).get("priceScales", []),
        offers_panel_visible=any(
            item.is_visible()
            for item in page.get_by_text("Alle Angebote", exact=True).all()
        ),
    )
    offers = [
        public_offer(offer, product_id, quantity)
        for offer in state["collections"]["offers"]
    ]
    if len({offer["offer_id"] for offer in offers}) != len(offers):
        raise DataError("Duplicate offer identifiers in the product payload")
    warnings = []
    if any(offer["exclusion_status"] != "INCLUDED" for offer in offers):
        warnings.append(
            "Some source offers are excluded for this account; they are retained with exclusion_status and are not the selected price."
        )

    def section_text(selector):
        element = page.locator(selector)
        return element.inner_text().strip() if element.count() else ""

    row.update(
        highlights=section_text("#descriptionTitle__productHighlights"),
        description=section_text(
            "#descriptionTitle__productDescription .productDescription"
        ),
        notes=section_text(
            "#productDescriptionWrapper #descriptionTitle__productNotes"
        ),
    )
    nested = {
        "breadcrumbs_json": breadcrumbs,
        "image_urls_json": images,
        "overview_json": overview,
        "technical_specs_json": specs,
        "documents_json": documents,
        "datasheet_json": datasheets,
        "selected_offer_json": selected,
        "offers_json": offers,
        "warnings_json": warnings,
    }
    row.update({key: json_text(value) for key, value in nested.items()})
    row["field_status_json"] = json_text(
        {
            key: "observed" if nested.get(key, row[key]) else "absent"
            for key in REQUIRED_FIELDS
        }
    )
    validate_record(row)
    return row
