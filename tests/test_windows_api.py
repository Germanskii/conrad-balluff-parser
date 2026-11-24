"""Windows integration: separate from mocked, cross-platform isolation logic."""

import subprocess
import sys
import unittest
from project_browser import windows_arguments


@unittest.skipUnless(sys.platform == "win32", "Requires the Windows Shell32 API")
class WindowsArgumentTests(unittest.TestCase):
    def test_native_parser_preserves_spaces_and_quoted_profile_paths(self):
        cases = [
            [
                "browser.exe",
                "--user-data-dir=C:/Synthetic Project/Profile",
                "--remote-debugging-port=9223",
            ],
            [
                "C:/Program Files/Synthetic/browser.exe",
                "--user-data-dir=C:/Test/Profile",
            ],
        ]
        for args in cases:
            with self.subTest(args=args):
                self.assertEqual(windows_arguments(subprocess.list2cmdline(args)), args)


if __name__ == "__main__":
    unittest.main()
