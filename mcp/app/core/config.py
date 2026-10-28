"""Validated runtime configuration for the Luna Visia backend."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse


@dataclass(frozen=True)
class AppConfig:
    """Immutable backend settings loaded from environment variables."""

    ASSET_DIR: ClassVar[Path] = Path(__file__).resolve().parent.parent / "assets"
    VISION_MAX_INPUT_EDGE: ClassVar[int] = 1024
    # Luna Lexia asks Sentia for the same model with this context. Ollama reloads a
    # model whose requested context changes, so matching it lets both apps share one
    # loaded copy instead of swapping it (about 6 s) whenever they alternate.
    VISION_CONTEXT_TOKENS: ClassVar[int] = 16_384
    VISION_TIMEOUT_SECONDS: ClassVar[float] = 300.0
    VISION_CONNECT_TIMEOUT_SECONDS: ClassVar[float] = 10.0
    REMOTE_FETCH_TIMEOUT_SECONDS: ClassVar[float] = 30.0
    # Longest embedded text (prompts, generator notes) kept from an image's metadata.
    INSPECTION_MAX_TEXT_CHARS: ClassVar[int] = 1000
    # Leftover archives without a ticket are only removed once clearly abandoned, so a
    # concurrent purge never deletes an archive whose ticket is being written.
    DOWNLOAD_LEFTOVER_GRACE_SECONDS: ClassVar[int] = 60 * 60
    FULL_DETAIL_MAX_IMAGES: ClassVar[int] = 20
    BATCH_MAX_IMAGES: ClassVar[int] = 100
    CREDENTIAL_SALT_BYTES: ClassVar[int] = 16

    image_dir: Path
    database_url: str
    ollama_url: str
    vision_model: str
    public_base_url: str = ""
    api_token: str = ""
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
    download_ttl_seconds: int = 24 * 60 * 60
    max_download_store_bytes: int = 10 * 1024 * 1024 * 1024
    upload_ttl_seconds: int = 15 * 60
    # A persistent volume, so download links keep working across restarts.
    download_dir: Path = Path("/data/downloads")
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
    # Public HTTPS origin of this MCP server, used for icon URLs ("" = embedded icons only).
    mcp_public_url: str = ""

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Build and validate backend configuration from the environment."""
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("DATABASE_URL is required.")
        ollama_url = os.getenv("OLLAMA_URL", "http://luna-sentia-ollama:11434").rstrip("/")
        parsed = urlparse(ollama_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("OLLAMA_URL must be a valid HTTP(S) URL.")
        vision_model = os.getenv("VISION_MODEL", "qwen3-vl:8b-instruct").strip()
        if not vision_model:
            raise RuntimeError("VISION_MODEL cannot be blank.")
        image_dir = Path(os.getenv("IMAGE_DIR", "/data/images"))
        public_base_url = (
            (
                os.getenv("LUNAVISIA_PUBLIC_BASE_URL")
                or os.getenv("LUNAVAULT_PUBLIC_BASE_URL")
                or ""
            )
            .strip()
            .rstrip("/")
        )
        if public_base_url:
            public_url = urlparse(public_base_url)
            if public_url.scheme != "https" or not public_url.netloc:
                raise RuntimeError(
                    "LUNAVISIA_PUBLIC_BASE_URL must be a valid HTTPS URL."
                )
        api_token = os.getenv("LUNAVISIA_API_TOKEN", "").strip()
        if len(api_token) < 32:
            raise RuntimeError(
                "LUNAVISIA_API_TOKEN must contain at least 32 characters."
            )
        mcp_public_url = os.getenv("MCP_PUBLIC_URL", "").strip().rstrip("/")
        if mcp_public_url:
            parsed_public_url = urlparse(mcp_public_url)
            if parsed_public_url.scheme != "https" or not parsed_public_url.netloc:
                raise RuntimeError("MCP_PUBLIC_URL must be a valid HTTPS URL.")
        return cls(
            mcp_public_url=mcp_public_url,
            image_dir=image_dir,
            database_url=database_url,
            ollama_url=ollama_url,
            vision_model=vision_model,
            public_base_url=public_base_url,
            api_token=api_token,
        )
