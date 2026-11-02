"""Read-only access to vault image files: listing, size facts, and cached thumbnails."""

from __future__ import annotations

from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from config import UIConfig


class ImageFiles:
    """Pair backend metadata with the vault's files, and encode thumbnails once."""

    def __init__(self, image_dir: Path) -> None:
        self.image_dir = image_dir
        self.thumbnails: OrderedDict[tuple[str, int], bytes] = OrderedDict()
        self.dimensions: OrderedDict[tuple[str, int], tuple[int, int]] = OrderedDict()

    def path(self, name: str) -> Path:
        """The vault file called ``name``; FileNotFoundError for anything else."""
        path = self.image_dir / name
        if (
            Path(name).name != name
            or path.suffix.lower() not in UIConfig.IMAGE_EXTENSIONS
            or not path.is_file()
        ):
            raise FileNotFoundError(name)
        return path

    @staticmethod
    def _remember(cache: OrderedDict, key: Any, value: Any, limit: int) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)

    def _dimensions(self, path: Path, modified_ns: int) -> tuple[int, int]:
        key = (path.name, modified_ns)
        cached = self.dimensions.get(key)
        if cached is not None:
            return cached
        # Opening reads only the file's header, not its pixels.
        with Image.open(path) as source:
            size = source.size
        self._remember(self.dimensions, key, size, UIConfig.FACTS_CACHE_ENTRIES)
        return size

    def collection(self, metadata: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Records with files on disk: newest first, or the saved showcase order once one exists."""
        records = []
        for item in metadata:
            try:
                path = self.path(item["name"])
                stat = path.stat()
                width, height = self._dimensions(path, stat.st_mtime_ns)
            except OSError:
                continue
            records.append(
                {**item, "size_bytes": stat.st_size, "width": width, "height": height, "version": stat.st_mtime_ns}
            )
        records.sort(key=lambda record: record["version"], reverse=True)
        if any(record.get("showcase_rank") is not None for record in records):
            # Stable sort: unranked (new) images keep their newest-first order ahead of ranked ones.
            records.sort(key=lambda record: record.get("showcase_rank") or 0)
        return records

    def thumbnail(self, name: str) -> bytes:
        """A card-sized WebP of the image, encoded once per file version."""
        path = self.path(name)
        key = (name, path.stat().st_mtime_ns)
        cached = self.thumbnails.get(key)
        if cached is not None:
            self.thumbnails.move_to_end(key)
            return cached
        with Image.open(path) as source:
            source.draft("RGB", UIConfig.THUMBNAIL_SIZE)
            preview = ImageOps.exif_transpose(source).convert("RGB")
        fitted = ImageOps.fit(preview, UIConfig.THUMBNAIL_SIZE, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        encoded = BytesIO()
        fitted.save(encoded, "WEBP", quality=UIConfig.THUMBNAIL_QUALITY)
        data = encoded.getvalue()
        self._remember(self.thumbnails, key, data, UIConfig.THUMBNAIL_CACHE_ENTRIES)
        return data
