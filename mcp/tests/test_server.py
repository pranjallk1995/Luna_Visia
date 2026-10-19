"""Tests for the MCP tool surface, HTTP routes, and image delivery helpers."""

import asyncio
import base64
import json
import os
import tempfile
import time
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image as PillowImage
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

from fakes import (
    FakeAnalyzer,
    FakeCredentialRepository,
    FakeImageRepository,
    FakePublicURLRepository,
    image_bytes,
    png_payload,
)

from app.branding import ServerBranding
from app.config import AppConfig
from app.downloads import DownloadStore
from app.http_routes import TOKEN_HEADER, VisiaHTTPRoutes
from app.mcp_tools import VisiaMCPTools
from app.media import ImageArchive, PreviewRenderer
from app.remote import ImageSourceResolver
from app.security import CredentialPolicy, HiddenVaultAuth, SessionStore
from app.services import VisiaService


class ServerTestCase(unittest.TestCase):
    """Register the real adapters over an in-memory service graph."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.downloads_temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = AppConfig(
            image_dir=self.root,
            database_url="postgresql://test",
            ollama_url="http://ollama:11434",
            vision_model="test-vision",
            public_base_url="https://vault.example.test/vault",
            download_dir=Path(self.downloads_temp.name),
        )
        self.repository = FakeImageRepository()
        self.service = VisiaService.create(
            self.config,
            self.repository,
            FakePublicURLRepository(self.repository),
            FakeAnalyzer(),
        )
        self.auth = HiddenVaultAuth(
            FakeCredentialRepository(), CredentialPolicy(100_000), SessionStore(60)
        )
        self.branding = ServerBranding()
        # Match production: a non-localhost bind leaves DNS-rebinding checks off for TestClient.
        self.server = FastMCP("Luna Visia test", host="0.0.0.0", icons=self.branding.mcp_icons())
        self.mcp_tools = VisiaMCPTools(
            self.server,
            self.service,
            ImageSourceResolver(
                self.service.visible.decoder,
                self.config.max_image_bytes,
                self.config.max_upload_redirects,
            ),
            PreviewRenderer(self.config.display_max_edge),
        )
        VisiaHTTPRoutes(self.server, self.service, self.auth, self.branding)
        self.service.visible.ingest("one.png", png_payload("red", (1200, 800)))
        self.service.visible.ingest("two.png", png_payload("blue", (640, 480)))

    def tearDown(self) -> None:
        self.temp.cleanup()
        self.downloads_temp.cleanup()

    def resolver(self) -> ImageSourceResolver:
        return ImageSourceResolver(self.service.visible.decoder, 1024 * 1024, 3)

    def call(self, tool_name: str, arguments: dict):
        tool = self.server._tool_manager.get_tool(tool_name)
        return asyncio.run(tool.run(arguments))

    def client(self) -> TestClient:
        return TestClient(self.server.streamable_http_app())


class MCPToolTests(ServerTestCase):
    def test_upload_image_accepts_base64(self) -> None:
        result = self.call("upload_image", {"name": "chat.png", "content_base64": png_payload()})
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["name"], "chat.png")
        self.assertTrue((self.root / "chat.png").is_file())

    def test_upload_image_accepts_image_data_url(self) -> None:
        result = self.call(
            "upload_image",
            {"name": "chat.png", "image_url": f"data:image/png;base64,{png_payload()}"},
        )
        self.assertEqual(result["status"], "created")

    def test_upload_source_requires_exactly_one_input(self) -> None:
        resolver = self.resolver()
        with self.assertRaisesRegex(ValueError, "exactly one"):
            resolver.resolve(None, None)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            resolver.resolve("aW1hZ2U=", "data:image/png;base64,aW1hZ2U=")

    def test_upload_source_rejects_private_network_urls(self) -> None:
        with self.assertRaisesRegex(ValueError, "public"):
            self.resolver().resolve(None, "https://127.0.0.1/cat.png")

    def test_view_image_returns_displayable_image_data(self) -> None:
        content = self.call("view_image", {"name": "one.png"}).to_image_content()
        self.assertEqual(content.mimeType, "image/jpeg")
        self.assertTrue(base64.b64decode(content.data))

    def test_get_image_url_returns_visible_https_location(self) -> None:
        self.assertEqual(
            self.call("get_image_url", {"name": "one.png"}),
            {
                "name": "one.png",
                "url": "https://vault.example.test/vault/api/public/images/image-1",
            },
        )

    def test_rename_image_preserves_public_url(self) -> None:
        renamed = self.call("rename_image", {"name": "one.png", "new_name": "renamed.png"})
        self.assertEqual(renamed["name"], "renamed.png")
        self.assertTrue(renamed["public_url"].endswith("/image-1"))

    def test_set_public_base_url_updates_urls_from_chat(self) -> None:
        result = self.call(
            "set_public_base_url", {"public_base_url": "https://new.example.test/vault/"}
        )
        self.assertEqual(result["public_base_url"], "https://new.example.test/vault")
        self.assertEqual(result["updated_image_count"], 2)

    def test_set_favorite_images_adds_and_removes_from_chat(self) -> None:
        added = self.call("set_favorite_images", {"names": ["one.png", "two.png"], "favorite": True})
        removed = self.call("set_favorite_images", {"names": ["one.png"], "favorite": False})
        self.assertEqual((added["status"], added["count"]), ("favorited", 2))
        self.assertEqual((removed["status"], removed["names"]), ("unfavorited", ["one.png"]))

    def test_fetch_images_rejects_hidden_images(self) -> None:
        self.service.hidden.set_hidden(["one.png"], True)
        with self.assertRaises(Exception):
            self.call("fetch_images", {"names": ["one.png"]})

    def test_fetch_images_zip_returns_download_link_not_blob(self) -> None:
        result = self.call("fetch_images", {"names": ["one.png", "two.png"], "delivery": "zip"})
        self.assertEqual(result["status"], "zip_ready")
        self.assertTrue(
            result["download_url"].startswith(
                "https://vault.example.test/vault/api/downloads/"
            )
        )
        self.assertEqual((result["image_count"], result["names"]), (2, ["one.png", "two.png"]))
        self.assertLess(len(json.dumps(result)), 1024)

    def test_fetch_images_zip_rejects_hidden_images(self) -> None:
        self.service.hidden.set_hidden(["one.png"], True)
        with self.assertRaises(Exception):
            self.call("fetch_images", {"names": ["one.png"], "delivery": "zip"})


class ChatUploadToolTests(ServerTestCase):
    def test_upload_tool_declares_chatgpt_file_param(self) -> None:
        tool = next(t for t in asyncio.run(self.server.list_tools()) if t.name == "upload_image")
        self.assertEqual(tool.meta, {"openai/fileParams": ["file"]})
        self.assertIn("download_url", json.dumps(tool.inputSchema["properties"]["file"]))
        self.assertNotIn("name", tool.inputSchema.get("required", []))

    def test_upload_from_chatgpt_file_reference(self) -> None:
        resolver = self.server_resolver()
        resolver._download = lambda url: (image_bytes("JPEG"), url)
        result = self.call(
            "upload_image",
            {"file": {"download_url": "https://files.example.test/abc", "file_id": "file-1", "file_name": "Holiday.png"}},
        )
        self.assertEqual((result["status"], result["name"]), ("created", "Holiday.jpg"))

    def test_upload_without_name_uses_url_filename(self) -> None:
        self.server_resolver()._download = lambda url: (image_bytes("PNG", "green"), url)
        result = self.call("upload_image", {"image_url": "https://cdn.example.test/photos/beach.png"})
        self.assertEqual(result["name"], "beach.png")

    def test_local_file_paths_explain_upload_link(self) -> None:
        for path in ("/mnt/data/cat.png", "file:///tmp/cat.png", "sandbox:/mnt/data/cat.png"):
            with self.subTest(path), self.assertRaisesRegex(Exception, "create_upload_link"):
                self.call("upload_image", {"image_url": path})

    def test_slow_upload_does_not_block_event_loop(self) -> None:
        analyzer = self.service.indexer.analyzer
        original = analyzer.analyze

        def slow(data):
            time.sleep(0.5)
            return original(data)

        analyzer.analyze = slow

        async def scenario():
            upload = asyncio.create_task(
                self.server.call_tool("upload_image", {"name": "slow.png", "content_base64": png_payload("teal")})
            )
            started = time.monotonic()
            await asyncio.sleep(0.05)
            responsive_after = time.monotonic() - started
            await upload
            return responsive_after

        self.assertLess(asyncio.run(scenario()), 0.3)

    def server_resolver(self) -> ImageSourceResolver:
        return self.mcp_tools.sources


class UploadLinkRouteTests(ServerTestCase):
    def link_path(self, name: str = "cat.png") -> str:
        url = self.call("create_upload_link", {"name": name})["upload_url"]
        return url.removeprefix("https://vault.example.test/vault")

    def test_raw_body_upload(self) -> None:
        path = self.link_path("cat")
        with self.client() as client:
            response = client.post(path, content=image_bytes("PNG", "purple"))
            reused = client.post(path, content=image_bytes("PNG", "orange"))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "cat.png")
        self.assertEqual(reused.status_code, 404)

    def test_multipart_upload(self) -> None:
        with self.client() as client:
            response = client.post(
                self.link_path("dog.jpg"),
                files={"file": ("IMG_0001.HEIC.jpg", image_bytes("JPEG", "navy"), "image/jpeg")},
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "dog.jpg")

    def test_oversized_upload_is_rejected(self) -> None:
        self.service.uploads.max_bytes = 100
        with self.client() as client:
            response = client.post(self.link_path(), content=b"x" * 101)
        self.assertEqual(response.status_code, 400)
        self.assertIn("limit", response.json()["error"])


class MediaTests(ServerTestCase):
    def test_single_display_returns_one_bounded_jpeg_preview(self) -> None:
        jpeg = PreviewRenderer(self.config.display_max_edge).render([self.root / "one.png"])
        with PillowImage.open(BytesIO(jpeg)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLessEqual(max(image.size), self.config.display_max_edge)

    def test_multiple_display_returns_one_contact_sheet(self) -> None:
        jpeg = PreviewRenderer(768).render([self.root / "one.png", self.root / "two.png"])
        with PillowImage.open(BytesIO(jpeg)) as image:
            self.assertEqual(image.size, (1024, 424))

    def test_archive_stores_originals_uncompressed(self) -> None:
        destination = Path(self.downloads_temp.name) / "test.zip"
        ImageArchive.write([self.root / "two.png"], destination)
        with zipfile.ZipFile(destination) as bundle:
            entry = bundle.getinfo("two.png")
            self.assertEqual(entry.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(bundle.read("two.png"), (self.root / "two.png").read_bytes())

    def test_archive_entry_names_drop_upload_prefix_and_stay_unique(self) -> None:
        prefix_a, prefix_b = "a" * 32, "b" * 32
        names = ImageArchive.entry_names(
            [Path(f"{prefix_a}_cat.png"), Path(f"{prefix_b}_cat.png"), Path("Cat.png"), Path("dog.png")]
        )
        self.assertEqual(names, ["cat.png", "cat (2).png", "Cat (3).png", "dog.png"])


class DownloadRouteTests(ServerTestCase):
    def create_zip(self, names: list[str]) -> dict:
        return self.call("fetch_images", {"names": names, "delivery": "zip"})

    @staticmethod
    def path_of(download_url: str) -> str:
        return download_url.removeprefix("https://vault.example.test/vault")

    def test_download_link_serves_zip_attachment(self) -> None:
        result = self.create_zip(["two.png", "one.png"])
        with self.client() as client:
            response = client.get(self.path_of(result["download_url"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("luna-visia-images.zip", response.headers["content-disposition"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(len(response.content), result["size_bytes"])
        with zipfile.ZipFile(BytesIO(response.content)) as bundle:
            self.assertEqual(bundle.namelist(), ["two.png", "one.png"])
            self.assertEqual(bundle.read("one.png"), (self.root / "one.png").read_bytes())

    def test_zip_exceeding_display_limits_is_allowed(self) -> None:
        names = []
        for index in range(self.config.max_fetch_images + 5):
            name = f"bulk_{index}.png"
            self.service.visible.ingest(name, png_payload("red"))
            names.append(name)
        result = self.create_zip(names)
        self.assertEqual(result["image_count"], len(names))

    def test_zip_enforces_archive_limits(self) -> None:
        downloads = self.service.downloads
        downloads.max_bytes = 10
        with self.assertRaisesRegex(ValueError, "limit"):
            downloads.create_zip(["one.png"])

    def test_unknown_or_expired_links_return_404_and_are_purged(self) -> None:
        live = self.create_zip(["one.png"])
        self.service.downloads.store.ttl_seconds = 0
        expired = self.create_zip(["two.png"])
        with self.client() as client:
            self.assertEqual(client.get("/api/downloads/not-a-token").status_code, 404)
            self.assertEqual(client.get(self.path_of(expired["download_url"])).status_code, 404)
            self.assertEqual(client.get(self.path_of(live["download_url"])).status_code, 200)
        remaining = [path.stem for path in Path(self.downloads_temp.name).glob("*.zip")]
        self.assertEqual(remaining, [live["download_url"].rsplit("/", 1)[-1]])

    def store_dir(self) -> Path:
        return Path(self.downloads_temp.name)

    def test_link_survives_restart(self) -> None:
        result = self.create_zip(["one.png", "two.png"])
        # A new store over the same directory models a service restart.
        self.service.downloads.store = DownloadStore(self.store_dir(), 3600, 10**9)
        with self.client() as client:
            response = client.get(self.path_of(result["download_url"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.content), result["size_bytes"])

    def test_store_evicts_oldest_archives_over_size_cap(self) -> None:
        first = self.create_zip(["one.png"])
        size = first["size_bytes"]
        self.service.downloads.store.max_total_bytes = size + 1
        second = self.create_zip(["two.png"])
        with self.client() as client:
            self.assertEqual(client.get(self.path_of(first["download_url"])).status_code, 404)
            self.assertEqual(client.get(self.path_of(second["download_url"])).status_code, 200)

    def test_incomplete_archive_without_ticket_is_not_served(self) -> None:
        token = "A" * 43
        (self.store_dir() / f"{token}.zip").write_bytes(b"partial")
        with self.client() as client:
            self.assertEqual(client.get(f"/api/downloads/{token}").status_code, 404)

    def test_malformed_tokens_cannot_reach_other_files(self) -> None:
        with self.client() as client:
            for token in ("..%2F..%2Fetc%2Fpasswd", "short", "A" * 42 + "."):
                self.assertEqual(client.get(f"/api/downloads/{token}").status_code, 404)

    def test_abandoned_leftovers_are_purged_after_grace(self) -> None:
        store = self.service.downloads.store
        leftover = self.store_dir() / f"{'B' * 43}.zip"
        leftover.write_bytes(b"orphan")
        store.purge()
        self.assertTrue(leftover.exists())
        old = time.time() - store.LEFTOVER_GRACE_SECONDS - 1
        os.utime(leftover, (old, old))
        store.purge()
        self.assertFalse(leftover.exists())


class HTTPRouteTests(ServerTestCase):
    def test_health_route_reports_service_status(self) -> None:
        with self.client() as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "luna-visia"})

    def test_metadata_route_lists_visible_images(self) -> None:
        self.service.hidden.set_hidden(["two.png"], True)
        with self.client() as client:
            images = client.get("/api/metadata").json()["images"]
        self.assertEqual([image["name"] for image in images], ["one.png"])

    def test_public_image_route_serves_by_stable_id(self) -> None:
        with self.client() as client:
            response = client.get("/api/public/images/image-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, (self.root / "one.png").read_bytes())
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_upload_route_returns_created_metadata(self) -> None:
        with self.client() as client:
            response = client.post(
                "/api/images", json={"name": "new.png", "content_base64": png_payload()}
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "created")

    def test_favorites_route_requires_boolean(self) -> None:
        with self.client() as client:
            response = client.post("/api/favorites", json={"names": ["one.png"], "favorite": "yes"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("favorite", response.json()["error"])

    def test_hidden_routes_require_session_token(self) -> None:
        with self.client() as client:
            self.assertEqual(client.get("/api/hidden/images").status_code, 401)
            token = client.post(
                "/api/hidden/setup", json={"password": "strong-password", "pin": "12345678"}
            ).json()["token"]
            headers = {TOKEN_HEADER: token}
            hidden = client.post("/api/hidden/hide", json={"names": ["one.png"]}, headers=headers)
            images = client.get("/api/hidden/images", headers=headers).json()["images"]
            updated = client.post(
                "/api/hidden/images/one.png/metadata",
                json={"add_tags": ["Private"]},
                headers=headers,
            )
            client.post("/api/hidden/logout", headers=headers)
            locked = client.get("/api/hidden/images", headers=headers)
        self.assertEqual(hidden.json()["status"], "hidden")
        self.assertEqual([image["name"] for image in images], ["one.png"])
        self.assertEqual(updated.json()["tags"], ["cat", "sofa", "private"])
        self.assertEqual(locked.status_code, 401)


class BrandingTests(ServerTestCase):
    def test_favicon_route_serves_icon(self) -> None:
        with self.client() as client:
            response = client.get("/favicon.ico")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/x-icon")
        with PillowImage.open(BytesIO(response.content)) as icon:
            self.assertEqual(icon.format, "ICO")

    def test_icon_route_serves_bundled_png_sizes(self) -> None:
        with self.client() as client:
            for size in ServerBranding.ICON_SIZES:
                response = client.get(f"/icons/icon-{size}.png")
                self.assertEqual(response.status_code, 200)
                with PillowImage.open(BytesIO(response.content)) as icon:
                    self.assertEqual(icon.size, (size, size))

    def test_icon_route_rejects_unknown_names(self) -> None:
        with self.client() as client:
            for name in ("icon-1.png", "favicon.ico", "..%2Fbranding.py"):
                self.assertNotEqual(client.get(f"/icons/{name}").status_code, 200)

    def test_initialize_advertises_server_icons(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
        with self.client() as client:
            response = client.post(
                "/mcp",
                json=request,
                headers={"Accept": "application/json, text/event-stream"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        data = next(
            line.removeprefix("data:").strip()
            for line in response.text.splitlines()
            if line.startswith("data:")
        )
        icons = json.loads(data)["result"]["serverInfo"]["icons"]
        self.assertEqual([icon["sizes"] for icon in icons], [["48x48"], ["96x96"]])
        for icon in icons:
            self.assertEqual(icon["mimeType"], "image/png")
            header, payload = icon["src"].split(",", 1)
            self.assertEqual(header, "data:image/png;base64")
            with PillowImage.open(BytesIO(base64.b64decode(payload))) as image:
                self.assertEqual(f"{image.width}x{image.height}", icon["sizes"][0])


if __name__ == "__main__":
    unittest.main()
