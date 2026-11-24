"""Sequential browser collection with positive auth checks and durable progress."""

from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout

from browser_session import (
    EXAMPLE_URL,
    AuthenticationRequired,
    SecurityChallenge,
    check_security,
)
from data import Checkpoint, DataError, product_identity, utc_now, write_csv
from project_browser import (
    LOCAL,
    attached_browser,
    click_visible,
    require_profile_proof,
)
from site_adapter import read_product

# Observed in the manufacturer-logo link on the authenticated example card.
BRAND_URL = "https://www.conrad.de/de/marken/balluff.html"
LINKS = LOCAL / "links.json"


class LoadError(RuntimeError):
    pass


def navigate(page, url: str, retries: int = 2):
    for attempt in range(retries + 1):
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            check_security(page)
            status = response.status if response else None
            if status == 429:
                raise SecurityChallenge(
                    "HTTP 429: collection stopped to avoid further load; retry later manually"
                )
            if status == 403:
                raise SecurityChallenge(
                    "HTTP 403: stop and inspect the site manually; no bypass is attempted"
                )
            if status is None or status >= 500:
                raise LoadError(f"Transient HTTP status {status}")
            if status >= 400:
                raise DataError(f"Product/page HTTP status {status}")
            return
        except (BrowserTimeout, LoadError):
            if attempt == retries:
                raise LoadError(
                    "Page did not load after three bounded attempts"
                ) from None
            time.sleep(3 * (attempt + 1))


def wait_listing(page, expected: int, previous_ids=None, attempts: int = 60):
    """Require the expected page plus a stable changed set of product IDs."""
    previous_ids = previous_ids or set()
    last_ids = None
    for attempt in range(attempts):
        check_security(page)
        snapshot = page.evaluate("""() => ({
            number:document.querySelector('nav.pagination [aria-current=page]')?.getAttribute('data-page'),
            links:[...document.querySelectorAll('a[href*="/de/p/"]')].map(e=>e.href)
        })""")
        links = {}
        for link in snapshot["links"]:
            identity, canonical = product_identity(link)
            links[identity] = canonical
        ids = set(links)
        if (
            snapshot["number"] == str(expected)
            and ids
            and (not previous_ids or ids - previous_ids)
        ):
            if ids == last_ids:
                return links
            last_ids = ids
        else:
            last_ids = None
        if attempt + 1 < attempts:
            page.wait_for_timeout(250)
    raise DataError(
        f"Listing page {expected}: expected number and stable new product IDs were not observed"
    )


def discover(browser: str, pages: int = 2) -> list[dict]:
    if not 1 <= pages <= 5:
        raise DataError("Discovery is limited to 1-5 pages per invocation")
    found, visited, page_results = {}, [], []
    stage = "attach browser"

    def save(complete, error=None):
        LINKS.parent.mkdir(parents=True, exist_ok=True)
        temporary = LINKS.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "discovered_at": utc_now(),
                    "complete": complete,
                    "requested_pages": pages,
                    "completed_pages": len(visited),
                    "failure_stage": error,
                    "source_pages": visited,
                    "page_results": page_results,
                    "links": list(found.values()),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(LINKS)

    try:
        with attached_browser(browser) as (_, context, manifest):
            require_profile_proof(browser, manifest)
            page = next(
                (
                    p
                    for p in context.pages
                    if p.url.startswith("https://www.conrad.de/")
                ),
                None,
            )
            if page is None:
                raise DataError("Open a Conrad page in the project browser first")
            stage = "load brand listing"
            print("Discover: " + stage, flush=True)
            navigate(page, BRAND_URL)
            previous_ids = set()
            for index in range(pages):
                stage = f"page {index + 1}: wait for page number and new cards"
                print("Discover: " + stage, flush=True)
                links = wait_listing(page, index + 1, previous_ids)
                source = page.url
                new_ids = set(links) - set(found)
                visited.append(source)
                for identity, canonical in links.items():
                    found.setdefault(
                        identity,
                        {
                            "product_id": identity,
                            "url": canonical,
                            "listing_url": source,
                        },
                    )
                page_results.append(
                    {
                        "page": index + 1,
                        "product_ids": sorted(links),
                        "new_unique_products": len(new_ids),
                    }
                )
                save(index + 1 == pages)
                print(
                    f"Listing page {index + 1}: {len(links)} IDs, {len(new_ids)} new; {len(found)} unique total",
                    flush=True,
                )
                if index + 1 == pages:
                    break
                previous_ids = set(links)
                stage = f"page {index + 1}: find next control"
                print("Discover: " + stage, flush=True)
                control = page.get_by_role("link", name="nächste", exact=True)
                control.wait_for(state="visible", timeout=15000)
                stage = f"page {index + 1}: wait for handler and activate next control"
                print("Discover: " + stage, flush=True)
                click_visible(page, control, wait_handler=True)
    except (Exception, KeyboardInterrupt) as exc:
        save(False, stage)
        print(
            f"Discover incomplete: {len(visited)}/{pages} pages, {len(found)} IDs saved; stage: {stage}; error: {type(exc).__name__}",
            flush=True,
        )
        if isinstance(
            exc, (SecurityChallenge, AuthenticationRequired, KeyboardInterrupt)
        ):
            raise
        raise DataError(
            f"Discovery stopped at {stage}; incomplete links saved ({len(visited)}/{pages} pages)"
        ) from None
    print(
        f"Saved {len(found)} candidate links; completed {len(visited)}/{pages} pages. Brand is checked per card.",
        flush=True,
    )
    return list(found.values())


def one(browser: str, url: str = EXAMPLE_URL):
    _, canonical = product_identity(url)
    with attached_browser(browser) as (_, context, manifest):
        require_profile_proof(browser, manifest)
        page = next(
            p for p in context.pages if p.url.startswith("https://www.conrad.de/")
        )
        navigate(page, canonical)
        row = read_product(page)
        checkpoint = Checkpoint(LOCAL / "progress.sqlite3")
        try:
            checkpoint.save(row)
        finally:
            checkpoint.close()
        write_csv(Path("output/one-product.csv"), [row], expected=1)
        Path("output/one-product.json").write_text(
            json.dumps(
                {
                    key: json.loads(value) if key.endswith("_json") else value
                    for key, value in row.items()
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            f"Saved real product {row['product_id']} to output/one-product.csv and .json",
            flush=True,
        )


def collect(browser: str, count: int = 100, delay: float = 2.0):
    if count < 1 or count > 100 or delay < 2:
        raise DataError("Choose 1-100 products and a delay of at least 2 seconds")
    if not LINKS.is_file():
        raise DataError("Run discover first; no candidate URLs are invented")
    discovery = json.loads(LINKS.read_text(encoding="utf-8"))
    if discovery.get("complete") is False:
        raise DataError(
            "Discovery is marked incomplete. Rerun discover successfully before collection."
        )
    candidates = discovery["links"]
    checkpoint = Checkpoint(LOCAL / "progress.sqlite3")
    try:
        existing = checkpoint.rows()
        for row in existing:
            from data import validate_record

            validate_record(row)
        done = {row["product_id"] for row in existing}
        with attached_browser(browser) as (_, context, manifest):
            require_profile_proof(browser, manifest)
            page = next(
                p for p in context.pages if p.url.startswith("https://www.conrad.de/")
            )
            consecutive_errors = 0
            for candidate in candidates:
                if len(done) >= count:
                    break
                identity, url = product_identity(candidate["url"])
                if identity in done:
                    continue
                time.sleep(delay)
                try:
                    navigate(page, url)
                    row = read_product(page)
                    if row["product_id"] != identity:
                        raise DataError(
                            "Navigation redirected to a different product identifier"
                        )
                    checkpoint.save(row)
                    done.add(identity)
                    consecutive_errors = 0
                    print(f"Saved {len(done)}/{count}: {identity}", flush=True)
                except (AuthenticationRequired, SecurityChallenge):
                    checkpoint.failed(
                        identity,
                        "authentication_or_security",
                        "Stopped; manual attention required",
                    )
                    raise
                except (LoadError, BrowserTimeout):
                    consecutive_errors += 1
                    checkpoint.failed(
                        identity,
                        "load_error",
                        "Timed out after bounded page loading/extraction",
                    )
                    print(f"Skipped {identity}: load_error", flush=True)
                except (DataError, KeyError, TypeError, ValueError) as exc:
                    consecutive_errors += 1
                    # DataError messages are authored locally; never persist raw network URLs/errors.
                    message = (
                        str(exc) if isinstance(exc, DataError) else type(exc).__name__
                    )
                    checkpoint.failed(identity, "parse_error", message)
                    print(f"Skipped {identity}: parse_error: {message}", flush=True)
                if consecutive_errors >= 3:
                    raise DataError(
                        "Three consecutive extraction failures; stop and inspect one card before continuing"
                    )
            rows = checkpoint.rows()[:count]
            output = (
                Path("output/balluff.csv")
                if count == 100
                else Path(f"output/balluff-{count}.csv")
            )
            report = write_csv(output, rows, expected=count)
            report.update(
                account_context="Visible Abmelden checked for each newly collected card",
                browser="persistent installed " + browser,
                restricted_offers="Retained from product payload with exclusion_status; not substituted for selected offer",
            )
            Path("output/validation.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                f"Exported and re-read {len(rows)} unique products: {output}",
                flush=True,
            )
    finally:
        checkpoint.close()
