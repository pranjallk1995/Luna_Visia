"""In-memory test doubles for Luna Visia repositories and analysis."""

import base64
import sys
import zlib
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.core.models import ImageAnalysis
from app.data.repositories import PUBLIC_IMAGE_PATH


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

    def search(self, query, tags, match_all, limit, offset, exclude_tags=None):
        stem = query.rstrip("s")
        matches = [
            {
                key: row[key]
                for key in ("name", "caption", "tags", "favorite", "updated_at", "public_url")
            }
            for row in self.rows.values()
            if not row["hidden"]
            and (stem in row["caption"].lower() or any(stem in tag for tag in row["tags"]))
            and not set(exclude_tags or ()) & set(row["tags"])
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


def cbor(value):
    """Encode the CBOR subset used by C2PA manifests (maps, arrays, text, ints)."""
    def head(major, length):
        if length < 24:
            return bytes([major << 5 | length])
        return bytes([major << 5 | 26]) + length.to_bytes(4, "big")

    if isinstance(value, str):
        raw = value.encode()
        return head(3, len(raw)) + raw
    if isinstance(value, int):
        return head(0, value)
    if isinstance(value, list):
        return head(4, len(value)) + b"".join(cbor(item) for item in value)
    return head(5, len(value)) + b"".join(cbor(key) + cbor(item) for key, item in value.items())


def jumbf_box(kind, payload):
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def jumbf_superbox(label, *children):
    description = b"\0" * 16 + b"\x03" + label.encode() + b"\0"
    return jumbf_box(b"jumb", jumbf_box(b"jumd", description) + b"".join(children))


def c2pa_store(source_type="trainedAlgorithmicMedia", agent="ChatGPT"):
    """Build a minimal C2PA manifest store like the one in OpenAI's images."""
    actions = {
        "actions": [
            {
                "action": "c2pa.created",
                "softwareAgent": {"name": agent, "version": "gpt-image"},
                "digitalSourceType": f"http://cv.iptc.org/newscodes/digitalsourcetype/{source_type}",
            }
        ]
    }
    manifest = jumbf_superbox(
        "urn:c2pa:test",
        jumbf_superbox(
            "c2pa.assertions",
            jumbf_superbox("c2pa.actions.v2", jumbf_box(b"cbor", cbor(actions))),
        ),
        jumbf_superbox(
            "c2pa.claim.v2",
            jumbf_box(b"cbor", cbor({"claim_generator_info": {"name": "OpenAI Media Service API"}})),
        ),
    )
    return jumbf_superbox("c2pa", manifest)


def c2pa_jpeg(segment_size=40):
    """Create a JPEG whose manifest store is split across APP11 segments."""
    store = c2pa_store()
    header, body = store[:8], store[8:]
    segments = []
    for sequence, start in enumerate(range(0, len(body), segment_size), start=1):
        payload = b"JP" + (1).to_bytes(2, "big") + sequence.to_bytes(4, "big")
        payload += header + body[start : start + segment_size]
        segments.append(b"\xff\xeb" + (len(payload) + 2).to_bytes(2, "big") + payload)
    jpeg = image_bytes("JPEG", "green")
    return jpeg[:2] + b"".join(segments) + jpeg[2:]


def c2pa_png(source_type="trainedAlgorithmicMedia", agent="ChatGPT"):
    """Create a PNG carrying a minimal C2PA manifest like OpenAI's images."""
    store = c2pa_store(source_type, agent)
    png = image_bytes("PNG", "green")
    crc = zlib.crc32(b"caBX" + store).to_bytes(4, "big")
    chunk = len(store).to_bytes(4, "big") + b"caBX" + store + crc
    return png[:33] + chunk + png[33:]


def png_payload(color="red", size=(4, 3)):
    """Create a small valid Base64 PNG payload."""
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")
