"""Tests for the Luna Visia image use cases."""

import base64
import tempfile
import textwrap
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from fakes import (
    FakeAnalyzer,
    FakeImageRepository,
    FakePublicURLRepository,
    c2pa_jpeg,
    c2pa_png,
    image_bytes,
    png_payload,
)

from app.core.config import AppConfig
from app.core.models import MetadataChange, MetadataRules
from app.services.application import VisiaService
from app.services.inspection import ImageInspector
from app.services.uploads import UploadLinkNotFound
from app.integrations.vision import OllamaVisionAnalyzer


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

    def test_analysis_graph_describes_and_checks_provenance_in_parallel(self):
        graph = self.service.indexer.graph.graph.get_graph()
        self.assertEqual({edge.target for edge in graph.edges if edge.source == "__start__"}, {"describe", "check_provenance"})
        self.assertEqual({edge.source for edge in graph.edges if edge.target == "tag"}, {"describe", "check_provenance"})

    def test_backfill_skips_current_records(self):
        self.visible.ingest("cat.png", png_payload())
        Image.new("RGB", (4, 3), "green").save(self.root / "dog.png")
        result = self.service.indexer.backfill()
        self.assertEqual(result["analyzed"], ["dog.png"])
        self.assertEqual(result["skipped_count"], 1)


class AIProvenanceTests(VisiaServiceTestCase):
    def test_inspector_reads_c2pa_generator_and_source_type(self):
        result = ImageInspector().inspect(c2pa_png())
        self.assertTrue(result["ai_generated"])
        self.assertEqual(result["c2pa"]["claim_generator"], "OpenAI Media Service API")
        [action] = result["c2pa"]["actions"]
        self.assertEqual(action["software_agent"], "ChatGPT gpt-image")
        self.assertEqual(action["digital_source_type"], "trainedAlgorithmicMedia")
        self.assertFalse(result["c2pa"]["signature_verified"])

    def test_inspector_joins_jpeg_app11_manifest_segments(self):
        result = ImageInspector().inspect(c2pa_jpeg())
        self.assertEqual(result["file"]["format"], "JPEG")
        self.assertEqual(result["c2pa"]["actions"][0]["software_agent"], "ChatGPT gpt-image")
        self.assertTrue(result["ai_generated"])

    def test_c2pa_without_ai_source_type_is_not_ai(self):
        result = ImageInspector().inspect(c2pa_png("digitalCapture", "Camera"))
        self.assertTrue(result["c2pa"]["present"])
        self.assertFalse(result["ai_generated"])

    def test_diffusion_parameters_and_generator_software_are_ai_signals(self):
        from PIL.PngImagePlugin import PngInfo

        info = PngInfo()
        info.add_text("parameters", "a cat, Steps: 20, Sampler: Euler")
        buffer = BytesIO()
        Image.new("RGB", (4, 3)).save(buffer, format="PNG", pnginfo=info)
        result = ImageInspector().inspect(buffer.getvalue())
        self.assertTrue(result["ai_generated"])
        self.assertIn("parameters", result["text_chunks"])

        exif = Image.Exif()
        exif[0x0131] = "Midjourney"
        buffer = BytesIO()
        Image.new("RGB", (4, 3)).save(buffer, format="JPEG", exif=exif)
        self.assertEqual(
            ImageInspector().inspect(buffer.getvalue())["ai_signals"], ["EXIF Software: Midjourney"]
        )

    def test_plain_image_has_no_ai_signals(self):
        result = ImageInspector().inspect(image_bytes())
        self.assertFalse(result["ai_generated"])
        self.assertIsNone(result["c2pa"])
        self.assertEqual(result["file"]["format"], "PNG")

    def test_ingesting_ai_image_adds_ai_info_tag(self):
        result = self.visible.upload("generated.png", c2pa_png())
        self.assertEqual(result["tags"], ["cat", "sofa", "ai info"])
        plain = self.visible.upload("photo.png", image_bytes())
        self.assertNotIn("ai info", plain["tags"])

    def test_system_tags_stay_within_tag_limit(self):
        rules = self.service.indexer.rules
        full = [f"tag {index}" for index in range(rules.max_tags)]
        tagged = rules.with_tags(full, ["ai modified", "ai info"])
        self.assertEqual(len(tagged), rules.max_tags)
        self.assertEqual(tagged[-2:], ["ai modified", "ai info"])

    def test_ai_info_tag_cannot_be_removed_from_ai_images(self):
        self.visible.upload("generated.png", c2pa_png())
        with self.assertRaisesRegex(ValueError, "cannot be removed"):
            self.visible.update_metadata("generated.png", MetadataChange(remove_tags=["ai info"]))
        with self.assertRaisesRegex(ValueError, "cannot be removed"):
            self.visible.update_metadata(
                "generated.png", MetadataChange(remove_tags=["cat", "ai info"])
            )
        self.assertEqual(self.repository.rows["generated.png"]["tags"], ["cat", "sofa", "ai info"])
        updated = self.visible.update_metadata("generated.png", MetadataChange(remove_tags=["cat"]))
        self.assertEqual(updated["tags"], ["sofa", "ai info"])

    def test_editing_untagged_ai_image_restores_ai_info_tag(self):
        self.visible.upload("generated.png", c2pa_png())
        self.repository.rows["generated.png"]["tags"] = ["cat"]
        updated = self.visible.update_metadata("generated.png", MetadataChange(caption="A cat."))
        self.assertEqual(updated["tags"], ["cat", "ai info"])

    def test_ai_info_is_enforced_for_hidden_images_too(self):
        self.visible.upload("generated.png", c2pa_png())
        self.repository.rows["generated.png"]["hidden"] = True
        with self.assertRaisesRegex(ValueError, "cannot be removed"):
            self.hidden.update_metadata("generated.png", MetadataChange(remove_tags=["ai info"]))

    def test_ai_info_can_be_added_and_removed_on_other_images(self):
        self.visible.upload("photo.png", image_bytes())
        self.visible.update_metadata("photo.png", MetadataChange(add_tags=["ai info"]))
        updated = self.visible.update_metadata("photo.png", MetadataChange(remove_tags=["ai info"]))
        self.assertNotIn("ai info", updated["tags"])

    def test_backfill_adds_ai_info_tag_to_current_records(self):
        self.visible.upload("generated.png", c2pa_png())
        self.repository.rows["generated.png"]["tags"] = ["cat"]
        result = self.service.indexer.backfill()
        self.assertEqual(result["ai_tagged"], ["generated.png"])
        self.assertEqual(result["analyzed"], [])
        self.assertEqual(self.repository.rows["generated.png"]["tags"], ["cat", "ai info"])
        self.assertEqual(self.service.indexer.backfill()["ai_tagged_count"], 0)

    def test_metadata_summary_names_generator(self):
        self.visible.upload("generated.png", c2pa_png())
        [item] = self.visible.metadata_page(10, 0, 100)["images"]
        self.assertEqual(item["embedded"]["generator"], "ChatGPT gpt-image")
        self.assertTrue(item["embedded"]["ai_generated"])
        self.assertIn("ai info", item["tags"])

    def test_metadata_page_reports_unreadable_files(self):
        self.visible.upload("generated.png", c2pa_png())
        (self.root / "generated.png").unlink()
        [item] = self.visible.metadata_page(10, 0, 100)["images"]
        self.assertIn("error", item["embedded"])

    def test_metadata_combines_record_and_embedded_details(self):
        self.visible.upload("generated.png", c2pa_png())
        metadata = self.visible.metadata("generated.png")
        self.assertEqual(metadata["caption"], "A tabby cat on a sofa.")
        self.assertEqual(metadata["file"]["width"], 4)
        self.assertTrue(metadata["ai_generated"])
        self.repository.rows["generated.png"]["hidden"] = True
        with self.assertRaises(PermissionError):
            self.visible.metadata("generated.png")


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

    def test_stats_summarize_visible_library_only(self):
        self.visible.upload("generated.png", c2pa_png())
        self.visible.ingest("cat.png", png_payload())
        self.visible.ingest("cat.png", png_payload("blue"), replace=True)
        self.visible.ingest("secret.png", png_payload("green"))
        self.visible.set_favorite(["cat.png"], True)
        self.hidden.set_hidden(["secret.png"], True)
        stats = self.visible.stats(top_tags=2)
        rows = self.visible.list()
        self.assertEqual(stats["total_images"], 2)
        self.assertEqual(stats["favorite_count"], 1)
        self.assertEqual(stats["ai_generated_count"], 1)
        self.assertEqual(stats["ai_modified_count"], 1)
        self.assertEqual(stats["total_size_bytes"], sum(row["size_bytes"] for row in rows))
        self.assertEqual(stats["formats"], {"png": 2})
        self.assertEqual(stats["top_tags"], [{"tag": "cat", "count": 2}, {"tag": "sofa", "count": 2}])
        self.assertEqual(stats["unique_tag_count"], 4)

    def test_stats_for_empty_library(self):
        stats = self.visible.stats()
        self.assertEqual(stats["total_images"], 0)
        self.assertEqual(stats["total_size"], "0 B")
        self.assertIsNone(stats["largest_image"])
        self.assertIsNone(stats["newest_updated_at"])

    def test_human_bytes_picks_readable_unit(self):
        self.assertEqual(self.visible.human_bytes(512), "512 B")
        self.assertEqual(self.visible.human_bytes(1536), "1.5 KiB")
        self.assertEqual(self.visible.human_bytes(3 * 1024**3), "3.0 GiB")


class ChatUploadTests(VisiaServiceTestCase):
    def test_tolerant_base64_variants_are_accepted(self):
        raw = image_bytes("PNG")
        standard = base64.b64encode(raw).decode()
        variants = {
            "wrapped": "\n".join(textwrap.wrap(standard, 76)),
            "unpadded": standard.rstrip("="),
            "urlsafe": base64.urlsafe_b64encode(raw).decode(),
            "data_url": f"data:image/png;base64,{standard}",
        }
        for index, (label, payload) in enumerate(variants.items()):
            with self.subTest(label):
                self.repository.rows.clear()
                result = self.visible.ingest(f"cat_{index}.png", payload)
                self.assertEqual((self.root / result["name"]).read_bytes(), raw)

    def test_upload_corrects_wrong_or_missing_extension(self):
        cases = [("photo.png", "photo.jpg"), ("sunset", "sunset.jpg"), ("/mnt/data/beach.JPG", "beach.JPG")]
        for index, (requested, expected) in enumerate(cases):
            with self.subTest(requested):
                result = self.visible.upload(requested, image_bytes("JPEG", size=(4 + index, 3)))
                self.assertEqual(result["name"], expected)
                self.assertEqual(result["status"], "created")
                self.assertTrue((self.root / expected).is_file())

    def test_upload_without_name_uses_default(self):
        self.assertEqual(self.visible.upload(None, image_bytes("WEBP"))["name"], "image.webp")

    def test_upload_reports_requested_name_when_changed(self):
        result = self.visible.upload("photo.png", image_bytes("JPEG"))
        self.assertEqual(result["requested_name"], "photo.png")

    def test_upload_numbers_taken_names(self):
        self.visible.upload("cat.png", image_bytes("PNG", "red"))
        result = self.visible.upload("cat.png", image_bytes("PNG", "blue"))
        self.assertEqual(result["name"], "cat_2.png")

    def test_upload_reports_exact_duplicate_without_second_copy(self):
        data = image_bytes("PNG")
        first = self.visible.upload("cat.png", data)
        again = self.visible.upload("retry.png", data)
        self.assertEqual(again["status"], "duplicate")
        self.assertEqual(again["name"], first["name"])
        self.assertFalse((self.root / "retry.png").exists())

    def test_hidden_duplicate_is_not_revealed(self):
        data = image_bytes("PNG")
        self.visible.upload("secret.png", data)
        self.hidden.set_hidden(["secret.png"], True)
        result = self.visible.upload("public.png", data)
        self.assertEqual((result["status"], result["name"]), ("created", "public.png"))

    def test_phone_mpo_jpeg_is_accepted(self):
        buffer = BytesIO()
        frames = [Image.new("RGB", (8, 6), color) for color in ("red", "blue")]
        frames[0].save(buffer, format="MPO", save_all=True, append_images=frames[1:])
        result = self.visible.upload("phone.jpg", buffer.getvalue())
        self.assertEqual(result["name"], "phone.jpg")

    def test_unsupported_or_invalid_content_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not supported"):
            self.visible.upload("scan.tiff", image_bytes("TIFF"))
        with self.assertRaisesRegex(ValueError, "not a valid image"):
            self.visible.upload("notes.png", b"hello")


class UploadLinkTests(VisiaServiceTestCase):
    def test_link_is_single_use(self):
        uploads = self.service.uploads
        link = uploads.create_link("cat")
        self.assertTrue(link["upload_url"].startswith("https://vault.example.test/vault/api/uploads/"))
        token = link["upload_url"].rsplit("/", 1)[-1]
        result = uploads.receive(token, image_bytes("PNG"))
        self.assertEqual((result["status"], result["name"]), ("created", "cat.png"))
        with self.assertRaises(UploadLinkNotFound):
            uploads.receive(token, image_bytes("PNG", "blue"))

    def test_failed_upload_can_retry_on_same_link(self):
        uploads = self.service.uploads
        token = uploads.create_link("cat.png")["upload_url"].rsplit("/", 1)[-1]
        with self.assertRaises(ValueError):
            uploads.receive(token, b"not an image")
        self.assertEqual(uploads.receive(token, image_bytes("PNG"))["status"], "created")

    def test_expired_link_is_rejected(self):
        uploads = self.service.uploads
        uploads.store.ttl_seconds = 0
        token = uploads.create_link("cat.png")["upload_url"].rsplit("/", 1)[-1]
        with self.assertRaises(UploadLinkNotFound):
            uploads.receive(token, image_bytes("PNG"))

    def test_replace_link_requires_existing_visible_image(self):
        uploads = self.service.uploads
        with self.assertRaises(FileNotFoundError):
            uploads.create_link("missing.png", replace=True)
        self.visible.ingest("cat.png", png_payload())
        token = uploads.create_link("cat.png", replace=True)["upload_url"].rsplit("/", 1)[-1]
        result = uploads.receive(token, image_bytes("PNG", "blue"))
        self.assertEqual(result["status"], "modified")
        self.assertIn("ai modified", result["tags"])


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
