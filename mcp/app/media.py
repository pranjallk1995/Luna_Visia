"""Image byte validation, chat previews, and ZIP archives."""

import base64
import binascii
import re
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image as PillowImage
from PIL import ImageDraw, ImageOps, UnidentifiedImageError


class ImagePayloadDecoder:
    """Decode image payloads, detect their real format, and check filenames.

    Base64 from LLM clients is often line-wrapped, unpadded, URL-safe, or a
    ``data:`` URL, so all of those are normalized before decoding.
    """

    FORMAT_EXTENSIONS = {"GIF": ".gif", "JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
    # Phone cameras write multi-picture JPEGs, which Pillow reports as MPO.
    FORMAT_ALIASES = {"MPO": "JPEG"}
    DEFAULT_STEM = "image"

    def __init__(self, allowed_formats: dict[str, str], max_image_bytes: int) -> None:
        self.allowed_formats = allowed_formats
        self.max_image_bytes = max_image_bytes

    @property
    def max_base64_chars(self) -> int:
        """Return the largest Base64 payload allowed for the byte limit."""
        return 4 * ((self.max_image_bytes + 2) // 3)

    def decode_base64(self, payload: str) -> bytes:
        """Decode standard, URL-safe, wrapped, unpadded, or data-URL Base64."""
        text = payload.strip()
        if text[:5].casefold() == "data:":
            header, separator, text = text.partition(",")
            if not separator or not header.casefold().endswith(";base64"):
                raise ValueError("Image data URLs must contain Base64-encoded data.")
        text = re.sub(r"\s+", "", text)
        if len(text) > self.max_base64_chars:
            raise ValueError("Image content exceeds the 50 MiB limit.")
        text = text.replace("-", "+").replace("_", "/")
        text += "=" * (-len(text) % 4)
        try:
            return base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("content_base64 must be valid base64 data.") from error

    def detect_format(self, data: bytes) -> str:
        """Return the Pillow format of valid, bounded, supported image bytes."""
        if not data or len(data) > self.max_image_bytes:
            raise ValueError("Image content is empty or exceeds the 50 MiB limit.")
        try:
            with PillowImage.open(BytesIO(data)) as image:
                detected = image.format
                image.verify()
        except (UnidentifiedImageError, OSError) as error:
            raise ValueError("Content is not a valid image.") from error
        detected = self.FORMAT_ALIASES.get(detected, detected)
        if detected not in self.FORMAT_EXTENSIONS:
            supported = ", ".join(self.FORMAT_EXTENSIONS)
            raise ValueError(f"{detected} images are not supported; use {supported}.")
        return detected

    def validate(self, name: str, data: bytes) -> bytes:
        """Require ``data`` to be an image whose format matches ``name``."""
        detected = self.detect_format(data)
        expected = self.allowed_formats[Path(name).suffix.lower()]
        if detected != expected:
            raise ValueError(f"Image content is {detected}, filename expects {expected}.")
        return data

    def decode(self, name: str, content_base64: str) -> bytes:
        return self.validate(name, self.decode_base64(content_base64))

    def name_for(self, requested: str | None, detected: str) -> str:
        """Turn a requested name into a plain filename whose extension matches.

        Directories are dropped, and a missing or wrong extension is replaced
        with the detected format's (``photo.png`` holding JPEG -> ``photo.jpg``).
        """
        name = Path((requested or "").strip().replace("\\", "/")).name
        stem, suffix = Path(name).stem, Path(name).suffix.lower()
        if suffix not in self.allowed_formats:
            stem = name
        stem = stem.strip().lstrip(".")
        if not stem:
            stem = self.DEFAULT_STEM
        if suffix in self.allowed_formats and self.allowed_formats[suffix] == detected:
            return f"{stem}{Path(name).suffix}"
        return f"{stem}{self.FORMAT_EXTENSIONS[detected]}"


class PreviewRenderer:
    """Render compact JPEG previews: one image, or a labeled contact sheet."""

    SHEET_COLUMNS = 2
    TILE_WIDTH = 512
    TILE_IMAGE_HEIGHT = 384
    TILE_LABEL_HEIGHT = 40
    TILE_PADDING = 24
    LABEL_MAX_CHARS = 58
    SHEET_BACKGROUND = "#111827"

    def __init__(self, max_edge: int) -> None:
        self.max_edge = max_edge

    @staticmethod
    def _load(path: Path, max_size: tuple[int, int]) -> PillowImage.Image:
        """Load one image with corrected orientation and a bounded RGB canvas."""
        with PillowImage.open(path) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail(max_size, PillowImage.Resampling.LANCZOS)
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                flattened = PillowImage.new("RGB", rgba.size, "white")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                return flattened
            return image.convert("RGB")

    @staticmethod
    def _jpeg(image: PillowImage.Image) -> bytes:
        output = BytesIO()
        image.save(output, format="JPEG", quality=82, optimize=True)
        return output.getvalue()

    def _label(self, name: str) -> str:
        limit = self.LABEL_MAX_CHARS
        return name if len(name) <= limit else f"{name[: limit - 3]}..."

    def render(self, paths: list[Path]) -> bytes:
        """Return JPEG bytes for one preview, or one sheet for several images."""
        if len(paths) == 1:
            return self._jpeg(self._load(paths[0], (self.max_edge, self.max_edge)))

        columns, width = self.SHEET_COLUMNS, self.TILE_WIDTH
        image_height, row_height = self.TILE_IMAGE_HEIGHT, self.TILE_IMAGE_HEIGHT + self.TILE_LABEL_HEIGHT
        rows = (len(paths) + columns - 1) // columns
        sheet = PillowImage.new("RGB", (columns * width, rows * row_height), self.SHEET_BACKGROUND)
        draw = ImageDraw.Draw(sheet)
        for index, path in enumerate(paths):
            image = self._load(
                path, (width - self.TILE_PADDING, image_height - self.TILE_PADDING)
            )
            column, row = index % columns, index // columns
            left = column * width + (width - image.width) // 2
            top = row * row_height + (image_height - image.height) // 2
            sheet.paste(image, (left, top))
            draw.text(
                (column * width + 12, row * row_height + image_height + 10),
                self._label(path.name),
                fill="white",
            )
        return self._jpeg(sheet)


class ImageArchive:
    """Write original image files into a ZIP on disk.

    Entries are stored uncompressed: every supported format is already
    compressed, so deflating only costs CPU and can grow the archive.
    """

    UPLOAD_PREFIX = re.compile(r"[0-9a-f]{32}_(?=.)", re.IGNORECASE)

    @classmethod
    def entry_name(cls, stored_name: str) -> str:
        """Drop the internal upload prefix so entries keep their original names."""
        return cls.UPLOAD_PREFIX.sub("", stored_name, count=1)

    @classmethod
    def entry_names(cls, paths: list[Path]) -> list[str]:
        """Return unique entry names, numbering duplicates like ``cat (2).png``."""
        names: list[str] = []
        taken: set[str] = set()
        for path in paths:
            name = cls.entry_name(path.name)
            stem, suffix = Path(name).stem, Path(name).suffix
            counter = 2
            while name.casefold() in taken:
                name = f"{stem} ({counter}){suffix}"
                counter += 1
            taken.add(name.casefold())
            names.append(name)
        return names

    @classmethod
    def write(cls, paths: list[Path], destination: Path) -> None:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as bundle:
            for path, name in zip(paths, cls.entry_names(paths)):
                bundle.write(path, arcname=name)
