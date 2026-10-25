import os
import unittest
from unittest.mock import patch

from sso import LunaSingleSignOn
from config import UIConfig


class AuthenticationTests(unittest.TestCase):
    def test_verified_allowlisted_email_is_required(self):
        auth = LunaSingleSignOn(frozenset({"owner@example.com"}), public_url="https://auth.example.com")
        self.assertTrue(auth.is_allowed({"email": "Owner@Example.com", "email_verified": True}))
        self.assertFalse(auth.is_allowed({"email": "owner@example.com", "email_verified": False}))
        self.assertFalse(auth.is_allowed({"email": "other@example.com", "email_verified": True}))

    def test_allowed_emails_are_required_and_normalized(self):
        with patch.dict(
            os.environ,
            {"LUNAVISIA_API_URL": "http://backend:8000", "LUNAVISIA_API_TOKEN": "x" * 32},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "LUNAVISIA_ALLOWED_EMAILS"):
                UIConfig.from_env()
        with patch.dict(
            os.environ,
            {
                "LUNAVISIA_API_URL": "http://backend:8000",
                "LUNAVISIA_API_TOKEN": "x" * 32,
                "LUNAVISIA_ALLOWED_EMAILS": " Owner@Example.com ",
            },
            clear=True,
        ):
            self.assertEqual(UIConfig.from_env().allowed_emails, frozenset({"owner@example.com"}))

    def test_api_token_is_required(self):
        with patch.dict(
            os.environ,
            {
                "LUNAVISIA_API_URL": "http://backend:8000",
                "LUNAVISIA_ALLOWED_EMAILS": "owner@example.com",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "LUNAVISIA_API_TOKEN"):
                UIConfig.from_env()


if __name__ == "__main__":
    unittest.main()
