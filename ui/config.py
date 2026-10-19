"""Validated presentation and service settings for the Streamlit UI."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse


@dataclass(frozen=True)
class UIConfig:
    """Immutable UI layout and backend-connection settings."""

    UI_ROOT: ClassVar[Path] = Path(__file__).resolve().parent
    LOGO_PATH: ClassVar[Path] = UI_ROOT / "assets" / "lunavisia_logo.png"
    MAX_TAGS: ClassVar[int] = 32
    UPLOAD_TYPES: ClassVar[tuple[str, ...]] = ("png", "jpg", "jpeg", "gif", "webp")

    image_dir: Path
    api_url: str
    allowed_emails: frozenset[str]
    card_size: tuple[int, int] = (420, 248)
    gallery_corner_radius: int = 10
    gallery_selection_color: str = "#D4D4D4"
    gallery_border_thickness: int = 4
    gallery_max_row_height: int = 240
    gallery_columns: int = 3
    images_per_page: int = 12
    image_extensions: frozenset[str] = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})

    @classmethod
    def from_env(cls) -> "UIConfig":
        """Build and validate UI configuration from environment variables."""
        api_url = (
            os.getenv("LUNAVISIA_API_URL")
            or os.getenv("LUNAVAULT_API_URL")
            or "http://luna-visia-mcp:8000"
        ).rstrip("/")
        parsed = urlparse(api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("LUNAVISIA_API_URL must be a valid HTTP(S) URL.")
        allowed_emails = frozenset(
            email.strip().casefold()
            for email in os.getenv("LUNAVISIA_ALLOWED_EMAILS", "").split(",")
            if email.strip()
        )
        if not allowed_emails:
            raise RuntimeError("LUNAVISIA_ALLOWED_EMAILS must list at least one account.")
        return cls(
            image_dir=Path(os.getenv("IMAGE_DIR", "/data/images")),
            api_url=api_url,
            allowed_emails=allowed_emails,
        )
