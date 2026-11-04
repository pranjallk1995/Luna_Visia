"""The starter library: images the owner chooses for everyone to begin with.

Each person gets their own copy, so they can rename or delete it without touching
anyone else's. Files are hard links on the one volume, which takes no extra space, and
every change to a stored file replaces it rather than writing into it, so a copy cannot
alter the original. Hidden images and favorites are never included.
"""

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from psycopg import sql

from app.core import tenancy
from app.data.database import Database
from app.data.repositories import PUBLIC_IMAGE_PATH

logger = logging.getLogger(__name__)

# What a copy keeps from the original. It gets its own ID and public link, and starts
# as neither hidden nor a favorite.
COPIED_COLUMNS = (
    "size_bytes", "content_sha256", "caption", "tags", "analysis_model", "created_at", "updated_at",
    "search_vector", "showcase_rank", "embedding", "embedding_model",
)


class StarterLibrary:
    def __init__(self, database: Database, image_dir: Path) -> None:
        self.database = database
        self.image_dir = image_dir

    def choose(self, names: list[str] | None, included: bool) -> dict[str, Any]:
        """Add images to the library, or take them out; no names means every eligible image."""
        tenancy.require_owner("Choosing the starter images")
        chosen = (
            "NOT hidden AND NOT favorite" if names is None else "name = ANY(%s) AND NOT hidden AND NOT favorite"
        )
        params = (included,) if names is None else (included, names)
        with self.database.connect() as connection:
            changed = connection.execute(
                f"UPDATE image_metadata SET starter = %s WHERE {chosen}", params
            ).rowcount
        delivered = self.deliver_to_everyone() if included else 0
        return {"status": "starter_images_updated", "changed": changed, "copies_delivered": delivered}

    def deliver_to_everyone(self) -> int:
        delivered = 0
        for user_id in self.database.tenants.users():
            if user_id != tenancy.OWNER:
                self.database.tenants.ensure(user_id)
                delivered += self.deliver(user_id)
        return delivered

    def deliver(self, user_id: str) -> int:
        """Copy the images this person has not been given yet; returns how many.

        Each image is given once, so one they deleted does not come back. A name they
        already use keeps their own image.
        """
        if user_id == tenancy.OWNER:
            return 0
        source = sql.Identifier(tenancy.space_name(tenancy.OWNER))
        target = sql.Identifier(tenancy.space_name(user_id))
        columns = sql.SQL(", ").join(map(sql.Identifier, COPIED_COLUMNS))
        insert = sql.SQL(
            """INSERT INTO {target}.image_metadata (name, image_id, public_url, {columns})
               SELECT name, %s, %s, {columns} FROM {source}.image_metadata WHERE image_id = %s
               ON CONFLICT (name) DO NOTHING RETURNING name"""
        ).format(target=target, source=source, columns=columns)
        given = 0
        folder = self.image_dir / tenancy.space_name(user_id)
        with self.database.transaction() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"starter:{user_id}",)
            )
            base = connection.execute(
                sql.SQL("SELECT public_base_url FROM {}.vault_config").format(target)
            ).fetchone()
            rows = connection.execute(
                sql.SQL(
                    """SELECT s.name, s.image_id FROM {source}.image_metadata s
                       WHERE s.starter AND NOT s.hidden AND NOT s.favorite AND NOT EXISTS (
                           SELECT 1 FROM {target}.starter_received r WHERE r.source_image_id = s.image_id)"""
                ).format(source=source, target=target)
            ).fetchall()
            for row in rows:
                original = self.image_dir / tenancy.space_name(tenancy.OWNER) / row["name"]
                if not original.is_file():
                    logger.warning("Starter image %s has no file; skipping it.", row["name"])
                    continue
                image_id = uuid4()
                public_url = f"{base['public_base_url']}{PUBLIC_IMAGE_PATH}{image_id}" if base else None
                if connection.execute(insert, (image_id, public_url, row["image_id"])).fetchone():
                    folder.mkdir(exist_ok=True)
                    link_or_keep(original, folder / row["name"])
                    given += 1
                connection.execute(
                    sql.SQL("INSERT INTO {}.starter_received (source_image_id) VALUES (%s)").format(target),
                    (row["image_id"],),
                )
        if given:
            logger.info("Gave %d starter images to one person.", given)
        return given


def link_or_keep(original: Path, copy: Path) -> None:
    try:
        copy.hardlink_to(original)
    except FileExistsError:
        pass  # An earlier delivery that did not finish linked it already.
