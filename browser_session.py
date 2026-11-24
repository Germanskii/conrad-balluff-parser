"""Interactive login and honest verification of a restored browser session."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from data import DataError, json_text, utc_now

EXAMPLE_URL = (
    "https://www.conrad.de/de/p/balluff-induktiver-sensor-bes0068-1-st-3416652.html"
)
LOCAL = Path("local")
STATE = LOCAL / "storage_state.json"
PROOF = LOCAL / "session_verification.json"


class SecurityChallenge(RuntimeError):
    """The security check requires human action; never retry around it."""


class AuthenticationRequired(RuntimeError):
    """A live page did not provide positive authentication evidence."""


def check_security(page):
    text = page.locator("body").inner_text(timeout=15000)
    # Both phrases were observed on the real blocked page. No cookie names are assumed.
    if (
        "Sicherheitsüberprüfung wird durchgeführt" in text
        or "Nur einen Moment" in page.title()
    ):
        raise SecurityChallenge(
            "Conrad shows a security check. Stop here and complete it manually; "
            "no automated collection or workaround will run."
        )


def logout_is_visible(page) -> bool:
    # Only a visible logout marker is accepted as account evidence.
    return any(
        marker.is_visible() for marker in page.get_by_text("Abmelden", exact=True).all()
    )


def verify_live_login(page, *, manual_menu: bool):
    check_security(page)
    if not logout_is_visible(page) and manual_menu:
        print("Open the Conrad account menu so 'Abmelden' is visible.")
        print(
            "Do NOT sign in again in this verification window. If it is signed out, press Enter."
        )
        input("Press Enter after opening the menu: ")
        check_security(page)
    if not logout_is_visible(page):
        raise AuthenticationRequired(
            "No visible 'Abmelden' marker: this session is expired, signed out, or cannot be verified. "
            "Run: python conrad.py login. Collection must not continue as a guest."
        )


def goto_product(page):
    response = page.goto(EXAMPLE_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    return response.status if response else None


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_state():
    if not STATE.is_file() or not PROOF.is_file():
        raise AuthenticationRequired(
            "No verified saved session. Run: python conrad.py login"
        )
    proof = json.loads(PROOF.read_text(encoding="utf-8"))
    if (
        proof.get("state_sha256") != fingerprint(STATE)
        or proof.get("restored") is not True
    ):
        raise AuthenticationRequired(
            "Saved state has no matching restoration proof. Run login again."
        )
    # This local metadata is an audit record, never sufficient evidence for a new run.


def save_capture(page, name: str):
    from public_inspection import save_structure

    save_structure(page, name)


def login(channel: str):
    from playwright.sync_api import sync_playwright

    LOCAL.mkdir(exist_ok=True)
    candidate = LOCAL / "candidate_state.json"
    candidate.unlink(missing_ok=True)
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str((LOCAL / "browser-profile").resolve()),
                channel=channel,
                headless=False,
                locale="de-DE",
                viewport={"width": 1440, "height": 1000},
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                goto_product(page)
                print("Sign in to Conrad manually using its actual login controls.")
                print(
                    "Complete any security check manually. Never put credentials in this terminal."
                )
                print("Open the account menu and leave 'Abmelden' visible.")
                input(
                    "Press Enter when ready, or Ctrl+C to stop if the site remains blocked: "
                )
                verify_live_login(page, manual_menu=False)
                context.storage_state(path=str(candidate), indexed_db=True)
            finally:
                context.close()

            # This context has no access to the login profile. It uses only the saved state.
            browser = playwright.chromium.launch(channel=channel, headless=False)
            try:
                restored = browser.new_context(
                    storage_state=str(candidate), locale="de-DE"
                )
                page = restored.new_page()
                goto_product(page)
                print("A FRESH browser window is now testing saved-state restoration.")
                verify_live_login(page, manual_menu=True)
                candidate.replace(STATE)
                PROOF.write_text(
                    json_text(
                        {
                            "restored": True,
                            "verified_at": utc_now(),
                            "state_sha256": fingerprint(STATE),
                            "evidence": "Visible Abmelden in a fresh context; account menu opened manually",
                            "url": EXAMPLE_URL,
                        }
                    ),
                    encoding="utf-8",
                )
                print(
                    "Saved-state restoration verified in a fresh context. Initial login was manual."
                )
            finally:
                browser.close()
    finally:
        candidate.unlink(missing_ok=True)


def inspect_authenticated(channel: str):
    from playwright.sync_api import sync_playwright

    require_state()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=channel, headless=False)
        try:
            context = browser.new_context(
                storage_state=str(STATE),
                locale="de-DE",
                viewport={"width": 1440, "height": 1000},
            )
            page = context.new_page()
            goto_product(page)
            verify_live_login(page, manual_menu=True)
            print(
                "Close the account menu. Expand the technical data and document sections."
            )
            input("Press Enter to save the product DOM and screenshot: ")
            check_security(page)
            save_capture(page, "authenticated-product")
            print(
                "Open the all-offers panel manually; do not change quantities or add to cart."
            )
            input(
                "Press Enter to save its DOM and screenshot (Ctrl+C if no panel is available): "
            )
            check_security(page)
            save_capture(page, "authenticated-offers")
        finally:
            browser.close()


def diagnose(channel: str, headed: bool):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=channel, headless=not headed)
        try:
            page = browser.new_page(
                locale="de-DE", viewport={"width": 1440, "height": 1000}
            )
            status = goto_product(page)
            if headed:
                input(
                    "Inspect the page manually, then press Enter to save diagnostics: "
                )
            print(f"HTTP status at navigation: {status}")
            check_security(page)
            if status is None or status >= 400:
                raise DataError(f"HTTP {status}: this is not a successful product load")
            save_capture(page, "diagnostic")
            print(
                "Public page loaded. This does not prove authentication or a working collector."
            )
        finally:
            browser.close()
