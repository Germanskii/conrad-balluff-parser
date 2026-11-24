"""Offline authentication gates, not proof of successful Conrad login."""

import unittest
from unittest.mock import Mock

from browser_session import AuthenticationRequired, SecurityChallenge, verify_live_login


def synthetic_page(text="Synthetic product", *, logout_visible=False):
    page = Mock()
    page.locator.return_value.inner_text.return_value = text
    page.title.return_value = "Synthetic test page"
    marker = Mock()
    marker.is_visible.return_value = logout_visible
    page.get_by_text.return_value.all.return_value = [marker]
    return page


class AuthGateTests(unittest.TestCase):
    def test_hidden_logout_does_not_prove_authentication(self):
        with self.assertRaises(AuthenticationRequired):
            verify_live_login(synthetic_page(logout_visible=False), manual_menu=False)

    def test_security_check_takes_precedence_over_a_logout_marker(self):
        page = synthetic_page(
            "Sicherheitsüberprüfung wird durchgeführt", logout_visible=True
        )
        with self.assertRaises(SecurityChallenge):
            verify_live_login(page, manual_menu=False)

    def test_positive_marker_allows_gate_without_credentials(self):
        page = synthetic_page(logout_visible=True)
        verify_live_login(page, manual_menu=False)
        page.get_by_text.assert_called_with("Abmelden", exact=True)


if __name__ == "__main__":
    unittest.main()
