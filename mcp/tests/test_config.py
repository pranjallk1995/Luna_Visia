"""Settings read from the environment."""

import os
import unittest
from unittest import mock

from app.core.config import AppConfig

BASE = {"DATABASE_URL": "postgresql://test/db", "LUNAVISIA_API_TOKEN": "t" * 32}


def config_with(**settings: str) -> AppConfig:
    with mock.patch.dict(os.environ, {**BASE, **settings}, clear=True):
        return AppConfig.from_env()


class McpPublicUrlTests(unittest.TestCase):
    def test_https_is_accepted(self) -> None:
        self.assertEqual(config_with(MCP_PUBLIC_URL="https://mcp.example.com/").mcp_public_url, "https://mcp.example.com")

    def test_plain_http_on_localhost_is_accepted_for_a_start_without_a_tunnel(self) -> None:
        for url in ("http://localhost:18005", "http://127.0.0.1:18005"):
            self.assertEqual(config_with(MCP_PUBLIC_URL=url).mcp_public_url, url)

    def test_plain_http_elsewhere_is_refused(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "MCP_PUBLIC_URL"):
            config_with(MCP_PUBLIC_URL="http://mcp.example.com")

    def test_blank_means_embedded_icons_only(self) -> None:
        self.assertEqual(config_with(MCP_PUBLIC_URL="").mcp_public_url, "")


if __name__ == "__main__":
    unittest.main()
