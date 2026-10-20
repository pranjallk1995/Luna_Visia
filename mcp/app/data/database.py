"""PostgreSQL connection handling and idempotent schema management."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS image_metadata (
    name text PRIMARY KEY,
    image_id uuid NOT NULL DEFAULT gen_random_uuid(),
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    content_sha256 char(64) NOT NULL,
    caption text NOT NULL,
    tags text[] NOT NULL DEFAULT '{}',
    analysis_model text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    search_vector tsvector NOT NULL,
    hidden boolean NOT NULL DEFAULT false,
    favorite boolean NOT NULL DEFAULT false,
    public_url text
);
ALTER TABLE image_metadata
    ADD COLUMN IF NOT EXISTS hidden boolean NOT NULL DEFAULT false;
ALTER TABLE image_metadata
    ADD COLUMN IF NOT EXISTS favorite boolean NOT NULL DEFAULT false;
ALTER TABLE image_metadata
    ADD COLUMN IF NOT EXISTS public_url text;
ALTER TABLE image_metadata
    ADD COLUMN IF NOT EXISTS image_id uuid NOT NULL DEFAULT gen_random_uuid();
CREATE UNIQUE INDEX IF NOT EXISTS image_metadata_image_id_idx
    ON image_metadata (image_id);
UPDATE image_metadata
SET search_vector =
    setweight(to_tsvector('english', regexp_replace(name, '[._-]+', ' ', 'g')), 'A') ||
    setweight(to_tsvector('english', caption), 'A') ||
    setweight(to_tsvector('english', array_to_string(tags, ' ')), 'B');
CREATE INDEX IF NOT EXISTS image_metadata_search_idx
    ON image_metadata USING gin (search_vector);
CREATE INDEX IF NOT EXISTS image_metadata_tags_idx
    ON image_metadata USING gin (tags);
CREATE INDEX IF NOT EXISTS image_metadata_sha_idx
    ON image_metadata (content_sha256);
CREATE INDEX IF NOT EXISTS image_metadata_hidden_idx
    ON image_metadata (hidden, updated_at DESC);
CREATE INDEX IF NOT EXISTS image_metadata_favorite_idx
    ON image_metadata (favorite, hidden, updated_at DESC);
CREATE TABLE IF NOT EXISTS vault_security (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    password_salt bytea NOT NULL,
    password_hash bytea NOT NULL,
    reset_pin_salt bytea NOT NULL,
    reset_pin_hash bytea NOT NULL,
    iterations integer NOT NULL CHECK (iterations >= 100000),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS vault_config (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    public_base_url text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
"""


class Database:
    """Open short-lived transactional connections that return dict rows."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        """Yield one connection; its transaction commits when the block succeeds."""
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            yield connection

    def initialize_schema(self) -> None:
        """Create or upgrade the idempotent Luna Visia database schema."""
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            connection.execute(SCHEMA_SQL)
