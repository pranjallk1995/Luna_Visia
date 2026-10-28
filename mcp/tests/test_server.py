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
from mcp.server.fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from fakes import (
    FakeAnalyzer,
    FakeImageRepository,
    FakePublicURLRepository,
    image_bytes,
    png_payload,
)

from app.transport.branding import ServerBranding
from app.core.config import AppConfig
from app.services.downloads import DownloadStore
from app.transport.http_routes import VisiaHTTPRoutes
from app.transport.mcp_tools import VisiaMCPTools
from app.services.media import ImageArchive, PreviewRenderer, display_name
from app.integrations.remote import ImageSourceResolver
from app.services.application import VisiaService


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
        )
        VisiaHTTPRoutes(self.server, self.service, self.branding, "x" * 32)
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

    def test_view_image_returns_image_with_caption_and_link(self) -> None:
        image, summary = self.call("view_image", {"name": "one.png"})
        content = image.to_image_content()
        self.assertEqual(content.mimeType, "image/jpeg")
        self.assertTrue(base64.b64decode(content.data))
        [item] = summary["previewed"]
        self.assertEqual(item["name"], "one.png")
        self.assertTrue(item["public_url"].endswith("/api/public/images/image-1"))
        self.assertNotIn("note", summary)
        self.assertEqual(set(item), {"number", "name", "caption", "public_url"})

    def test_get_image_url_returns_visible_https_location(self) -> None:
        self.assertEqual(
            self.call("get_image_url", {"name": "one.png"}),
            {
                "name": "one.png",
                "url": "https://vault.example.test/vault/api/public/images/image-1",
            },
        )

    def test_get_image_metadata_returns_stored_and_embedded_fields(self) -> None:
        metadata = self.call("get_image_metadata", {"name": "one.png"})
        self.assertEqual(metadata["name"], "one.png")
        self.assertEqual(metadata["tags"], ["cat", "sofa"])
        self.assertEqual((metadata["file"]["width"], metadata["file"]["height"]), (1200, 800))
        self.assertFalse(metadata["ai_generated"])

    def test_get_image_metadata_rejects_hidden_images(self) -> None:
        self.repository.rows["one.png"]["hidden"] = True
        with self.assertRaises(ToolError):
            self.call("get_image_metadata", {"name": "one.png"})

    def test_list_image_metadata_pages_compact_summaries(self) -> None:
        first = self.call("list_image_metadata", {"limit": 1})
        self.assertEqual((first["count"], first["next_offset"]), (2, 1))
        [item] = first["images"]
        self.assertEqual(item["embedded"]["format"], "PNG")
        self.assertFalse(item["embedded"]["ai_generated"])
        self.assertNotIn("exif", item)
        second = self.call("list_image_metadata", {"limit": 1, "offset": 1})
        self.assertIsNone(second["next_offset"])

    def test_list_image_metadata_batch_reports_unavailable_names(self) -> None:
        self.repository.rows["two.png"]["hidden"] = True
        result = self.call(
            "list_image_metadata", {"names": ["one.png", "two.png", "missing.png"], "detail": "full"}
        )
        self.assertEqual([item["name"] for item in result["images"]], ["one.png"])
        self.assertIn("exif", result["images"][0])
        self.assertEqual(result["unavailable"], ["two.png", "missing.png"])

    def test_list_image_metadata_caps_full_detail_batches(self) -> None:
        with self.assertRaises(ToolError):
            self.call("list_image_metadata", {"names": [f"{i}.png" for i in range(21)], "detail": "full"})

    def test_update_images_metadata_applies_each_change_and_reports_failures(self) -> None:
        result = self.call(
            "update_images_metadata",
            {
                "updates": [
                    {"name": "one.png", "add_tags": ["food"], "remove_tags": ["sofa"]},
                    {"name": "two.png", "caption": "A blue square."},
                    {"name": "missing.png", "add_tags": ["food"]},
                ]
            },
        )
        self.assertEqual((result["updated_count"], result["failed_count"]), (2, 1))
        self.assertEqual(self.repository.rows["one.png"]["tags"], ["cat", "food"])
        self.assertEqual(self.repository.rows["two.png"]["caption"], "A blue square.")
        self.assertEqual(result["failed"][0]["name"], "missing.png")

    def test_update_images_metadata_rejects_capitalized_tags_without_changes(self) -> None:
        with self.assertRaises(ToolError):
            self.call(
                "update_images_metadata",
                {"updates": [{"name": "one.png", "add_tags": ["food"]}, {"name": "two.png", "add_tags": ["Food"]}]},
            )
        self.assertEqual(self.repository.rows["one.png"]["tags"], ["cat", "sofa"])

    def test_batch_update_reports_ai_info_removal_as_failure(self) -> None:
        from fakes import c2pa_png

        self.service.visible.upload("generated.png", c2pa_png())
        result = self.call(
            "update_images_metadata",
            {
                "updates": [
                    {"name": "generated.png", "remove_tags": ["ai info"]},
                    {"name": "one.png", "add_tags": ["food"]},
                ]
            },
        )
        self.assertEqual(result["updated_count"], 1)
        self.assertIn("cannot be removed", result["failed"][0]["error"])

    def test_batch_tool_schemas_are_inline(self) -> None:
        for name in ("update_images_metadata", "rename_images"):
            schema = json.dumps(self.server._tool_manager.get_tool(name).parameters)
            self.assertNotIn("$ref", schema)

    def test_rename_images_applies_in_order(self) -> None:
        result = self.call(
            "rename_images",
            {
                "renames": [
                    {"name": "one.png", "new_name": "first.png"},
                    {"name": "two.png", "new_name": "one.png"},
                    {"name": "first.png", "new_name": "bad name.png"},
                ]
            },
        )
        self.assertEqual([item["name"] for item in result["renamed"]], ["first.png", "one.png"])
        self.assertTrue(result["renamed"][0]["public_url"].endswith("/image-1"))
        self.assertEqual(result["failed"][0]["name"], "first.png")
        self.assertTrue((self.root / "first.png").is_file())

    def test_batch_updates_are_capped(self) -> None:
        with self.assertRaises(ToolError):
            self.call("rename_images", {"renames": [{"name": f"{i}.png", "new_name": f"n{i}.png"} for i in range(101)]})
        with self.assertRaises(ToolError):
            self.call("update_images_metadata", {"updates": []})

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

    def test_get_gallery_stats_counts_visible_images(self) -> None:
        self.call("set_favorite_images", {"names": ["one.png"], "favorite": True})
        self.service.hidden.set_hidden(["two.png"], True)
        stats = self.call("get_gallery_stats", {})
        self.assertEqual((stats["total_images"], stats["favorite_count"]), (1, 1))
        self.assertEqual(stats["largest_image"]["name"], "one.png")

    def test_fetch_images_rejects_hidden_images(self) -> None:
        self.service.hidden.set_hidden(["one.png"], True)
        with self.assertRaises(Exception):
            self.call("fetch_images", {"names": ["one.png"]})

    def test_display_skips_unavailable_names_instead_of_failing(self) -> None:
        self.service.hidden.set_hidden(["two.png"], True)
        image, summary = self.call(
            "fetch_images", {"names": ["one.png", "renamed_away.png", "two.png"]}
        )
        self.assertEqual([item["name"] for item in summary["previewed"]], ["one.png"])
        self.assertEqual(summary["unavailable"], ["renamed_away.png", "two.png"])
        self.assertTrue(image.to_image_content().data)

    def test_display_numbers_tiles_in_request_order(self) -> None:
        _, summary = self.call("fetch_images", {"names": ["two.png", "one.png"]})
        self.assertEqual(
            [(item["number"], item["name"]) for item in summary["previewed"]],
            [(1, "two.png"), (2, "one.png")],
        )
        self.assertIn("contact sheet", summary["note"])

    def test_display_is_not_limited_by_original_file_sizes(self) -> None:
        self.service.visible.max_fetch_bytes = 1
        image, _ = self.call("fetch_images", {"names": ["one.png", "two.png"]})
        self.assertTrue(image.to_image_content().data)

    def test_display_caps_image_count_and_points_to_zip(self) -> None:
        names = [f"image_{index}.png" for index in range(self.config.max_fetch_images + 1)]
        with self.assertRaisesRegex(Exception, "zip"):
            self.call("fetch_images", {"names": names})

    def test_list_images_pages_compact_summaries(self) -> None:
        for index in range(3):
            self.service.visible.ingest(f"extra_{index}.png", png_payload("red", (5 + index, 3)))
        first = self.call("list_images", {"limit": 2})
        self.assertEqual((first["count"], first["offset"], first["next_offset"]), (5, 0, 2))
        self.assertEqual(
            set(first["images"][0]),
            {"name", "caption", "tags", "favorite", "size_bytes", "updated_at", "public_url", "analysis_model"},
        )
        last = self.call("list_images", {"limit": 2, "offset": 4})
        self.assertEqual((len(last["images"]), last["next_offset"]), (1, None))

    def test_list_images_can_include_preview(self) -> None:
        listing, image, preview = self.call("list_images", {"include_preview": True})
        self.assertEqual(listing["count"], 2)
        self.assertTrue(image.to_image_content().data)
        self.assertEqual(len(preview["preview"]["previewed"]), 2)

    def test_search_returns_links_and_optional_preview(self) -> None:
        results = self.call("search_images", {"query": "cat"})
        self.assertTrue(all(item["public_url"] for item in results["results"]))
        self.assertIsNone(results["next_offset"])
        listing, image, preview = self.call("search_images", {"query": "cat", "include_preview": True})
        self.assertEqual(len(preview["preview"]["previewed"]), listing["count"])

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


class LowercaseTagToolTests(ServerTestCase):
    """MCP tools reject capitalized tags instead of silently lowercasing them."""

    def tags_of(self, name: str) -> list[str]:
        return self.repository.get(name)["tags"]

    def test_capitalized_add_tags_are_rejected_without_changes(self) -> None:
        before = self.tags_of("one.png")

        with self.assertRaisesRegex(ToolError, "add_tags must be lowercase.*'dinner'"):
            self.call("update_image_metadata", {"name": "one.png", "add_tags": ["food", "Dinner"]})

        self.assertEqual(self.tags_of("one.png"), before)

    def test_capitalized_remove_tags_are_rejected(self) -> None:
        with self.assertRaisesRegex(ToolError, "remove_tags must be lowercase"):
            self.call("update_image_metadata", {"name": "one.png", "remove_tags": ["CAT"]})

    def test_capitalized_search_tags_are_rejected(self) -> None:
        with self.assertRaisesRegex(ToolError, "tags must be lowercase"):
            self.call("search_images", {"tags": ["Cat"]})

    def test_lowercase_tags_are_accepted(self) -> None:
        result = self.call("update_image_metadata", {"name": "one.png", "add_tags": ["dinner"]})

        self.assertIn("dinner", result["tags"])
        self.assertIsInstance(self.call("search_images", {"tags": ["dinner"]}), dict)


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

    def test_contact_sheets_stay_near_square_and_readable(self) -> None:
        renderer = PreviewRenderer(768)
        for count, columns in ((2, 2), (8, 3), (20, 5)):
            with self.subTest(count=count):
                jpeg = renderer.render([self.root / "one.png"] * count)
                with PillowImage.open(BytesIO(jpeg)) as image:
                    self.assertEqual(image.width, PreviewRenderer.SHEET_WIDTH // columns * columns)
                    self.assertLessEqual(image.height, 1600)

    def test_transparent_palette_images_are_flattened(self) -> None:
        path = self.root / "logo.png"
        PillowImage.new("P", (20, 20)).save(path, transparency=0)
        with PillowImage.open(BytesIO(PreviewRenderer(768).render([path]))) as image:
            self.assertEqual(image.mode, "RGB")

    def test_display_name_drops_upload_prefix(self) -> None:
        self.assertEqual(display_name(f"{'a' * 32}_Big_Ben.png"), "Big_Ben.png")
        self.assertEqual(display_name("plain.png"), "plain.png")

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
        old = time.time() - AppConfig.DOWNLOAD_LEFTOVER_GRACE_SECONDS - 1
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

    def test_hidden_routes_are_available_to_the_oauth_gated_ui(self) -> None:
        headers = {"Authorization": f"Bearer {'x' * 32}"}
        with self.client() as client:
            self.assertEqual(client.get("/api/hidden/images").status_code, 401)
            hidden = client.post(
                "/api/hidden/hide", json={"names": ["one.png"]}, headers=headers
            )
            images = client.get("/api/hidden/images", headers=headers).json()["images"]
            updated = client.post(
                "/api/hidden/images/one.png/metadata",
                json={"add_tags": ["Private"]},
                headers=headers,
            )
        self.assertEqual(hidden.json()["status"], "hidden")
        self.assertEqual([image["name"] for image in images], ["one.png"])
        self.assertEqual(updated.json()["tags"], ["cat", "sofa", "private"])


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

    def test_home_page_links_the_icons(self) -> None:
        with self.client() as client:
            page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        self.assertIn('href="/icons/icon-192.png"', page.text)
        self.assertIn('href="/favicon.ico"', page.text)

    def test_public_url_icons_are_listed_before_embedded_ones(self) -> None:
        icons = ServerBranding("https://mcp.example.com/").mcp_icons()
        self.assertEqual(
            [icon.src for icon in icons[:4]],
            [f"https://mcp.example.com/icons/icon-{size}.png" for size in ServerBranding.ICON_SIZES],
        )
        self.assertTrue(icons[4].src.startswith("data:image/png;base64,"))

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
