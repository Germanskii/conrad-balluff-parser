"""Offline pagination checks; no browser session required."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import contextmanager
import collector
from data import DataError

URL = "https://www.conrad.de/de/p/balluff-test-3416652.html"
OTHER = "https://www.conrad.de/de/p/balluff-test-3416024.html"


class DiscoveryTests(unittest.TestCase):
    def test_changed_number_with_old_cards_is_not_accepted(self):
        page = Mock()
        page.evaluate.side_effect = [
            {"number": "2", "links": [URL]},
            {"number": "2", "links": [OTHER]},
            {"number": "2", "links": [OTHER]},
        ]
        with patch.object(collector, "check_security"):
            links = collector.wait_listing(page, 2, {"3416652"}, attempts=3)
        self.assertEqual(set(links), {"3416024"})
        self.assertEqual(page.evaluate.call_count, 3)

    def test_unchanged_cards_fail_with_bounded_polls(self):
        page = Mock()
        page.evaluate.return_value = {"number": "2", "links": [URL]}
        with patch.object(collector, "check_security"), self.assertRaises(DataError):
            collector.wait_listing(page, 2, {"3416652"}, attempts=2)
        self.assertEqual(page.evaluate.call_count, 2)

    def test_failed_transition_keeps_first_page_and_incomplete_status(self):
        page = Mock()
        page.url = "https://www.conrad.de/de/marken/balluff.html"
        context = Mock(pages=[page])

        @contextmanager
        def browser(_):
            yield None, context, {}

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "links.json"
            with (
                patch.object(collector, "LINKS", target),
                patch.object(collector, "attached_browser", browser),
                patch.object(collector, "require_profile_proof"),
                patch.object(collector, "navigate"),
                patch.object(collector, "click_visible"),
                patch.object(
                    collector,
                    "wait_listing",
                    side_effect=[{"3416652": URL}, DataError("synthetic timeout")],
                ),
            ):
                with self.assertRaises(DataError):
                    collector.discover("chrome", 2)
            saved = json.loads(target.read_text(encoding="utf-8"))
            self.assertIs(saved["complete"], False)
            self.assertEqual(saved["completed_pages"], 1)
            self.assertEqual(len(saved["links"]), 1)
            self.assertEqual(
                saved["failure_stage"], "page 2: wait for page number and new cards"
            )
