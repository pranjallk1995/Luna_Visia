"""Image byte validation, chat previews, and ZIP archives."""

import base64
import binascii
import math
import re
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image as PillowImage
from PIL import ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


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


UPLOAD_PREFIX = re.compile(r"[0-9a-f]{32}_(?=.)", re.IGNORECASE)


def display_name(stored_name: str) -> str:
    """Drop the internal upload prefix (32 hex characters and ``_``) from a filename."""
    return UPLOAD_PREFIX.sub("", stored_name, count=1)


class PreviewRenderer:
    """Render compact JPEG previews: one image, or a numbered contact sheet.

    Sheets use a near-square grid capped at ``SHEET_WIDTH`` so chat clients do
    not shrink them until labels become unreadable (a tall two-column strip of
    20 images was 1024x4240 and displayed at under half size).
    """

    SHEET_WIDTH = 1536
    MAX_COLUMNS = 5
    TILE_ASPECT = 0.75
    TILE_PADDING = 12
    SHEET_BACKGROUND = "#111827"
    LABEL_COLOR = "#f5f5f5"
    # Mid-grey checks keep both white and black artwork visible behind transparency.
    CHECKER_COLORS = ("#8c8c8c", "#a6a6a6")
    CHECKER_SIZE = 12

    def __init__(self, max_edge: int) -> None:
        self.max_edge = max_edge

    @classmethod
    def _checkerboard(cls, size: tuple[int, int]) -> PillowImage.Image:
        board = PillowImage.new("RGB", size, cls.CHECKER_COLORS[0])
        draw = ImageDraw.Draw(board)
        step = cls.CHECKER_SIZE
        for top in range(0, size[1], step):
            for left in range((top // step % 2) * step, size[0], 2 * step):
                draw.rectangle((left, top, left + step - 1, top + step - 1), fill=cls.CHECKER_COLORS[1])
        return board

    @classmethod
    def _load(cls, path: Path, max_size: tuple[int, int]) -> PillowImage.Image:
        """Load one image with corrected orientation and a bounded RGB canvas."""
        with PillowImage.open(path) as source:
            source.draft("RGB", max_size)
            image = ImageOps.exif_transpose(source)
            image.thumbnail(max_size, PillowImage.Resampling.LANCZOS)
            if image.mode in {"RGBA", "LA", "P"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                if rgba.getchannel("A").getextrema()[0] < 255:
                    flattened = cls._checkerboard(rgba.size)
                    flattened.paste(rgba, mask=rgba.getchannel("A"))
                    return flattened
                return rgba.convert("RGB")
            return image.convert("RGB")

    @staticmethod
    def _jpeg(image: PillowImage.Image) -> bytes:
        output = BytesIO()
        image.save(output, format="JPEG", quality=82, optimize=True)
        return output.getvalue()

    @classmethod
    def columns_for(cls, count: int) -> int:
        return max(2, min(cls.MAX_COLUMNS, math.ceil(math.sqrt(count))))

    @staticmethod
    def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
        """Truncate ``text`` with an ellipsis so it fits ``width`` pixels."""
        if draw.textlength(text, font=font) <= width:
            return text
        while text and draw.textlength(f"{text}…", font=font) > width:
            text = text[:-1]
        return f"{text}…"

    def render(self, paths: list[Path], labels: list[str] | None = None) -> bytes:
        """Return JPEG bytes for one preview, or one sheet for several images.

        Sheet tiles are numbered from 1 and labeled with ``labels`` (default:
        display names), so they can be matched to an accompanying text list.
        """
        if len(paths) == 1:
            return self._jpeg(self._load(paths[0], (self.max_edge, self.max_edge)))

        labels = labels or [display_name(path.name) for path in paths]
        columns = self.columns_for(len(paths))
        tile_width = self.SHEET_WIDTH // columns
        image_height = int(tile_width * self.TILE_ASPECT)
        font = ImageFont.load_default(size=max(14, tile_width // 18))
        label_height = int(font.size * 1.9)
        row_height = image_height + label_height
        rows = math.ceil(len(paths) / columns)
        sheet = PillowImage.new(
            "RGB", (columns * tile_width, rows * row_height), self.SHEET_BACKGROUND
        )
        draw = ImageDraw.Draw(sheet)
        inner = (tile_width - 2 * self.TILE_PADDING, image_height - 2 * self.TILE_PADDING)
        for index, (path, label) in enumerate(zip(paths, labels)):
            column, row = index % columns, index // columns
            left, top = column * tile_width, row * row_height
            image = self._load(path, inner)
            sheet.paste(
                image,
                (left + (tile_width - image.width) // 2, top + (image_height - image.height) // 2),
            )
            text = self._fit(draw, f"{index + 1}. {label}", font, inner[0])
            draw.text(
                (left + self.TILE_PADDING, top + image_height + (label_height - font.size) // 2),
                text,
                fill=self.LABEL_COLOR,
                font=font,
            )
        return self._jpeg(sheet)


class ImageArchive:
    """Write original image files into a ZIP on disk.

    Entries are stored uncompressed: every supported format is already
    compressed, so deflating only costs CPU and can grow the archive.
    """

    @staticmethod
    def entry_name(stored_name: str) -> str:
        """Drop the internal upload prefix so entries keep their original names."""
        return display_name(stored_name)

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
