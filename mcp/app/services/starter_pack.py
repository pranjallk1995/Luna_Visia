"""The starter pack: the starter library as one file, for a fresh install to begin with.

Exporting writes the owner's starter images that Visia knows are AI-made (tagged
``ai info``) or are Luna Vibia's cover art (``cover art``) to a ZIP: a manifest with each
image's caption, tags and SigLIP 2 embedding, and the files. Any other starter image,
such as a photo of a real person, stays out of the pack, which is published for anyone.

A fresh install downloads the pack on its first start and adds it to the owner's
gallery as their starter images, so everyone begins with them, already described and
searchable, without the vision model looking at each one. Whether an install is fresh
is decided before the server answers anything, so an image another app stores while
the pack downloads cannot make it look used. An install that had images, or has loaded
the pack, never loads it again; one that has not finished tries again on its next start.

    python -m app.services.starter_pack export /data/downloads/luna-visia-starter-pack.zip
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.core import tenancy
from app.core.config import AppConfig
from app.data.database import Database
from app.data.repositories import PUBLIC_IMAGE_PATH

logger = logging.getLogger(__name__)

APP = "visia"
PACK_FORMAT = 1
MANIFEST = "manifest.json"
FILES = "images/"
# Only these images go in a public pack: Visia found them AI-made, or they are covers.
PUBLIC_TAGS = ["ai info", "cover art"]
PACKED_COLUMNS = (
    "name", "size_bytes", "content_sha256", "caption", "tags", "analysis_model",
    "showcase_rank", "embedding", "embedding_model",
)
SEARCH_VECTOR = (
    "setweight(to_tsvector('english', regexp_replace(%(name)s, '[._-]+', ' ', 'g')), 'A') || "
    "setweight(to_tsvector('english', %(caption)s), 'A') || "
    "setweight(to_tsvector('english', array_to_string(%(tags)s::text[], ' ')), 'B')"
)
# The states of an install's pack: still to load, loaded, or never needed.
PENDING, LOADED, SKIPPED = "pending", "loaded", "skipped"
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0)
ATTEMPTS = 10
RETRY_SECONDS = 60.0


class StarterPack:
    def __init__(self, database: Database, image_dir: Path, allowed_suffixes: set[str]) -> None:
        self.database = database
        self.image_dir = image_dir
        self.allowed_suffixes = allowed_suffixes

    @property
    def folder(self) -> Path:
        return self.image_dir / tenancy.space_name(tenancy.OWNER)

    def export(self, path: Path) -> int:
        """Write the owner's public starter images to a pack at ``path``; returns how many."""
        with tenancy.acting_as(tenancy.OWNER), self.database.connect() as connection:
            rows = connection.execute(
                """SELECT name, size_bytes, content_sha256, caption, tags, analysis_model,
                          showcase_rank, embedding::text AS embedding, embedding_model
                   FROM image_metadata
                   WHERE starter AND NOT hidden AND NOT favorite AND tags && %s::text[]
                   ORDER BY name""",
                (PUBLIC_TAGS,),
            ).fetchall()
        images = []
        partial = path.with_name(path.name + ".part")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_STORED) as pack:
            for row in rows:
                file = self.folder / row["name"]
                data = file.read_bytes() if file.is_file() else None
                if data is None or hashlib.sha256(data).hexdigest() != row["content_sha256"].strip():
                    logger.warning("Leaving %s out: its file is missing or does not match.", row["name"])
                    continue
                pack.writestr(FILES + row["name"], data)
                images.append({column: row[column] for column in PACKED_COLUMNS})
            pack.writestr(MANIFEST, json.dumps({"app": APP, "format": PACK_FORMAT, "images": images}, indent=1))
        partial.replace(path)
        return len(images)

    def decide(self, url: str) -> bool:
        """Note whether this install still has the pack to load; call before serving anything."""
        if not url:
            return False
        with tenancy.acting_as(tenancy.OWNER), self.database.connect() as connection:
            state = connection.execute("SELECT state FROM starter_pack").fetchone()
            if state is None:
                used = connection.execute("SELECT EXISTS (SELECT 1 FROM image_metadata) AS used").fetchone()
                state = {"state": SKIPPED if used["used"] else PENDING}
                connection.execute(
                    "INSERT INTO starter_pack (state, source) VALUES (%s, %s)", (state["state"], url)
                )
        return state["state"] == PENDING

    def load_in_background(self, url: str, after: Any = None) -> None:
        """Load the pack, retrying a failed download for a while; then call ``after``."""
        for attempt in range(ATTEMPTS):
            try:
                count = self.load_from(url)
            except Exception:  # noqa: BLE001 - logged, and the next start tries again.
                logger.exception("Could not load the starter pack (attempt %d of %d).", attempt + 1, ATTEMPTS)
                time.sleep(RETRY_SECONDS)
                continue
            logger.info("Loaded %d starter images from the starter pack.", count)
            if after is not None:
                after()
            return
        logger.warning("Gave up on the starter pack for now; the next start tries again.")

    def load_from(self, url: str) -> int:
        with tempfile.TemporaryDirectory() as scratch:
            pack = Path(scratch) / "pack.zip"
            with httpx.stream("GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT) as response:
                response.raise_for_status()
                with pack.open("wb") as out:
                    for chunk in response.iter_bytes(1 << 20):
                        out.write(chunk)
            return self.load(pack)

    def load(self, path: Path) -> int:
        """Add a pack's images to the owner's gallery as starter images; returns how many.

        A name already in use keeps its image. Images whose bytes do not match the
        manifest are left out.
        """
        given = 0
        with zipfile.ZipFile(path) as pack, tenancy.acting_as(tenancy.OWNER):
            manifest = json.loads(pack.read(MANIFEST))
            if manifest.get("app") != APP or manifest.get("format") != PACK_FORMAT:
                raise ValueError("This is not a Luna Visia starter pack this version can read.")
            self.folder.mkdir(parents=True, exist_ok=True)
            with self.database.connect() as connection:
                base = connection.execute("SELECT public_base_url FROM vault_config").fetchone()
                for image in manifest["images"]:
                    name = str(image["name"])
                    if Path(name).name != name or name.startswith(".") or Path(name).suffix.lower() not in self.allowed_suffixes:
                        logger.warning("Leaving out a starter image with an unusable name: %r", name)
                        continue
                    data = pack.read(FILES + name)
                    if hashlib.sha256(data).hexdigest() != image["content_sha256"].strip():
                        logger.warning("Leaving out starter image %s: it does not match the manifest.", name)
                        continue
                    image_id = uuid4()
                    values = {
                        **{column: image.get(column) for column in PACKED_COLUMNS},
                        "name": name,
                        "size_bytes": len(data),
                        "image_id": image_id,
                        "public_url": f"{base['public_base_url']}{PUBLIC_IMAGE_PATH}{image_id}" if base else None,
                    }
                    added = connection.execute(
                        f"""INSERT INTO image_metadata
                               (name, image_id, public_url, size_bytes, content_sha256, caption, tags,
                                analysis_model, showcase_rank, embedding, embedding_model, starter,
                                search_vector)
                            VALUES (%(name)s, %(image_id)s, %(public_url)s, %(size_bytes)s,
                                    %(content_sha256)s, %(caption)s, %(tags)s, %(analysis_model)s,
                                    %(showcase_rank)s, %(embedding)s::vector, %(embedding_model)s, true,
                                    {SEARCH_VECTOR})
                            ON CONFLICT (name) DO NOTHING RETURNING name""",
                        values,
                    ).fetchone()
                    if added:
                        write_file(self.folder / name, data)
                        given += 1
                connection.execute("UPDATE starter_pack SET state = %s, loaded_at = now()", (LOADED,))
        return given


def write_file(target: Path, data: bytes) -> None:
    """Write a whole file, so a reader never sees half of one."""
    partial = target.with_name(f".{target.name}.{uuid4().hex}.part")
    partial.write_bytes(data)
    os.replace(partial, target)


def main(arguments: list[str]) -> None:
    if len(arguments) != 2 or arguments[0] != "export":
        raise SystemExit("Usage: python -m app.services.starter_pack export <pack.zip>")
    logging.basicConfig(level=logging.INFO)
    config = AppConfig.from_env()
    pack = StarterPack(Database(config.database_url), config.image_dir, set(config.allowed_formats))
    count = pack.export(Path(arguments[1]))
    print(f"Wrote {count} starter images to {arguments[1]}.")


if __name__ == "__main__":
    main(sys.argv[1:])
