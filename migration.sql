-- SQLite migration
-- Rebuild is required when code was declared as UNIQUE in the table schema.
PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

DROP TABLE IF EXISTS movies_new;

CREATE TABLE movies_new (
    id INTEGER NOT NULL,
    code VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    message_id BIGINT,
    channel_id VARCHAR(255),
    views INTEGER NOT NULL DEFAULT 0,
    content_type VARCHAR(16) NOT NULL DEFAULT 'movie',
    episode INTEGER,
    PRIMARY KEY (id)
);

INSERT INTO movies_new (
    id,
    code,
    title,
    message_id,
    channel_id,
    views,
    content_type,
    episode
)
SELECT
    id,
    code,
    title,
    message_id,
    channel_id,
    COALESCE(views, 0),
    'movie',
    NULL
FROM movies;

DROP TABLE movies;
ALTER TABLE movies_new RENAME TO movies;

DROP INDEX IF EXISTS ix_movies_code;
DROP INDEX IF EXISTS uq_movies_code;
CREATE INDEX IF NOT EXISTS ix_movies_code ON movies (code);

COMMIT;

PRAGMA foreign_keys = ON;


-- PostgreSQL migration
ALTER TABLE movies
    ADD COLUMN IF NOT EXISTS content_type VARCHAR(16) NOT NULL DEFAULT 'movie';

ALTER TABLE movies
    ADD COLUMN IF NOT EXISTS episode INTEGER;

DO $$
DECLARE
    unique_constraint_name text;
BEGIN
    FOR unique_constraint_name IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE t.relname = 'movies'
          AND n.nspname = current_schema()
          AND c.contype = 'u'
          AND c.conkey = ARRAY[
              (
                  SELECT a.attnum
                  FROM pg_attribute a
                  WHERE a.attrelid = t.oid
                    AND a.attname = 'code'
                    AND NOT a.attisdropped
              )
          ]::smallint[]
    LOOP
        EXECUTE format('ALTER TABLE movies DROP CONSTRAINT %I', unique_constraint_name);
    END LOOP;
END $$;

DROP INDEX IF EXISTS uq_movies_code;
DROP INDEX IF EXISTS movies_code_key;
DROP INDEX IF EXISTS ix_movies_code;
CREATE INDEX IF NOT EXISTS ix_movies_code ON movies (code);
