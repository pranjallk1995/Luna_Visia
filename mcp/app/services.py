"""Luna Visia use cases, independent of the HTTP and MCP transports."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import AppConfig
from .downloads import DownloadStore, DownloadTicket
from .media import ImagePayloadDecoder
from .models import ImageAnalysis, ImageRecord, MetadataChange, MetadataRules
from .repositories import ImageRepository, PublicURLRepository, Row
from .storage import ImageStorage
from .vision import OllamaVisionAnalyzer


def unique_names(names: list[str], empty_message: str) -> list[str]:
    """Return ``names`` deduplicated in order, rejecting an empty selection."""
    if not isinstance(names, list) or not names:
        raise ValueError(empty_message)
    return list(dict.fromkeys(names))


class PublicURLService:
    """Validate and persist the HTTPS origin that every image URL derives from."""

    def __init__(self, repository: PublicURLRepository) -> None:
        self.repository = repository

    @staticmethod
    def validate(public_base_url: str) -> str:
        """Normalize and validate a public HTTPS origin and optional path prefix."""
        normalized = public_base_url.strip().rstrip("/")
        parsed = urlparse(normalized)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "public_base_url must be an HTTPS URL without credentials, "
                "a query, or a fragment."
            )
        return normalized

    def seed(self, default_url: str) -> str:
        return self.repository.seed(default_url)

    def base_url(self) -> str:
        public_base_url = self.repository.get()
        if not public_base_url:
            raise RuntimeError("The Luna Visia public base URL is not configured.")
        return public_base_url

    def update(self, public_base_url: str) -> dict[str, Any]:
        """Persist a new HTTPS origin and regenerate all stored image URLs."""
        normalized = self.validate(public_base_url)
        return {
            "status": "public_base_url_updated",
            "public_base_url": normalized,
            "updated_image_count": self.repository.replace(normalized),
        }


class ImageIndexer:
    """Generate AI metadata for image bytes and persist it with content identity."""

    AI_MODIFIED_TAG = "ai modified"

    def __init__(
        self,
        storage: ImageStorage,
        images: ImageRepository,
        analyzer: OllamaVisionAnalyzer,
        urls: PublicURLService,
    ) -> None:
        self.storage = storage
        self.images = images
        self.analyzer = analyzer
        self.urls = urls

    def analyze(self, data: bytes, modified: bool = False) -> ImageAnalysis:
        """Analyze bytes, tagging replacements of an existing image."""
        analysis = self.analyzer.analyze(data)
        if modified and self.AI_MODIFIED_TAG not in analysis.tags:
            analysis = analysis.model_copy(update={"tags": [*analysis.tags, self.AI_MODIFIED_TAG]})
        return analysis

    def store(self, name: str, data: bytes, analysis: ImageAnalysis) -> Row:
        record = ImageRecord(
            name=name,
            size_bytes=len(data),
            content_sha256=hashlib.sha256(data).hexdigest(),
            caption=analysis.caption,
            tags=list(analysis.tags),
            analysis_model=self.analyzer.model,
        )
        return self.images.upsert(record, self.urls.base_url())

    def backfill(self) -> dict[str, Any]:
        """Analyze unindexed or changed files while skipping current records."""
        indexed = {item["name"]: item for item in self.images.list(hidden=None)}
        analyzed = []
        skipped = 0
        for path in self.storage.image_files():
            data = path.read_bytes()
            existing = indexed.get(path.name)
            if (
                existing
                and existing["content_sha256"] == hashlib.sha256(data).hexdigest()
                and existing["analysis_model"] == self.analyzer.model
            ):
                skipped += 1
                continue
            self.store(path.name, data, self.analyze(data))
            analyzed.append(path.name)
        return {
            "analyzed_count": len(analyzed),
            "skipped_count": skipped,
            "analyzed": analyzed,
        }


class ImageCollectionService:
    """Operations shared by the visible library and the Hidden Vault.

    Each subclass is bound to one side of the ``hidden`` flag and refuses to
    touch images that belong to the other side.
    """

    HIDDEN: bool
    WRONG_COLLECTION_MESSAGE: str

    def __init__(
        self, storage: ImageStorage, images: ImageRepository, rules: MetadataRules
    ) -> None:
        self.storage = storage
        self.images = images
        self.rules = rules

    def path(self, name: str) -> Path:
        """Return the file for an image in this collection or reject access."""
        path = self.storage.existing_path(name)
        if self.images.is_hidden(name) != self.HIDDEN:
            raise PermissionError(self.WRONG_COLLECTION_MESSAGE)
        return path

    def get(self, name: str) -> Row:
        row = self.images.get(name, hidden=self.HIDDEN)
        if row is None:
            raise FileNotFoundError(f"Image {name!r} was not found.")
        return row

    def list(self) -> list[Row]:
        return self.images.list(hidden=self.HIDDEN)

    def update_metadata(self, name: str, change: MetadataChange) -> Row:
        """Explicitly edit a caption and/or searchable tags."""
        self.path(name)
        if change.is_empty:
            raise ValueError("Provide a caption, add_tags, or remove_tags change.")
        current = self.get(name)
        caption = (
            current["caption"] if change.caption is None else self.rules.caption(change.caption)
        )
        tags = self.rules.edited_tags(current["tags"], change.add_tags, change.remove_tags)
        return self.images.update_metadata(name, caption, tags, hidden=self.HIDDEN)

    def remove(self, name: str) -> dict[str, str]:
        """Delete an image and its metadata, restoring the file on failure."""
        with self.storage.removing(self.path(name)):
            self.images.delete(name)
        return {"status": "deleted", "removed_name": name}


class VisibleImageService(ImageCollectionService):
    """The public library: ingest, rename, favorites, and public delivery."""

    HIDDEN = False
    WRONG_COLLECTION_MESSAGE = "Hidden images require authentication."
    RENAME_STEM_PATTERN = re.compile(r"[A-Za-z0-9_]+")

    def __init__(
        self,
        storage: ImageStorage,
        images: ImageRepository,
        rules: MetadataRules,
        decoder: ImagePayloadDecoder,
        indexer: ImageIndexer,
        max_fetch_images: int,
        max_fetch_bytes: int,
    ) -> None:
        super().__init__(storage, images, rules)
        self.decoder = decoder
        self.indexer = indexer
        self.max_fetch_images = max_fetch_images
        self.max_fetch_bytes = max_fetch_bytes

    def ingest(self, name: str, content_base64: str, replace: bool = False) -> Row:
        """Create or replace an image and persist matching AI metadata."""
        path = self.storage.path(name)
        data = self.decoder.decode(name, content_base64)
        exists = path.is_file()
        if replace and not exists:
            raise FileNotFoundError(f"Image {name!r} was not found.")
        if replace and self.images.is_hidden(name):
            raise PermissionError(self.WRONG_COLLECTION_MESSAGE)
        if not replace and exists:
            raise FileExistsError(f"Image {name!r} already exists; use modify_image.")
        analysis = self.indexer.analyze(data, modified=replace)
        with self.storage.writing(path, data):
            return self.indexer.store(name, data, analysis)

    def rename(self, name: str, new_name: str) -> dict[str, Any]:
        """Rename an image, keeping its extension, immutable ID, and public URL."""
        source = self.path(name)
        destination = self.storage.path(new_name)
        if not self.RENAME_STEM_PATTERN.fullmatch(destination.stem):
            raise ValueError(
                "The new filename may contain only letters, numbers, and "
                "underscores before the image extension."
            )
        if source == destination:
            raise ValueError("The new image name must be different from the current name.")
        if source.suffix.lower() != destination.suffix.lower():
            raise ValueError("Renaming cannot change the image file extension.")
        if destination.exists():
            raise FileExistsError(f"Image {new_name!r} already exists.")
        with self.storage.renaming(source, destination):
            metadata = self.images.rename(name, new_name)
        return {"status": "renamed", "old_name": name, **metadata}

    def favorites(self) -> list[Row]:
        return self.images.list_favorites()

    def set_favorite(self, names: list[str], favorite: bool) -> dict[str, Any]:
        """Add visible images to Favorites or remove them."""
        clean_names = unique_names(names, "Select at least one image.")
        for name in clean_names:
            self.path(name)
        changed = self.images.set_favorite(clean_names, favorite)
        return {
            "status": "favorited" if favorite else "unfavorited",
            "names": changed,
            "count": len(changed),
        }

    def public_url(self, name: str) -> str:
        self.path(name)
        return self.get(name)["public_url"]

    def public_path(self, identifier: str) -> Path:
        """Resolve a stable image ID, with legacy filename compatibility."""
        return self.path(self.images.name_for_image_id(identifier) or identifier)

    def select_paths(
        self,
        names: list[str],
        max_images: int | None = None,
        max_bytes: int | None = None,
    ) -> list[Path]:
        """Resolve a bounded, deduplicated batch of visible image paths.

        Limits default to the chat-display bounds; callers that do not embed
        image bytes in a tool result (such as ZIP downloads) pass larger ones.
        """
        max_images = max_images or self.max_fetch_images
        max_bytes = max_bytes or self.max_fetch_bytes
        if isinstance(names, list) and any(not isinstance(name, str) for name in names):
            raise ValueError("Every image name must be a string.")
        clean_names = unique_names(names, "Provide at least one image name.")
        if len(clean_names) > max_images:
            raise ValueError(f"Fetch at most {max_images} images at a time.")
        paths = [self.path(name) for name in clean_names]
        if sum(path.stat().st_size for path in paths) > max_bytes:
            raise ValueError(
                f"Selected images exceed the {self.format_bytes(max_bytes)} limit; "
                "request fewer images."
            )
        return paths

    @staticmethod
    def format_bytes(size: int) -> str:
        if size >= 1024**3 and size % 1024**3 == 0:
            return f"{size // 1024**3} GiB"
        return f"{size / 1024**2:.0f} MiB"


class ImageDownloadService:
    """Package visible images as a ZIP behind a short-lived public download link.

    Archives are never embedded in tool results: MCP clients reject large
    messages, and chat apps cannot hand an embedded blob to the user as a file.
    """

    DOWNLOAD_PATH = "/api/downloads/"

    def __init__(
        self,
        visible: VisibleImageService,
        store: DownloadStore,
        urls: PublicURLService,
        max_images: int,
        max_bytes: int,
    ) -> None:
        self.visible = visible
        self.store = store
        self.urls = urls
        self.max_images = max_images
        self.max_bytes = max_bytes

    def create_zip(self, names: list[str]) -> dict[str, Any]:
        """Archive the named visible images and return their download link."""
        base_url = self.urls.base_url()
        paths = self.visible.select_paths(names, self.max_images, self.max_bytes)
        ticket = self.store.create(paths)
        return {
            "status": "zip_ready",
            "download_url": f"{base_url}{self.DOWNLOAD_PATH}{ticket.token}",
            "file_name": ticket.file_name,
            "image_count": ticket.image_count,
            "size_bytes": ticket.size_bytes,
            "expires_at": ticket.expires_at_iso,
            "names": [path.name for path in paths],
        }

    def archive(self, token: str) -> DownloadTicket:
        """Return the live archive ticket for a download token."""
        return self.store.resolve(token)


class HiddenImageService(ImageCollectionService):
    """The authenticated Hidden Vault: list, edit, delete, hide, and restore."""

    HIDDEN = True
    WRONG_COLLECTION_MESSAGE = "Only hidden images can use this route."

    def set_hidden(self, names: list[str], hidden: bool) -> dict[str, Any]:
        """Move images into or out of the logical Hidden Vault."""
        clean_names = unique_names(names, "Select at least one image.")
        for name in clean_names:
            self.storage.existing_path(name)
        changed = self.images.set_hidden(clean_names, hidden)
        return {
            "status": "hidden" if hidden else "restored",
            "names": changed,
            "count": len(changed),
        }

    def remove_many(self, names: list[str]) -> dict[str, Any]:
        clean_names = unique_names(names, "Select at least one hidden image.")
        for name in clean_names:
            self.remove(name)
        return {"status": "deleted", "count": len(clean_names)}


class SearchService:
    """Search visible metadata with normalized text and tags."""

    def __init__(self, images: ImageRepository, rules: MetadataRules, limit_max: int) -> None:
        self.images = images
        self.rules = rules
        self.limit_max = limit_max

    def search(
        self,
        query: str,
        tags: list[str] | None,
        match_all_tags: bool,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        normalized_query = self.rules.search_query(query)
        normalized_tags = self.rules.normalize_tags(tags) if tags else []
        if not normalized_query and not normalized_tags:
            raise ValueError("Provide a meaningful query or at least one tag.")
        count, results = self.images.search(
            normalized_query,
            normalized_tags,
            match_all_tags,
            min(max(limit, 1), self.limit_max),
            max(offset, 0),
        )
        return {
            "query": query,
            "normalized_query": normalized_query,
            "count": count,
            "results": results,
        }


class VisiaService:
    """Facade grouping every Luna Visia use case for the transport adapters."""

    def __init__(
        self,
        visible: VisibleImageService,
        hidden: HiddenImageService,
        search: SearchService,
        indexer: ImageIndexer,
        urls: PublicURLService,
        downloads: ImageDownloadService,
    ) -> None:
        self.visible = visible
        self.hidden = hidden
        self.search = search
        self.indexer = indexer
        self.urls = urls
        self.downloads = downloads

    @classmethod
    def create(
        cls,
        config: AppConfig,
        images: ImageRepository,
        public_urls: PublicURLRepository,
        analyzer: OllamaVisionAnalyzer,
    ) -> "VisiaService":
        """Wire the use cases around the given persistence and analysis adapters."""
        rules = MetadataRules(config.max_tags, config.search_stop_words)
        storage = ImageStorage(config.image_dir, set(config.allowed_formats))
        urls = PublicURLService(public_urls)
        urls.seed(config.public_base_url)
        indexer = ImageIndexer(storage, images, analyzer, urls)
        visible = VisibleImageService(
            storage,
            images,
            rules,
            ImagePayloadDecoder(config.allowed_formats, config.max_image_bytes),
            indexer,
            config.max_fetch_images,
            config.max_fetch_bytes,
        )
        return cls(
            visible=visible,
            hidden=HiddenImageService(storage, images, rules),
            search=SearchService(images, rules, config.search_limit_max),
            indexer=indexer,
            urls=urls,
            downloads=ImageDownloadService(
                visible,
                DownloadStore(config.download_dir, config.download_ttl_seconds),
                urls,
                config.max_archive_images,
                config.max_archive_bytes,
            ),
        )
