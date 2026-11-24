"""Offline isolation checks; do not claim that a site login succeeded."""

import json
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import project_browser as pb
from data import DataError
from browser_session import AuthenticationRequired


class BrowserIsolationTests(unittest.TestCase):
    def setUp(self):
        self.executable = pb.ROOT / "synthetic-chrome.exe"
        self.manifest = {
            "browser": "chrome",
            "port": 9223,
            "profile": str(pb.profile_path("chrome")),
            "pid": 123,
            "process_started": "synthetic-start",
        }
        self.row = {
            "pid": 123,
            "address": "127.0.0.1",
            "started": "synthetic-start",
            "executable": str(self.executable),
            "command_line": subprocess.list2cmdline(
                [
                    str(self.executable),
                    f"--user-data-dir={pb.profile_path('chrome')}",
                    "--remote-debugging-port=9223",
                ]
            ),
        }

    def validate(self, row=None, manifest=None, arguments=None):
        parsed = arguments or [
            str(self.executable),
            "--user-data-dir=" + str(pb.profile_path("chrome")),
            "--remote-debugging-port=9223",
        ]
        with (
            patch.object(pb, "browser_executable", return_value=self.executable),
            patch.object(pb, "windows_arguments", return_value=parsed),
        ):
            pb.validate_runtime(
                "chrome", manifest or self.manifest, {"listeners": [row or self.row]}
            )

    def test_correct_project_process_is_accepted(self):
        self.validate()

    def test_nonlocal_listener_and_unexpected_process_are_rejected(self):
        for changes in [
            {"address": "0.0.0.0"},
            {"address": "::"},
            {"pid": 999},
            {"started": "reused-pid-different-process"},
        ]:
            with self.subTest(changes=changes), self.assertRaises(DataError):
                self.validate(dict(self.row, **changes))

    def test_primary_or_other_profile_cannot_be_attached(self):
        command = subprocess.list2cmdline(
            [
                str(self.executable),
                "--user-data-dir=C:\\SyntheticPrimary\\User Data",
                "--remote-debugging-port=9223",
            ]
        )
        with self.assertRaises(DataError):
            self.validate(
                dict(self.row, command_line=command),
                arguments=[
                    str(self.executable),
                    "--user-data-dir=SyntheticPrimary",
                    "--remote-debugging-port=9223",
                ],
            )
        with self.assertRaises(DataError):
            self.validate(
                manifest=dict(self.manifest, profile="C:\\SyntheticPrimary\\User Data")
            )

    def test_no_listener_never_counts_as_an_attachable_browser(self):
        with self.assertRaises(DataError):
            pb.validate_runtime("chrome", self.manifest, {"listeners": []})

    def test_repeated_check_in_same_process_cannot_claim_another_restart(self):
        @contextmanager
        def connection(_):
            yield None, None, {"generation": "already-verified-process"}

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            proof = {
                "initial_generation": "original-process",
                "last_generation": "already-verified-process",
                "restart_verified": True,
            }
            (root / "chrome-auth.json").write_text(json.dumps(proof), encoding="utf-8")
            with (
                patch.object(pb, "LOCAL", root),
                patch.object(pb, "attached_browser", connection),
            ):
                with self.assertRaises(AuthenticationRequired):
                    pb.session_check("chrome", after_restart=True)


if __name__ == "__main__":
    unittest.main()
