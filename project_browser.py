"""Ordinary installed browser, isolated profile, and verified loopback-only CDP."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

from browser_session import (
    AuthenticationRequired,
    EXAMPLE_URL,
    check_security,
    logout_is_visible,
)
from data import DataError, json_text, utc_now

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / "local"
PORTS = {"chrome": 9223, "edge": 9224}


def profile_path(browser: str) -> Path:
    if browser not in PORTS:
        raise DataError("Choose chrome or edge")
    return (LOCAL / f"{browser}-profile").resolve()


def manifest_path(browser: str) -> Path:
    profile_path(browser)
    return LOCAL / f"{browser}-connection.json"


def browser_executable(browser: str) -> Path:
    relative = {
        "chrome": Path("Google/Chrome/Application/chrome.exe"),
        "edge": Path("Microsoft/Edge/Application/msedge.exe"),
    }[browser]
    for base in [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ]:
        if base and (Path(base) / relative).is_file():
            return (Path(base) / relative).resolve()
    raise DataError(f"Installed {browser} executable was not found")


def windows_arguments(command_line: str) -> list[str]:
    if sys.platform != "win32":
        raise DataError(
            "Project browser process verification currently requires Windows"
        )
    from ctypes import wintypes

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW(command_line, ctypes.byref(count))
    if not pointer:
        raise DataError("Cannot inspect the browser command line")
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(pointer)


def runtime_info(port: int) -> dict:
    if port not in PORTS.values():
        raise DataError("Only the fixed project CDP ports are accepted")
    # The only interpolated value is an allowlisted integer. No profile path or URL
    # is interpolated into PowerShell. Raw command lines stay in memory only.
    script = """
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new()
$listeners=@(Get-NetTCPConnection -State Listen -LocalPort PORT_NUMBER -ErrorAction SilentlyContinue)
$rows=@(foreach($item in $listeners) {
  $browserProcess=Get-CimInstance Win32_Process -Filter ("ProcessId="+$item.OwningProcess)
  [pscustomobject]@{address=$item.LocalAddress;pid=$item.OwningProcess;
    executable=$browserProcess.ExecutablePath;command_line=$browserProcess.CommandLine;
    started=$browserProcess.CreationDate.ToUniversalTime().ToString('o')}
})
ConvertTo-Json -InputObject $rows -Compress
""".replace("PORT_NUMBER", str(port))
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    if result.returncode:
        raise DataError(
            "Cannot verify CDP listener ownership; run from ordinary Windows PowerShell"
        )
    rows = json.loads(result.stdout.strip() or "[]")
    return {"listeners": rows}


def validate_runtime(browser: str, manifest: dict, info: dict) -> None:
    expected_profile = profile_path(browser)
    if manifest.get("browser") != browser or manifest.get("port") != PORTS[browser]:
        raise DataError("Unexpected browser connection metadata")
    if Path(manifest.get("profile", "")).resolve() != expected_profile:
        raise DataError(
            "Refusing a profile outside this project's dedicated browser directory"
        )
    rows = info["listeners"]
    if not rows:
        raise DataError(
            f"Project {browser} is not listening. Run browser-open --browser {browser}"
        )
    for row in rows:
        if row["address"] not in {"127.0.0.1", "::1"}:
            raise DataError(
                "CDP is not bound exclusively to loopback; refusing connection"
            )
        if int(row["pid"]) != manifest.get("pid") or row["started"] != manifest.get(
            "process_started"
        ):
            raise DataError("CDP port is owned by an unexpected browser process")
        if Path(row["executable"]).resolve() != browser_executable(browser):
            raise DataError(
                "CDP listener does not belong to the expected installed browser"
            )
        args = windows_arguments(row["command_line"])
        data_dirs = [
            a.split("=", 1)[1] for a in args if a.startswith("--user-data-dir=")
        ]
        debug_ports = [
            a.split("=", 1)[1] for a in args if a.startswith("--remote-debugging-port=")
        ]
        if len(data_dirs) != 1 or Path(data_dirs[0]).resolve() != expected_profile:
            raise DataError("Listener does not use the exact isolated project profile")
        if debug_ports != [str(PORTS[browser])]:
            raise DataError("Unexpected CDP command-line port")


def endpoint_metadata(browser: str) -> dict:
    endpoint = f"http://127.0.0.1:{PORTS[browser]}"
    # Never send a local debugging request through a configured external proxy.
    with build_opener(ProxyHandler({})).open(
        endpoint + "/json/version", timeout=5
    ) as response:
        metadata = json.load(response)
    socket_url = urlsplit(metadata.get("webSocketDebuggerUrl", ""))
    if (
        socket_url.scheme != "ws"
        or socket_url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or socket_url.port != PORTS[browser]
    ):
        raise DataError("The CDP server advertised a nonlocal WebSocket")
    return metadata


def checked_connection(browser: str) -> dict:
    path = manifest_path(browser)
    if not path.is_file():
        raise DataError(
            f"No managed {browser} connection. Run browser-open --browser {browser}"
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validate_runtime(browser, manifest, runtime_info(PORTS[browser]))
    endpoint_metadata(browser)
    return manifest


def open_browser(browser: str):
    executable = browser_executable(browser)
    profile = profile_path(browser)
    LOCAL.mkdir(exist_ok=True)
    info = runtime_info(PORTS[browser])
    if info["listeners"]:
        checked_connection(browser)
        print(
            f"Project {browser} is already open; reusing its verified loopback connection."
        )
        return
    profile.mkdir(exist_ok=True)
    args = [
        str(executable),
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={PORTS[browser]}",
        "--remote-debugging-address=127.0.0.1",
        "--new-window",
        EXAMPLE_URL,
    ]
    subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    deadline = time.monotonic() + 15
    info = {"listeners": []}
    while time.monotonic() < deadline:
        time.sleep(0.5)
        info = runtime_info(PORTS[browser])
        if info["listeners"]:
            break
    if not info["listeners"]:
        raise DataError(
            "Browser started without a verified CDP listener; no connection was attempted"
        )
    row = info["listeners"][0]
    manifest = {
        "browser": browser,
        "profile": str(profile),
        "port": PORTS[browser],
        "pid": int(row["pid"]),
        "process_started": row["started"],
        "generation": str(uuid.uuid4()),
        "opened_at": utc_now(),
    }
    validate_runtime(browser, manifest, info)
    metadata = endpoint_metadata(browser)
    manifest_path(browser).write_text(json_text(manifest), encoding="utf-8")
    print(
        f"Opened installed {browser}: {metadata.get('Browser', 'version unavailable')}"
    )
    print(f"Dedicated profile: {profile}")
    print(
        f"CDP verified on loopback port {PORTS[browser]}. Primary profiles were not opened."
    )
    print(
        "Complete site checks and Google login manually in this window. Leave Abmelden visible."
    )
    print(f"Then run: python conrad.py session-check --browser {browser}")


@contextmanager
def attached_browser(browser: str):
    from playwright.sync_api import sync_playwright

    manifest = checked_connection(browser)
    with sync_playwright() as playwright:
        connection = playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{PORTS[browser]}", no_defaults=True, timeout=15000
        )
        if len(connection.contexts) != 1:
            raise DataError("Expected the one existing persistent browser context")
        # Do not close the persistent context/browser: stopping Playwright disconnects
        # this client and leaves the user's dedicated visible window running.
        yield connection, connection.contexts[0], manifest


def verify_account_menu(page):
    """Use the account button observed on the authenticated live product page."""
    page.bring_to_front()
    check_security(page)
    if not logout_is_visible(page):
        click_visible(
            page,
            page.get_by_role("button", name="Konto", exact=True),
            wait_handler=True,
        )
        deadline = time.monotonic() + 10
        while not logout_is_visible(page) and time.monotonic() < deadline:
            page.wait_for_timeout(200)
    if not logout_is_visible(page):
        raise AuthenticationRequired(
            "The account menu does not show Abmelden; sign in manually again"
        )


def click_visible(page, locator, *, wait_handler: bool = False, pointer: bool = False):
    """Activate a read-only UI control after visibility, hit and enabled checks.

    Background desktop tabs can suspend requestAnimationFrame, which otherwise
    stalls Playwright's stability wait. No site or browser protection is changed.
    """
    locator.wait_for(state="visible", timeout=15000)
    # Observed on the live Vue UI: an SSR button exists before its onClick
    # invoker is attached. Reading this readiness marker never changes the page.
    deadline = time.monotonic() + 20
    while wait_handler and not locator.evaluate("""e => Object.getOwnPropertySymbols(e).some(
        symbol => symbol.description === '_vei' && e[symbol] && Object.keys(e[symbol]).some(k=>k.startsWith('onClick'))
    )"""):
        if time.monotonic() >= deadline:
            raise DataError(
                "The observed UI control did not finish hydration within 20 seconds"
            )
        page.wait_for_timeout(200)
    hit = locator.evaluate("""e => {
        e.scrollIntoView({block:'center', behavior:'instant'});
        const r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
        return {x,y,visible:e.checkVisibility(),enabled:!e.disabled,
                topmost:e.contains(document.elementFromPoint(x,y))};
    }""")
    if not all(hit[key] for key in ["visible", "enabled", "topmost"]):
        raise DataError(
            "The requested read-only control is hidden, disabled or covered"
        )
    # Target the inspected element directly: sticky header movement between the
    # hit test and OS pointer delivery otherwise makes background-window clicks
    # miss. This dispatches its ordinary UI handler; it changes no browser flags,
    # page security checks, credentials, or fingerprint properties.
    if pointer:
        page.mouse.click(hit["x"], hit["y"])
    else:
        locator.dispatch_event("click")


def require_profile_proof(browser: str, manifest: dict):
    path = LOCAL / f"{browser}-auth.json"
    if not path.is_file():
        raise AuthenticationRequired(
            "Run session-check and the restart verification first"
        )
    proof = json.loads(path.read_text(encoding="utf-8"))
    if (
        not proof.get("restart_verified")
        or proof["last_generation"] != manifest["generation"]
    ):
        raise AuthenticationRequired(
            "Verify this process with session-check --after-restart"
        )


def close_account_menu(page):
    if not logout_is_visible(page):
        return
    panel = page.get_by_role("dialog", name="Fly-in panel", exact=True)
    backdrop = panel.locator("xpath=preceding-sibling::div")
    click_visible(page, backdrop, pointer=True)
    deadline = time.monotonic() + 3
    while logout_is_visible(page) and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    if logout_is_visible(page):
        raise DataError("The account panel did not close normally")


def product_page(context, *, navigate: bool = False):
    for page in context.pages:
        parts = urlsplit(page.url)
        if (
            parts.hostname == "www.conrad.de"
            and parts.path == urlsplit(EXAMPLE_URL).path
        ):
            return page
    if navigate:
        for page in context.pages:
            if urlsplit(page.url).hostname == "www.conrad.de":
                verify_account_menu(page)
                response = page.goto(
                    EXAMPLE_URL, wait_until="domcontentloaded", timeout=60000
                )
                check_security(page)
                if not response or response.status >= 400:
                    raise DataError("The authenticated example card failed to load")
                page.locator("h1").wait_for(state="visible", timeout=15000)
                return page
    raise DataError(
        "Open the example Conrad card in the project browser before checking"
    )


def session_check(browser: str, *, after_restart: bool = False) -> dict:
    proof_path = LOCAL / f"{browser}-auth.json"
    previous = (
        json.loads(proof_path.read_text(encoding="utf-8"))
        if proof_path.is_file()
        else None
    )
    with attached_browser(browser) as (_, context, manifest):
        if after_restart and (
            not previous or previous["last_generation"] == manifest["generation"]
        ):
            raise AuthenticationRequired(
                "A previous positive check and a NEW browser process are required"
            )
        page = product_page(context, navigate=True)
        verify_account_menu(page)
        heading = page.locator("h1").all_text_contents()
        if not any("BALLUFF" in title.upper() for title in heading):
            raise DataError(
                "Authenticated marker found, but the Balluff product heading is not loaded"
            )
        proof = {
            "browser": browser,
            "initial_generation": previous["initial_generation"]
            if previous
            else manifest["generation"],
            "last_generation": manifest["generation"],
            "last_checked_at": utc_now(),
            "restart_verified": bool(
                after_restart
                or previous
                and previous.get("restart_verified")
                and previous["last_generation"] == manifest["generation"]
            ),
            "evidence": {
                "visible_logout_marker": True,
                "example_product_heading": True,
            },
            "manual_initial_login": True,
            "storage_state_portability_tested": False,
        }
        proof_path.write_text(json_text(proof), encoding="utf-8")
        print(
            json.dumps(
                {
                    key: proof[key]
                    for key in [
                        "browser",
                        "last_checked_at",
                        "restart_verified",
                        "evidence",
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return proof


def inspect_attached(browser: str):
    from public_inspection import save_structure

    proof_path = LOCAL / f"{browser}-auth.json"
    if not proof_path.is_file():
        raise AuthenticationRequired("Run session-check first")
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    with attached_browser(browser) as (_, context, manifest):
        if (
            not proof.get("restart_verified")
            or proof["last_generation"] != manifest["generation"]
        ):
            raise AuthenticationRequired(
                "Verify this browser process using session-check --after-restart first"
            )
        page = product_page(context)
        verify_account_menu(page)
        save_structure(page, "authenticated-product")


def close_project_browser(browser: str):
    """Request normal exit only from the verified isolated project process."""
    from playwright.sync_api import Error

    with attached_browser(browser) as (connection, _, manifest):
        session = connection.new_browser_cdp_session()
        try:
            session.send("Browser.close")
        except Error:
            # Browser.close can drop the transport before returning its response.
            pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not runtime_info(PORTS[browser])["listeners"]:
            print(
                f"Project {browser} exited normally. No operating-system process kill was used."
            )
            return
        time.sleep(0.5)
    raise DataError("Project browser has not exited; no force-kill was attempted")
