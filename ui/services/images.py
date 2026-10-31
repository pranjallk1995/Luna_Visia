"""Read-only access to vault image files with cached decoding."""

from dataclasses import dataclass
from pathlib import Path

import streamlit as st
from PIL import Image, ImageOps

from services.api import LunaVisiaAPI
from config import UIConfig


class ImageFiles:
    """List vault files and decode cached thumbnails, previews, and facts."""

    def __init__(self, config: UIConfig) -> None:
        self.config = config
        self.image_dir = config.image_dir
        self.image_dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[Path]:
        """List supported vault files newest first without decoding images."""
        return sorted(
            (
                path
                for path in self.image_dir.iterdir()
                if path.is_file() and path.suffix.lower() in self.config.image_extensions
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def path(self, name: str) -> Path:
        return self.image_dir / name

    @staticmethod
    @st.cache_data(show_spinner=False, max_entries=UIConfig.THUMBNAIL_CACHE_ENTRIES)
    def _thumbnail(image_path: str, modified_ns: int, max_size: tuple[int, int]) -> Image.Image:
        del modified_ns
        with Image.open(image_path) as source:
            source.draft("RGB", max_size)
            preview = ImageOps.exif_transpose(source).convert("RGB")
            return ImageOps.fit(
                preview, max_size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)
            )

    @staticmethod
    @st.cache_data(show_spinner=False, max_entries=UIConfig.PREVIEW_CACHE_ENTRIES)
    def _full_preview(image_path: str, modified_ns: int) -> Image.Image:
        del modified_ns
        with Image.open(image_path) as source:
            preview = ImageOps.exif_transpose(source)
            preview.load()
            return preview.copy()

    @staticmethod
    @st.cache_data(show_spinner=False, max_entries=UIConfig.FACTS_CACHE_ENTRIES)
    def _facts(image_path: str, modified_ns: int) -> tuple[int, int, int]:
        del modified_ns
        path = Path(image_path)
        with Image.open(path) as source:
            width, height = source.size
        return path.stat().st_size, width, height

    def thumbnail(self, path: Path) -> Image.Image:
        """Return a cached, card-sized thumbnail for a vault image."""
        return self._thumbnail(str(path), path.stat().st_mtime_ns, self.config.card_size)

    def full_preview(self, path: Path) -> Image.Image:
        """Return a decoded image suitable for a full-size preview."""
        return self._full_preview(str(path), path.stat().st_mtime_ns)

    def facts(self, path: Path) -> tuple[int, int, int]:
        """Return cached byte size and pixel dimensions for an image."""
        return self._facts(str(path), path.stat().st_mtime_ns)

    @staticmethod
    def mime_type(path: Path) -> str:
        return f"image/{path.suffix.lower().lstrip('.')}"

    @staticmethod
    def size_label(size: int) -> str:
        if size >= 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size} B"

    @staticmethod
    def display_name(stored_name: str) -> str:
        """Remove the internal upload prefix from a user-facing filename."""
        prefix, separator, original_name = stored_name.partition("_")
        if (
            separator
            and len(prefix) == 32
            and all(character in "0123456789abcdef" for character in prefix.lower())
        ):
            return original_name
        return stored_name


@dataclass(frozen=True)
class ImageCollection:
    """Vault files that have metadata in one collection, keyed by filename."""

    images: list[Path]
    metadata_by_name: dict[str, dict]

    @classmethod
    def from_metadata(cls, files: ImageFiles, metadata: list[dict]) -> "ImageCollection":
        """Newest files first, or the server's showcase order once one is saved."""
        metadata_by_name = {item["name"]: item for item in metadata}
        paths = [path for path in files.list() if path.name in metadata_by_name]
        if any(item.get("showcase_rank") is not None for item in metadata):
            # Stable sort: unranked (new) images keep their newest-first order ahead of ranked ones.
            paths.sort(key=lambda path: metadata_by_name[path.name].get("showcase_rank") or 0)
        return cls(paths, metadata_by_name)

    @classmethod
    def visible(cls, api: LunaVisiaAPI, files: ImageFiles) -> "ImageCollection":
        return cls.from_metadata(files, api.metadata())

    def only(self, names: set[str]) -> "ImageCollection":
        return ImageCollection(
            [path for path in self.images if path.name in names], self.metadata_by_name
        )

    def matching(self, query: str) -> "ImageCollection":
        """Keep images whose display name or any tag contains ``query``."""
        if not query:
            return self

        def matches(path: Path) -> bool:
            if query in ImageFiles.display_name(path.name).casefold():
                return True
            tags = (self.metadata_by_name.get(path.name) or {}).get("tags", [])
            return any(query in str(tag).casefold() for tag in tags)

        return ImageCollection(
            [path for path in self.images if matches(path)], self.metadata_by_name
        )
