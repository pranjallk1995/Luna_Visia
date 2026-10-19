"""In-memory test doubles for Luna Visia repositories and analysis."""

import base64
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.models import ImageAnalysis
from app.repositories import PUBLIC_IMAGE_PATH


class FakeImageRepository:
    """Provide an in-memory ImageRepository test double."""

    def __init__(self) -> None:
        self.rows = {}

    def upsert(self, record, public_base_url):
        previous = self.rows.get(record.name, {})
        image_id = previous.get("image_id", f"image-{len(self.rows) + 1}")
        row = {
            **vars(record),
            "tags": list(record.tags),
            "updated_at": "test",
            "hidden": previous.get("hidden", False),
            "favorite": previous.get("favorite", False),
            "image_id": image_id,
            "public_url": f"{public_base_url}{PUBLIC_IMAGE_PATH}{image_id}",
        }
        self.rows[record.name] = row
        return row

    def get(self, name, hidden=False):
        row = self.rows.get(name)
        if row is None or (hidden is not None and row["hidden"] != hidden):
            return None
        return row

    def list(self, hidden=False):
        return [
            row for row in self.rows.values() if hidden is None or row["hidden"] == hidden
        ]

    def list_favorites(self):
        return [row for row in self.rows.values() if row["favorite"] and not row["hidden"]]

    def is_hidden(self, name):
        if name not in self.rows:
            raise FileNotFoundError(name)
        return self.rows[name]["hidden"]

    def find_visible_by_content(self, content_sha256):
        return next(
            (
                row
                for row in self.rows.values()
                if row["content_sha256"] == content_sha256 and not row["hidden"]
            ),
            None,
        )

    def name_for_image_id(self, image_id):
        return next(
            (name for name, row in self.rows.items() if row["image_id"] == image_id), None
        )

    def delete(self, name):
        self.rows.pop(name, None)

    def rename(self, name, new_name):
        if new_name in self.rows:
            raise FileExistsError(new_name)
        row = self.rows.get(name)
        if row is None or row["hidden"]:
            raise FileNotFoundError(name)
        self.rows[new_name] = {**self.rows.pop(name), "name": new_name}
        return self.rows[new_name]

    def update_metadata(self, name, caption, tags, hidden=False):
        row = self.get(name, hidden)
        if row is None:
            raise FileNotFoundError(name)
        row["caption"] = caption
        row["tags"] = tags
        return row

    def set_hidden(self, names, hidden):
        return self._set_flag(names, "hidden", hidden, include_hidden=True)

    def set_favorite(self, names, favorite):
        return self._set_flag(names, "favorite", favorite, include_hidden=False)

    def _set_flag(self, names, field, value, include_hidden):
        changed = []
        for name in names:
            row = self.rows.get(name)
            if row and (include_hidden or not row["hidden"]) and row[field] != value:
                row[field] = value
                changed.append(name)
        return changed

    def search(self, query, tags, match_all, limit, offset):
        stem = query.rstrip("s")
        matches = [
            {
                key: row[key]
                for key in ("name", "caption", "tags", "favorite", "updated_at", "public_url")
            }
            for row in self.rows.values()
            if not row["hidden"]
            and (stem in row["caption"].lower() or any(stem in tag for tag in row["tags"]))
        ]
        return len(matches), matches[offset : offset + limit]


class FakePublicURLRepository:
    """Provide an in-memory PublicURLRepository bound to the image rows."""

    def __init__(self, images: FakeImageRepository) -> None:
        self.images = images
        self.base_url = ""

    def get(self):
        return self.base_url

    def seed(self, default_url):
        if not self.base_url:
            self.base_url = default_url
        if self.base_url:
            self._regenerate()
        return self.base_url

    def replace(self, public_base_url):
        self.base_url = public_base_url
        return self._regenerate()

    def _regenerate(self):
        for row in self.images.rows.values():
            row["public_url"] = f"{self.base_url}{PUBLIC_IMAGE_PATH}{row['image_id']}"
        return len(self.images.rows)


class FakeCredentialRepository:
    """Provide an in-memory CredentialRepository test double."""

    def __init__(self):
        self.credentials = None

    def get(self):
        return self.credentials

    def create(self, credentials):
        if self.credentials is not None:
            return False
        self.credentials = credentials
        return True

    def update_password(self, salt, password_hash, iterations):
        self.credentials = type(self.credentials)(
            salt,
            password_hash,
            self.credentials.pin_salt,
            self.credentials.pin_hash,
            iterations,
        )


class FakeAnalyzer:
    """Return deterministic image analysis without calling Ollama."""

    model = "test-vision"

    def analyze(self, data):
        del data
        return ImageAnalysis(caption="A tabby cat on a sofa.", tags=["cat", "sofa"])


def image_bytes(image_format="PNG", color="red", size=(4, 3)):
    """Create small valid image bytes in ``image_format``."""
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=image_format)
    return buffer.getvalue()


def png_payload(color="red", size=(4, 3)):
    """Create a small valid Base64 PNG payload."""
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")
