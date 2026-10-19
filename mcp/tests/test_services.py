"""Tests for the Luna Visia image use cases."""

import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from fakes import FakeAnalyzer, FakeImageRepository, FakePublicURLRepository, png_payload

from app.config import AppConfig
from app.models import MetadataChange, MetadataRules
from app.services import VisiaService
from app.vision import OllamaVisionAnalyzer


class VisiaServiceTestCase(unittest.TestCase):
    """Build a real service graph over in-memory repositories."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.downloads_temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repository = FakeImageRepository()
        self.config = AppConfig(
            image_dir=self.root,
            database_url="postgresql://test",
            ollama_url="http://ollama:11434",
            vision_model="test-vision",
            public_base_url="https://vault.example.test/vault",
            download_dir=Path(self.downloads_temp.name),
        )
        self.service = VisiaService.create(
            self.config,
            self.repository,
            FakePublicURLRepository(self.repository),
            FakeAnalyzer(),
        )
        self.visible = self.service.visible
        self.hidden = self.service.hidden

    def tearDown(self):
        self.temp.cleanup()
        self.downloads_temp.cleanup()


class IngestTests(VisiaServiceTestCase):
    def test_vision_preprocessing_bounds_dimensions(self):
        raw = BytesIO()
        Image.new("RGB", (2400, 1600), "blue").save(raw, format="PNG")
        prepared = OllamaVisionAnalyzer.prepare_image(raw.getvalue())
        with Image.open(BytesIO(prepared)) as image:
            self.assertLessEqual(max(image.size), 1024)
            self.assertEqual(image.format, "JPEG")

    def test_ingest_persists_generated_metadata(self):
        result = self.visible.ingest("cat.png", png_payload())
        self.assertEqual(result["caption"], "A tabby cat on a sofa.")
        self.assertEqual(result["tags"], ["cat", "sofa"])
        self.assertTrue((self.root / "cat.png").is_file())
        self.assertEqual(
            result["public_url"],
            "https://vault.example.test/vault/api/public/images/image-1",
        )

    def test_ingest_rejects_existing_name_without_replace(self):
        self.visible.ingest("cat.png", png_payload())
        with self.assertRaises(FileExistsError):
            self.visible.ingest("cat.png", png_payload("blue"))

    def test_ingest_rejects_mismatched_format(self):
        with self.assertRaisesRegex(ValueError, "filename expects JPEG"):
            self.visible.ingest("cat.jpg", png_payload())
        self.assertFalse((self.root / "cat.jpg").exists())

    def test_ingest_rolls_file_back_when_metadata_persistence_fails(self):
        self.repository.upsert = lambda record, base: (_ for _ in ()).throw(
            RuntimeError("database unavailable")
        )
        with self.assertRaises(RuntimeError):
            self.visible.ingest("cat.png", png_payload())
        self.assertFalse((self.root / "cat.png").exists())

    def test_replacing_image_adds_ai_modified_tag(self):
        self.visible.ingest("cat.png", png_payload())
        result = self.visible.ingest("cat.png", png_payload("blue"), replace=True)
        self.assertEqual(result["tags"], ["cat", "sofa", "ai modified"])
        self.assertEqual(result["tags"].count("ai modified"), 1)

    def test_backfill_skips_current_records(self):
        self.visible.ingest("cat.png", png_payload())
        Image.new("RGB", (4, 3), "green").save(self.root / "dog.png")
        result = self.service.indexer.backfill()
        self.assertEqual(result["analyzed"], ["dog.png"])
        self.assertEqual(result["skipped_count"], 1)


class PublicURLTests(VisiaServiceTestCase):
    def test_public_url_rejects_hidden_images(self):
        self.visible.ingest("cat.png", png_payload())
        self.repository.rows["cat.png"]["hidden"] = True
        with self.assertRaises(PermissionError):
            self.visible.public_url("cat.png")

    def test_public_path_resolves_stable_image_id(self):
        self.visible.ingest("cat.png", png_payload())
        self.assertEqual(self.visible.public_path("image-1").name, "cat.png")

    def test_public_base_url_update_regenerates_stored_urls(self):
        self.visible.ingest("cat.png", png_payload())
        result = self.service.urls.update("https://new-device.example.test/vault/")
        self.assertEqual(
            result,
            {
                "status": "public_base_url_updated",
                "public_base_url": "https://new-device.example.test/vault",
                "updated_image_count": 1,
            },
        )
        self.assertEqual(
            self.repository.rows["cat.png"]["public_url"],
            "https://new-device.example.test/vault/api/public/images/image-1",
        )

    def test_public_base_url_rejects_non_https_values(self):
        with self.assertRaises(ValueError):
            self.service.urls.update("http://localhost:18000/vault")


class RenameAndRemoveTests(VisiaServiceTestCase):
    def test_remove_deletes_file_and_metadata(self):
        self.visible.ingest("cat.png", png_payload())
        self.visible.remove("cat.png")
        self.assertFalse((self.root / "cat.png").exists())
        self.assertNotIn("cat.png", self.repository.rows)

    def test_rename_preserves_file_and_metadata(self):
        original = self.visible.ingest("cat.png", png_payload())
        self.visible.set_favorite(["cat.png"], True)
        result = self.visible.rename("cat.png", "favorite_cat.png")
        self.assertEqual(result["status"], "renamed")
        self.assertEqual(result["old_name"], "cat.png")
        self.assertEqual(result["name"], "favorite_cat.png")
        self.assertEqual(result["caption"], original["caption"])
        self.assertEqual(result["tags"], original["tags"])
        self.assertTrue(result["favorite"])
        self.assertFalse((self.root / "cat.png").exists())
        self.assertTrue((self.root / "favorite_cat.png").is_file())

    def test_rename_rejects_disallowed_filename_characters(self):
        self.visible.ingest("cat.png", png_payload())
        for new_name in ("favorite cat.png", "favorite-cat.png", "cat!.png"):
            with self.subTest(new_name=new_name):
                with self.assertRaisesRegex(ValueError, "only letters, numbers, and underscores"):
                    self.visible.rename("cat.png", new_name)
        self.assertTrue((self.root / "cat.png").is_file())
        self.assertIn("cat.png", self.repository.rows)

    def test_rename_rejects_collision_without_changing_source(self):
        self.visible.ingest("cat.png", png_payload())
        self.visible.ingest("dog.png", png_payload("blue"))
        with self.assertRaises(FileExistsError):
            self.visible.rename("cat.png", "dog.png")
        self.assertTrue((self.root / "cat.png").is_file())
        self.assertIn("cat.png", self.repository.rows)

    def test_rename_rejects_extension_change(self):
        self.visible.ingest("cat.png", png_payload())
        with self.assertRaises(ValueError):
            self.visible.rename("cat.png", "cat.jpg")
        self.assertTrue((self.root / "cat.png").is_file())

    def test_rename_rejects_unsafe_destination_name(self):
        self.visible.ingest("cat.png", png_payload())
        with self.assertRaises(ValueError):
            self.visible.rename("cat.png", "../cat.png")
        self.assertTrue((self.root / "cat.png").is_file())

    def test_rename_rejects_hidden_image(self):
        self.visible.ingest("cat.png", png_payload())
        self.hidden.set_hidden(["cat.png"], True)
        with self.assertRaises(PermissionError):
            self.visible.rename("cat.png", "renamed.png")
        self.assertTrue((self.root / "cat.png").is_file())

    def test_rename_rolls_file_back_when_metadata_update_fails(self):
        self.visible.ingest("cat.png", png_payload())
        self.repository.rename = lambda name, new_name: (_ for _ in ()).throw(
            RuntimeError("database unavailable")
        )
        with self.assertRaises(RuntimeError):
            self.visible.rename("cat.png", "renamed.png")
        self.assertTrue((self.root / "cat.png").is_file())
        self.assertFalse((self.root / "renamed.png").exists())
        self.assertIn("cat.png", self.repository.rows)

    def test_remove_restores_file_when_metadata_delete_fails(self):
        self.visible.ingest("cat.png", png_payload())
        self.repository.delete = lambda name: (_ for _ in ()).throw(
            RuntimeError("database unavailable")
        )
        with self.assertRaises(RuntimeError):
            self.visible.remove("cat.png")
        self.assertTrue((self.root / "cat.png").is_file())


class MetadataTests(VisiaServiceTestCase):
    def test_natural_count_query_uses_metadata(self):
        self.visible.ingest("cat.png", png_payload())
        result = self.service.search.search("how many images are about cats?", None, False, 50, 0)
        self.assertEqual(result["normalized_query"], "cats")
        self.assertEqual(result["count"], 1)

    def test_update_metadata_adds_tag_without_reanalysis(self):
        self.visible.ingest("cat.png", png_payload())
        result = self.visible.update_metadata("cat.png", MetadataChange(add_tags=["Pet Portrait"]))
        self.assertEqual(result["caption"], "A tabby cat on a sofa.")
        self.assertEqual(result["tags"], ["cat", "sofa", "pet portrait"])

    def test_update_metadata_allows_manual_tag_after_sixteen_model_tags(self):
        self.visible.ingest("cat.png", png_payload())
        self.repository.rows["cat.png"]["tags"] = [f"generated tag {index}" for index in range(16)]
        result = self.visible.update_metadata("cat.png", MetadataChange(add_tags=["favorite"]))
        self.assertEqual(len(result["tags"]), 17)
        self.assertEqual(result["tags"][-1], "favorite")

    def test_update_metadata_changes_caption_and_removes_tag(self):
        self.visible.ingest("cat.png", png_payload())
        result = self.visible.update_metadata(
            "cat.png", MetadataChange(caption="A relaxed indoor cat.", remove_tags=["sofa"])
        )
        self.assertEqual(result["caption"], "A relaxed indoor cat.")
        self.assertEqual(result["tags"], ["cat"])

    def test_update_metadata_requires_a_change(self):
        self.visible.ingest("cat.png", png_payload())
        with self.assertRaisesRegex(ValueError, "Provide a caption"):
            self.visible.update_metadata("cat.png", MetadataChange())

    def test_update_metadata_rejects_hidden_images(self):
        self.visible.ingest("cat.png", png_payload())
        self.hidden.set_hidden(["cat.png"], True)
        with self.assertRaises(PermissionError):
            self.visible.update_metadata("cat.png", MetadataChange(add_tags=["private"]))

    def test_update_hidden_metadata_changes_caption_and_tags(self):
        self.visible.ingest("cat.png", png_payload())
        self.hidden.set_hidden(["cat.png"], True)
        result = self.hidden.update_metadata(
            "cat.png", MetadataChange(caption="A private indoor cat.", add_tags=["Private"])
        )
        self.assertEqual(result["caption"], "A private indoor cat.")
        self.assertEqual(result["tags"], ["cat", "sofa", "private"])
        self.assertTrue(result["hidden"])

    def test_metadata_rules_normalize_and_bound_tags(self):
        rules = MetadataRules(max_tags=2, search_stop_words=frozenset())
        self.assertEqual(rules.normalize_tags(["Cat!", "cat", "  Big   Dog ", "x"]), ["cat", "big dog"])
        with self.assertRaises(ValueError):
            rules.normalize_tags(["!!!"])


class HiddenAndFavoriteTests(VisiaServiceTestCase):
    def test_authenticated_hidden_remove_deletes_file_and_metadata(self):
        self.visible.ingest("cat.png", png_payload())
        self.hidden.set_hidden(["cat.png"], True)
        result = self.hidden.remove("cat.png")
        self.assertEqual(result["status"], "deleted")
        self.assertFalse((self.root / "cat.png").exists())
        self.assertNotIn("cat.png", self.repository.rows)

    def test_hidden_remove_rejects_visible_images(self):
        self.visible.ingest("cat.png", png_payload())
        with self.assertRaisesRegex(PermissionError, "Only hidden images"):
            self.hidden.remove_many(["cat.png"])
        self.assertTrue((self.root / "cat.png").is_file())

    def test_hidden_images_are_excluded_and_protected(self):
        self.visible.ingest("cat.png", png_payload())
        self.hidden.set_hidden(["cat.png"], True)
        self.assertEqual(self.visible.list(), [])
        self.assertEqual(len(self.hidden.list()), 1)
        with self.assertRaises(PermissionError):
            self.visible.path("cat.png")
        with self.assertRaises(PermissionError):
            self.visible.remove("cat.png")
        self.assertEqual(self.service.search.search("cats", None, False, 50, 0)["count"], 0)

    def test_favorites_are_persisted_and_hidden_images_are_excluded(self):
        self.visible.ingest("cat.png", png_payload())
        result = self.visible.set_favorite(["cat.png"], True)
        self.assertEqual(result["status"], "favorited")
        self.assertEqual(result["names"], ["cat.png"])
        self.assertEqual([item["name"] for item in self.visible.favorites()], ["cat.png"])
        self.hidden.set_hidden(["cat.png"], True)
        self.assertEqual(self.visible.favorites(), [])


class SelectionTests(VisiaServiceTestCase):
    def setUp(self):
        super().setUp()
        self.visible.ingest("one.png", png_payload("red"))
        self.visible.ingest("two.png", png_payload("blue"))

    def test_selection_deduplicates_in_requested_order(self):
        paths = self.visible.select_paths(["two.png", "one.png", "two.png"])
        self.assertEqual([path.name for path in paths], ["two.png", "one.png"])

    def test_selection_rejects_hidden_images(self):
        self.hidden.set_hidden(["one.png"], True)
        with self.assertRaises(PermissionError):
            self.visible.select_paths(["one.png"])

    def test_selection_rejects_oversized_batches(self):
        limit = self.config.max_fetch_images
        with self.assertRaisesRegex(ValueError, str(limit)):
            self.visible.select_paths([f"image-{index}.png" for index in range(limit + 1)])

    def test_selection_rejects_non_string_names(self):
        with self.assertRaisesRegex(ValueError, "must be a string"):
            self.visible.select_paths([["one.png"]])


if __name__ == "__main__":
    unittest.main()
