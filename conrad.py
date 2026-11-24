"""Authenticated Balluff product collection using an isolated browser profile."""

import argparse
import json
import sys
from pathlib import Path

from browser_session import (
    AuthenticationRequired,
    SecurityChallenge,
    diagnose,
    inspect_authenticated,
    login,
)
from data import Checkpoint, DataError, read_csv, validate_rows, write_csv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [
        (
            "login",
            "Manual login, then verify saved-state restoration in a fresh context",
        ),
        (
            "inspect",
            "Inspect authenticated product structure (no account or raw HTML dump)",
        ),
        (
            "diagnose",
            "Inspect public product-page structure without collecting records",
        ),
    ]:
        command = sub.add_parser(name, help=help_text)
        command.add_argument(
            "--channel", default="msedge", choices=["msedge", "chrome", "chromium"]
        )
        if name == "diagnose":
            command.add_argument("--headed", action="store_true")
        if name == "inspect":
            command.add_argument(
                "--connection",
                choices=["storage-state", "cdp"],
                default="storage-state",
            )
            command.add_argument(
                "--browser", choices=["chrome", "edge"], default="chrome"
            )
    command = sub.add_parser(
        "browser-open",
        help="Open the installed browser with an isolated project profile and local CDP",
    )
    command.add_argument("--browser", choices=["chrome", "edge"], default="chrome")
    command = sub.add_parser(
        "browser-close", help="Request normal exit of only the verified project browser"
    )
    command.add_argument("--browser", choices=["chrome", "edge"], default="chrome")
    command = sub.add_parser(
        "session-check",
        help="Check the visible account marker in the managed persistent browser",
    )
    command.add_argument("--browser", choices=["chrome", "edge"], default="chrome")
    command.add_argument("--after-restart", action="store_true")
    command = sub.add_parser(
        "one", help="Extract and validate one real authenticated Balluff product"
    )
    command.add_argument("--browser", choices=["chrome", "edge"], default="chrome")
    from browser_session import EXAMPLE_URL

    command.add_argument("--url", default=EXAMPLE_URL)
    command = sub.add_parser(
        "discover",
        help="Discover canonical product links using observed brand-page pagination",
    )
    command.add_argument("--browser", choices=["chrome", "edge"], default="chrome")
    command.add_argument("--pages", type=int, default=2)
    command = sub.add_parser(
        "collect",
        help="Collect authenticated products sequentially, resuming SQLite progress",
    )
    command.add_argument("--browser", choices=["chrome", "edge"], default="chrome")
    command.add_argument("--count", type=int, default=100)
    command.add_argument("--delay", type=float, default=2.0)
    command = sub.add_parser(
        "review",
        help="Revisit three CSV sample cards and capture public product-only areas",
    )
    command.add_argument("--browser", choices=["chrome", "edge"], default="chrome")
    command = sub.add_parser(
        "validate", help="Check CSV structure, count, brand and unique identifiers"
    )
    command.add_argument("path", type=Path)
    command.add_argument("--expected", type=int, default=100)
    command = sub.add_parser(
        "export", help="Export completed checkpoint rows; no source data are fabricated"
    )
    command.add_argument(
        "--checkpoint", type=Path, default=Path("local/progress.sqlite3")
    )
    command.add_argument("--output", type=Path, default=Path("output/balluff.csv"))
    command.add_argument("--expected", type=int, default=100)
    args = parser.parse_args()
    try:
        if args.command == "login":
            login(args.channel)
        elif args.command == "inspect":
            if args.connection == "cdp":
                from project_browser import inspect_attached

                inspect_attached(args.browser)
            else:
                inspect_authenticated(args.channel)
        elif args.command == "browser-open":
            from project_browser import open_browser

            open_browser(args.browser)
        elif args.command == "browser-close":
            from project_browser import close_project_browser

            close_project_browser(args.browser)
        elif args.command == "session-check":
            from project_browser import session_check

            session_check(args.browser, after_restart=args.after_restart)
        elif args.command == "one":
            from collector import one

            one(args.browser, args.url)
        elif args.command == "discover":
            from collector import discover

            discover(args.browser, args.pages)
        elif args.command == "collect":
            from collector import collect

            collect(args.browser, args.count, args.delay)
        elif args.command == "review":
            from quality import review

            review(args.browser)
        elif args.command == "diagnose":
            diagnose(args.channel, args.headed)
        elif args.command == "validate":
            print(
                json.dumps(
                    validate_rows(read_csv(args.path), args.expected),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "export":
            if not args.checkpoint.is_file():
                raise DataError(
                    "No collection checkpoint exists. Run discover and collect in the verified project browser first."
                )
            checkpoint = Checkpoint(args.checkpoint)
            try:
                report = write_csv(args.output, checkpoint.rows(), args.expected)
            finally:
                checkpoint.close()
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except SecurityChallenge as exc:
        print(f"SECURITY CHECK: {exc}", file=sys.stderr)
        return 3
    except AuthenticationRequired as exc:
        print(f"AUTHENTICATION REQUIRED: {exc}", file=sys.stderr)
        return 4
    except (DataError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Stopped by the user. No collection was started.", file=sys.stderr)
        return 130
    except EOFError:
        print(
            "Interactive input is unavailable. Run this command in Windows PowerShell.",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        # Browser failures must produce a nonzero exit, never an apparently successful CSV.
        # Raw browser errors may include OAuth URLs or debugging WebSocket identifiers.
        print(
            f"BROWSER/UNEXPECTED ERROR: {type(exc).__name__}. "
            "Check that the dedicated browser is open and reachable; no raw network details were logged.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
