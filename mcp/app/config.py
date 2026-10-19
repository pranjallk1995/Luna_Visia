"""Validated runtime configuration for the Luna Visia backend."""

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class AppConfig:
    """Immutable backend settings loaded from environment variables."""

    image_dir: Path
    database_url: str
    ollama_url: str
    vision_model: str
    public_base_url: str = ""
    host: str = "0.0.0.0"
    port: int = 8000
    allowed_formats: dict[str, str] = field(
        default_factory=lambda: {
            ".gif": "GIF",
            ".jpeg": "JPEG",
            ".jpg": "JPEG",
            ".png": "PNG",
            ".webp": "WEBP",
        }
    )
    max_image_bytes: int = 50 * 1024 * 1024
    max_fetch_images: int = 20
    max_fetch_bytes: int = 50 * 1024 * 1024
    max_upload_redirects: int = 3
    # ZIPs are served as download links, so they are not bound by MCP message size.
    max_archive_images: int = 200
    max_archive_bytes: int = 2 * 1024 * 1024 * 1024
    download_ttl_seconds: int = 30 * 60
    download_dir: Path = Path(tempfile.gettempdir()) / "luna-visia-downloads"
    display_max_edge: int = 768
    max_tags: int = 32
    search_limit_max: int = 100
    password_hash_iterations: int = 600_000
    hidden_session_seconds: int = 30 * 60
    search_stop_words: frozenset[str] = frozenset(
        {
            "about",
            "all",
            "are",
            "count",
            "depict",
            "depicting",
            "do",
            "find",
            "how",
            "image",
            "images",
            "many",
            "of",
            "photo",
            "photos",
            "picture",
            "pictures",
            "show",
            "showing",
            "that",
            "the",
            "there",
            "what",
            "with",
        }
    )

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Build and validate backend configuration from the environment."""
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("DATABASE_URL is required.")
        ollama_url = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
        parsed = urlparse(ollama_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("OLLAMA_URL must be a valid HTTP(S) URL.")
        vision_model = os.getenv("VISION_MODEL", "qwen2.5vl:7b").strip()
        if not vision_model:
            raise RuntimeError("VISION_MODEL cannot be blank.")
        image_dir = Path(os.getenv("IMAGE_DIR", "/data/images"))
        public_base_url = (
            os.getenv("LUNAVISIA_PUBLIC_BASE_URL")
            or os.getenv("LUNAVAULT_PUBLIC_BASE_URL")
            or ""
        ).strip().rstrip("/")
        if public_base_url:
            public_url = urlparse(public_base_url)
            if public_url.scheme != "https" or not public_url.netloc:
                raise RuntimeError(
                    "LUNAVISIA_PUBLIC_BASE_URL must be a valid HTTPS URL."
                )
        return cls(
            image_dir=image_dir,
            database_url=database_url,
            ollama_url=ollama_url,
            vision_model=vision_model,
            public_base_url=public_base_url,
        )
