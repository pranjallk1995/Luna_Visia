"""PostgreSQL persistence for Luna Visia, one repository per aggregate."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

import psycopg

from .database import Database
from .models import ImageRecord

Row = dict[str, Any]

PUBLIC_IMAGE_PATH = "/api/public/images/"


def search_vector_sql(name: str, caption: str, tags: str) -> str:
    """Build the weighted full-text vector over filename, caption, and tags."""
    return (
        f"setweight(to_tsvector('english', regexp_replace({name}, '[._-]+', ' ', 'g')), 'A') || "
        f"setweight(to_tsvector('english', {caption}), 'A') || "
        f"setweight(to_tsvector('english', {tags}), 'B')"
    )


def public_url_sql(base_url_parameter: str) -> str:
    """Build a stable public URL from the configured base and immutable image ID."""
    return f"{base_url_parameter} || '{PUBLIC_IMAGE_PATH}' || image_id::text"


class Repository:
    """Shared query helpers that serialize rows into JSON-friendly dicts."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def serialize(row: Row | None) -> Row | None:
        if row is None:
            return None
        return {
            key: (
                value.isoformat()
                if hasattr(value, "isoformat")
                else str(value) if isinstance(value, UUID) else value
            )
            for key, value in row.items()
        }

    def fetch_one(self, sql: str, params: Any = ()) -> Row | None:
        with self.database.connect() as connection:
            return self.serialize(connection.execute(sql, params).fetchone())

    def fetch_all(self, sql: str, params: Any = ()) -> list[Row]:
        with self.database.connect() as connection:
            return [self.serialize(row) for row in connection.execute(sql, params).fetchall()]


class ImageRepository(Repository):
    """Image metadata, visibility, favorites, and the full-text search index."""

    COLUMNS = (
        "name, size_bytes, content_sha256, caption, tags, analysis_model, "
        "updated_at, hidden, favorite, public_url, image_id"
    )

    def upsert(self, record: ImageRecord, public_base_url: str) -> Row:
        """Insert or replace metadata and assign its stable public URL."""
        vector = search_vector_sql(
            "%(name)s", "%(caption)s", "array_to_string(%(tags)s::text[], ' ')"
        )
        with self.database.connect() as connection:
            connection.execute(
                f"""INSERT INTO image_metadata
                       (name, size_bytes, content_sha256, caption, tags,
                        analysis_model, search_vector)
                    VALUES (%(name)s, %(size_bytes)s, %(content_sha256)s,
                            %(caption)s, %(tags)s, %(analysis_model)s, {vector})
                    ON CONFLICT (name) DO UPDATE SET
                        size_bytes = EXCLUDED.size_bytes,
                        content_sha256 = EXCLUDED.content_sha256,
                        caption = EXCLUDED.caption,
                        tags = EXCLUDED.tags,
                        analysis_model = EXCLUDED.analysis_model,
                        search_vector = EXCLUDED.search_vector,
                        updated_at = now()""",
                asdict(record),
            )
            row = connection.execute(
                f"""UPDATE image_metadata SET public_url = {public_url_sql('%s')}
                    WHERE name = %s RETURNING {self.COLUMNS}""",
                (public_base_url, record.name),
            ).fetchone()
        return self.serialize(row)

    def get(self, name: str, hidden: bool | None = False) -> Row | None:
        """Return one record, restricted to a collection unless ``hidden`` is None."""
        return self.fetch_one(
            f"""SELECT {self.COLUMNS} FROM image_metadata
                WHERE name = %s AND (%s::boolean IS NULL OR hidden = %s)""",
            (name, hidden, hidden),
        )

    def list(self, hidden: bool | None = False) -> list[Row]:
        """List visible, hidden, or all records newest first."""
        return self.fetch_all(
            f"""SELECT {self.COLUMNS} FROM image_metadata
                WHERE %s::boolean IS NULL OR hidden = %s
                ORDER BY updated_at DESC, name""",
            (hidden, hidden),
        )

    def list_favorites(self) -> list[Row]:
        """List visible favorite images newest first."""
        return self.fetch_all(
            f"""SELECT {self.COLUMNS} FROM image_metadata
                WHERE favorite = true AND hidden = false
                ORDER BY updated_at DESC, name"""
        )

    def is_hidden(self, name: str) -> bool:
        """Return whether the named image belongs to the Hidden Vault."""
        row = self.fetch_one("SELECT hidden FROM image_metadata WHERE name = %s", (name,))
        if row is None:
            raise FileNotFoundError(f"Image {name!r} was not found.")
        return bool(row["hidden"])

    def find_visible_by_content(self, content_sha256: str) -> Row | None:
        """Return the newest visible image with identical bytes, if any."""
        return self.fetch_one(
            f"""SELECT {self.COLUMNS} FROM image_metadata
                WHERE content_sha256 = %s AND hidden = false
                ORDER BY updated_at DESC, name LIMIT 1""",
            (content_sha256,),
        )

    def name_for_image_id(self, image_id: str) -> str | None:
        """Resolve an immutable public identifier to its current filename."""
        row = self.fetch_one(
            "SELECT name FROM image_metadata WHERE image_id::text = %s", (image_id,)
        )
        return row["name"] if row else None

    def delete(self, name: str) -> None:
        with self.database.connect() as connection:
            connection.execute("DELETE FROM image_metadata WHERE name = %s", (name,))

    def rename(self, name: str, new_name: str) -> Row:
        """Rename one visible record while preserving all other fields."""
        vector = search_vector_sql("%s", "caption", "array_to_string(tags, ' ')")
        try:
            row = self.fetch_one(
                f"""UPDATE image_metadata
                    SET name = %s, search_vector = {vector}, updated_at = now()
                    WHERE name = %s AND hidden = false
                    RETURNING {self.COLUMNS}""",
                (new_name, new_name, name),
            )
        except psycopg.errors.UniqueViolation as error:
            raise FileExistsError(f"Image {new_name!r} already exists.") from error
        if row is None:
            raise FileNotFoundError(f"Visible image {name!r} was not found.")
        return row

    def update_metadata(
        self, name: str, caption: str, tags: list[str], hidden: bool = False
    ) -> Row:
        """Update editable metadata and rebuild its full-text search vector."""
        vector = search_vector_sql("name", "%s", "array_to_string(%s::text[], ' ')")
        row = self.fetch_one(
            f"""UPDATE image_metadata
                SET caption = %s, tags = %s, search_vector = {vector}, updated_at = now()
                WHERE name = %s AND hidden = %s
                RETURNING {self.COLUMNS}""",
            (caption, tags, caption, tags, name, hidden),
        )
        if row is None:
            raise FileNotFoundError(f"Image {name!r} was not found.")
        return row

    def set_hidden(self, names: list[str], hidden: bool) -> list[str]:
        """Persist hidden state and return the names that actually changed."""
        if not names:
            return []
        rows = self.fetch_all(
            """UPDATE image_metadata SET hidden = %s, updated_at = now()
               WHERE name = ANY(%s) AND hidden <> %s RETURNING name""",
            (hidden, names, hidden),
        )
        return [row["name"] for row in rows]

    def set_favorite(self, names: list[str], favorite: bool) -> list[str]:
        """Set favorite state for visible images and return the changed names."""
        if not names:
            return []
        rows = self.fetch_all(
            """UPDATE image_metadata SET favorite = %s, updated_at = now()
               WHERE name = ANY(%s) AND hidden = false AND favorite <> %s
               RETURNING name""",
            (favorite, names, favorite),
        )
        return [row["name"] for row in rows]

    def search(
        self,
        normalized_query: str,
        tags: list[str],
        match_all_tags: bool,
        limit: int,
        offset: int,
    ) -> tuple[int, list[Row]]:
        """Query persisted full-text and tag indexes without opening images."""
        clauses = ["hidden = false"]
        filters: list[Any] = []
        if normalized_query:
            clauses.append("search_vector @@ websearch_to_tsquery('english', %s)")
            filters.append(normalized_query)
        if tags:
            clauses.append(f"tags {'@>' if match_all_tags else '&&'} %s")
            filters.append(tags)
        rank_sql = (
            "ts_rank_cd(search_vector, websearch_to_tsquery('english', %s))"
            if normalized_query
            else "0"
        )
        params = ([normalized_query] if normalized_query else []) + filters + [limit, offset]
        rows = self.fetch_all(
            f"""SELECT name, caption, tags, favorite, updated_at, public_url,
                       count(*) OVER() AS total_count, {rank_sql} AS rank
                FROM image_metadata WHERE {' AND '.join(clauses)}
                ORDER BY rank DESC, updated_at DESC, name LIMIT %s OFFSET %s""",
            params,
        )
        count = rows[0]["total_count"] if rows else 0
        results = [
            {
                key: row[key]
                for key in ("name", "caption", "tags", "favorite", "updated_at", "public_url")
            }
            for row in rows
        ]
        return count, results


class PublicURLRepository(Repository):
    """The mutable public origin and every image URL derived from it."""

    SELECT_BASE_URL = "SELECT public_base_url FROM vault_config WHERE singleton = true"

    def get(self) -> str:
        """Return the currently persisted public HTTPS origin."""
        row = self.fetch_one(self.SELECT_BASE_URL)
        return row["public_base_url"] if row else ""

    def seed(self, default_url: str) -> str:
        """Store ``default_url`` only when unset, then refresh stored image URLs."""
        with self.database.connect() as connection:
            if default_url:
                connection.execute(
                    """INSERT INTO vault_config (singleton, public_base_url)
                       VALUES (true, %s) ON CONFLICT (singleton) DO NOTHING""",
                    (default_url,),
                )
            row = connection.execute(self.SELECT_BASE_URL).fetchone()
            public_base_url = row["public_base_url"] if row else ""
            if public_base_url:
                self._regenerate(connection, public_base_url)
        return public_base_url

    def replace(self, public_base_url: str) -> int:
        """Atomically store the origin and regenerate every image URL."""
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO vault_config (singleton, public_base_url)
                   VALUES (true, %s)
                   ON CONFLICT (singleton) DO UPDATE SET
                       public_base_url = EXCLUDED.public_base_url,
                       updated_at = now()""",
                (public_base_url,),
            )
            return self._regenerate(connection, public_base_url)

    @staticmethod
    def _regenerate(connection: psycopg.Connection, public_base_url: str) -> int:
        cursor = connection.execute(
            f"UPDATE image_metadata SET public_url = {public_url_sql('%s')}",
            (public_base_url,),
        )
        return cursor.rowcount


@dataclass(frozen=True)
class StoredCredentials:
    """Salted Hidden Vault password and reset-PIN hashes."""

    password_salt: bytes
    password_hash: bytes
    pin_salt: bytes
    pin_hash: bytes
    iterations: int


class CredentialRepository(Repository):
    """The single Hidden Vault credential record."""

    def get(self) -> StoredCredentials | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT password_salt, password_hash, reset_pin_salt AS pin_salt,
                          reset_pin_hash AS pin_hash, iterations
                   FROM vault_security WHERE singleton = true"""
            ).fetchone()
        return StoredCredentials(**row) if row else None

    def create(self, credentials: StoredCredentials) -> bool:
        """Persist credentials only when none exist; report whether they were stored."""
        with self.database.connect() as connection:
            row = connection.execute(
                """INSERT INTO vault_security
                       (singleton, password_salt, password_hash, reset_pin_salt,
                        reset_pin_hash, iterations)
                   VALUES (true, %s, %s, %s, %s, %s)
                   ON CONFLICT (singleton) DO NOTHING RETURNING singleton""",
                (
                    credentials.password_salt,
                    credentials.password_hash,
                    credentials.pin_salt,
                    credentials.pin_hash,
                    credentials.iterations,
                ),
            ).fetchone()
        return row is not None

    def update_password(self, salt: bytes, password_hash: bytes, iterations: int) -> None:
        """Replace the stored password-derived credential material."""
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE vault_security SET password_salt = %s,
                       password_hash = %s, iterations = %s, updated_at = now()
                   WHERE singleton = true""",
                (salt, password_hash, iterations),
            )
