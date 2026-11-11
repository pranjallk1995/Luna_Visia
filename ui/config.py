"""Validated settings for the UI gateway and the browser app it serves."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse


@dataclass(frozen=True)
class SSOConfig:
    """Single sign-on through Luna Sentia; this block is identical in every Luna app's config.py."""

    COOKIE_NAME: ClassVar[str] = "luna_sso"
    SESSION_HEADER: ClassVar[str] = "X-Luna-Session"
    # How long a confirmed session is trusted before asking Sentia again, so a sign-out
    # in another app takes effect here within this many seconds.
    RECHECK_SECONDS: ClassVar[int] = 60
    LOOKUP_TIMEOUT_SECONDS: ClassVar[float] = 5.0
    SESSION_CACHE_ENTRIES: ClassVar[int] = 256

    public_url: str
    internal_url: str = "http://luna-sentia-auth:8000"

    @classmethod
    def from_env(cls) -> "SSOConfig":
        public_url = os.getenv("LUNA_SSO_URL", "").strip().rstrip("/")
        if not public_url:
            raise RuntimeError("LUNA_SSO_URL must be the public address of Luna Sentia's auth service.")
        internal_url = os.getenv("LUNA_SSO_INTERNAL_URL", "").strip().rstrip("/")
        return cls(public_url=public_url, internal_url=internal_url or cls.internal_url)


@dataclass(frozen=True)
class UIConfig:
    """Immutable gateway settings, plus what the browser app reads from ``/auth/session``."""

    UI_ROOT: ClassVar[Path] = Path(__file__).resolve().parent
    # The release this image holds, shown in the footer and on Luna Unia's cards.
    VERSION: ClassVar[str] = os.getenv("LUNA_VERSION", "").strip()
    # The built React app; the Dockerfile puts it here.
    STATIC_DIR: ClassVar[Path] = UI_ROOT / "static"
    MAX_TAGS: ClassVar[int] = 32
    NAME_MAX_CHARS: ClassVar[int] = 200
    CAPTION_MAX_CHARS: ClassVar[int] = 500
    IMAGES_PER_PAGE: ClassVar[int] = 12
    # A bulk action is sent in batches of this many images; the backend accepts up to 100.
    BATCH_SIZE: ClassVar[int] = 100
    UPLOAD_TYPES: ClassVar[tuple[str, ...]] = ("png", "jpg", "jpeg", "gif", "webp")
    IMAGE_EXTENSIONS: ClassVar[frozenset[str]] = frozenset({".gif", ".jpeg", ".jpg", ".png", ".webp"})
    API_TIMEOUT_SECONDS: ClassVar[float] = 30.0
    # Uploads wait for the backend's vision model to caption and tag the image.
    UPLOAD_TIMEOUT_SECONDS: ClassVar[float] = 360.0
    # A batch reads up to BATCH_SIZE image files and updates each one's record.
    BATCH_TIMEOUT_SECONDS: ClassVar[float] = 120.0
    # A 50 MiB image grows by a third as base64, plus its JSON wrapper.
    MAX_REQUEST_BYTES: ClassVar[int] = 70 * 1024 * 1024
    # Twice the 420 x 280 card, so thumbnails stay sharp on high-density screens.
    # Changing the shape also needs a new `shape` in the web app's thumbnailUrl.
    THUMBNAIL_SIZE: ClassVar[tuple[int, int]] = (840, 560)
    THUMBNAIL_QUALITY: ClassVar[int] = 80
    THUMBNAIL_CACHE_ENTRIES: ClassVar[int] = 256
    FACTS_CACHE_ENTRIES: ClassVar[int] = 4096
    # Thumbnail and file URLs carry the file's modified time, so they never go stale.
    IMMUTABLE_CACHE_CONTROL: ClassVar[str] = "private, max-age=31536000, immutable"
    CONTENT_SECURITY_POLICY: ClassVar[str] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )

    image_dir: Path
    api_url: str
    api_token: str
    allowed_emails: frozenset[str]
    sso: SSOConfig

    @classmethod
    def from_env(cls) -> "UIConfig":
        """Build and validate gateway configuration from environment variables."""
        api_url = (
            os.getenv("LUNAVISIA_API_URL")
            or os.getenv("LUNAVAULT_API_URL")
            or "http://luna-visia-mcp:8000"
        ).rstrip("/")
        parsed = urlparse(api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("LUNAVISIA_API_URL must be a valid HTTP(S) URL.")
        api_token = os.getenv("LUNAVISIA_API_TOKEN", "").strip()
        if len(api_token) < 32:
            raise RuntimeError("LUNAVISIA_API_TOKEN must contain at least 32 characters.")
        allowed_emails = frozenset(
            email.strip().casefold()
            for email in os.getenv("LUNAVISIA_ALLOWED_EMAILS", "").split(",")
            if email.strip()
        )
        return cls(
            image_dir=Path(os.getenv("IMAGE_DIR", "/data/images")),
            api_url=api_url,
            api_token=api_token,
            allowed_emails=allowed_emails,
            sso=SSOConfig.from_env(),
        )
