import asyncio
import logging
from collections.abc import AsyncGenerator
from time import perf_counter

from sqlalchemy import event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)

_db_initialized = False
_init_db_lock = asyncio.Lock()


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    global _db_initialized

    if _db_initialized:
        logger.info("init_db allaqachon bajarilgan, qayta ishga tushirilmadi.")
        return

    async with _init_db_lock:
        if _db_initialized:
            logger.info("init_db allaqachon bajarilgan, qayta ishga tushirilmadi.")
            return

        _import_models()

        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            await _migrate_channels_table()
            await _migrate_movies_table()
            await _migrate_movies_serial_support()
            await _migrate_users_table()
        except RuntimeError:
            raise
        except Exception as exc:
            await engine.dispose()
            raise RuntimeError(_format_database_error(exc)) from exc

        _db_initialized = True


def _validate_database_url(database_url: str) -> URL:
    if not database_url.strip():
        raise RuntimeError(
            "DATABASE_URL bo'sh. .env ichida sqlite+aiosqlite:///bot.db yoki "
            "postgresql+asyncpg://user:password@host:5432/database ko'rinishida yozing."
        )

    try:
        url = make_url(database_url)
    except ArgumentError as exc:
        raise RuntimeError(
            "DATABASE_URL noto'g'ri formatda. Namuna: "
            "sqlite+aiosqlite:///bot.db yoki "
            "postgresql+asyncpg://user:password@host:5432/database"
        ) from exc

    driver = url.drivername
    if driver.startswith("sqlite"):
        if driver != "sqlite+aiosqlite":
            raise RuntimeError(
                "SQLite uchun async driver ishlating: DATABASE_URL=sqlite+aiosqlite:///bot.db"
            )
        return url

    if driver == "postgres" or driver.startswith("postgresql"):
        if driver != "postgresql+asyncpg":
            raise RuntimeError(
                "PostgreSQL uchun asyncpg driver shart: "
                "DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database"
            )
        if not url.database:
            raise RuntimeError(
                "PostgreSQL DATABASE_URL ichida database nomi ko'rsatilmagan. "
                "Namuna: postgresql+asyncpg://user:password@host:5432/database"
            )
        return url

    raise RuntimeError(
        "Faqat SQLite yoki PostgreSQL qo'llab-quvvatlanadi. "
        "SQLite: sqlite+aiosqlite:///bot.db. "
        "PostgreSQL: postgresql+asyncpg://user:password@host:5432/database."
    )


def _create_engine(database_url: URL):
    try:
        kwargs = {"echo": False}
        if database_url.get_backend_name() == "postgresql":
            kwargs["pool_pre_ping"] = True
        return create_async_engine(database_url, **kwargs)
    except (ArgumentError, NoSuchModuleError) as exc:
        raise RuntimeError(_format_database_error(exc)) from exc


def _import_models() -> None:
    from app.models import Channel, Movie, Search, User  # noqa: F401


def _format_database_error(exc: BaseException) -> str:
    if DATABASE_URL.get_backend_name() == "postgresql":
        return (
            "PostgreSQL bazasiga ulanish yoki jadvallarni yaratishda xatolik yuz berdi. "
            "DATABASE_URL quyidagi formatda bo'lishi kerak: "
            "postgresql+asyncpg://user:password@host:5432/database. "
            "Host, port, user, password, database nomi va asyncpg o'rnatilganini tekshiring. "
            f"Asl xato: {exc.__class__.__name__}: {exc}"
        )

    return (
        "SQLite bazasini ishga tushirishda xatolik yuz berdi. "
        "DATABASE_URL=sqlite+aiosqlite:///bot.db formatini tekshiring. "
        f"Asl xato: {exc.__class__.__name__}: {exc}"
    )


def _configure_query_logging(async_engine) -> None:
    @event.listens_for(async_engine.sync_engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        context._query_start_time = perf_counter()

    @event.listens_for(async_engine.sync_engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        started_at = getattr(context, "_query_start_time", None)
        if started_at is None:
            return

        clean_statement = statement.strip() if statement else ""
        operation = clean_statement.split(None, 1)[0].upper() if clean_statement else "UNKNOWN"
        elapsed_ms = (perf_counter() - started_at) * 1000
        logger.info(
            "DB query took %.2f ms operation=%s executemany=%s",
            elapsed_ms,
            operation,
            executemany,
        )


DATABASE_URL = _validate_database_url(settings.database_url)
engine = _create_engine(DATABASE_URL)
_configure_query_logging(engine)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
async_session = AsyncSessionLocal


def _normalize_migrated_username(value: str) -> str:
    clean_value = value.strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if clean_value.startswith(prefix):
            clean_value = clean_value.removeprefix(prefix)
            break
    clean_value = clean_value.strip("/")
    if clean_value.startswith("@"):
        return clean_value
    return f"@{clean_value}"


async def _migrate_channels_table() -> None:
    if engine.dialect.name == "sqlite":
        await _migrate_sqlite_channels_table()
    elif engine.dialect.name == "postgresql":
        await _migrate_postgresql_channels_table()


async def _migrate_movies_table() -> None:
    if engine.dialect.name == "sqlite":
        await _migrate_sqlite_movies_table()
    elif engine.dialect.name == "postgresql":
        await _migrate_postgresql_movies_table()


async def _migrate_movies_serial_support() -> None:
    """Add content_type / episode columns and drop the UNIQUE constraint on code."""
    if engine.dialect.name == "sqlite":
        await _migrate_sqlite_movies_serial_support()
    elif engine.dialect.name == "postgresql":
        await _migrate_postgresql_movies_serial_support()


async def _migrate_users_table() -> None:
    if engine.dialect.name == "sqlite":
        await _migrate_sqlite_users_table()
    elif engine.dialect.name == "postgresql":
        await _migrate_postgresql_users_table()


async def _migrate_sqlite_users_table() -> None:
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(users)"))
        columns = {row[1] for row in result.fetchall()}
        if not columns:
            return

        if "last_movie_id" not in columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN last_movie_id INTEGER"))
        if "last_search_type" not in columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN last_search_type VARCHAR(32)"))


async def _migrate_postgresql_users_table() -> None:
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'users'
                """
            )
        )
        columns = {row[0] for row in result.fetchall()}
        if not columns:
            logger.warning("users jadvali topilmadi, migratsiya o'tkazilmadi.")
            return

        if "last_movie_id" not in columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN last_movie_id INTEGER"))
        if "last_search_type" not in columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN last_search_type VARCHAR(32)"))


async def _migrate_sqlite_movies_table() -> None:
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(movies)"))
        rows = result.fetchall()
        if not rows:
            return

        columns = {row[1] for row in rows}
        required_columns = {
            "id",
            "code",
            "title",
            "content_type",
            "episode",
            "description",
            "movie_link",
            "archive_chat_id",
            "archive_message_id",
            "views_count",
            "created_at",
        }
        legacy_columns = {"message_id", "channel_id", "views"}

        should_rebuild = (
            not required_columns.issubset(columns)
            or bool(legacy_columns & columns)
            or await _sqlite_movies_has_unique_code_index(conn)
        )
        if should_rebuild:
            await _rebuild_sqlite_movies_table(conn)


async def _migrate_postgresql_movies_table() -> None:
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'movies'
                """
            )
        )
        columns = {row[0] for row in result.fetchall()}
        if not columns:
            logger.warning("movies jadvali topilmadi, migratsiya o'tkazilmadi.")
            return

        if "archive_chat_id" not in columns:
            await conn.execute(text("ALTER TABLE movies ADD COLUMN archive_chat_id VARCHAR(255)"))
        if "archive_message_id" not in columns:
            await conn.execute(text("ALTER TABLE movies ADD COLUMN archive_message_id INTEGER"))
        if "movie_link" in columns:
            await conn.execute(text("ALTER TABLE movies ALTER COLUMN movie_link DROP NOT NULL"))


async def _migrate_sqlite_channels_table() -> None:
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(channels)"))
        columns = {row[1] for row in result.fetchall()}

        if "chat_id" not in columns:
            await conn.execute(text("ALTER TABLE channels ADD COLUMN chat_id BIGINT"))
        if "username" not in columns:
            await conn.execute(text("ALTER TABLE channels ADD COLUMN username VARCHAR(255)"))
        if "is_private" not in columns:
            await conn.execute(
                text("ALTER TABLE channels ADD COLUMN is_private BOOLEAN NOT NULL DEFAULT 0")
            )

        if "username_or_id" in columns:
            result = await conn.execute(
                text(
                    "SELECT id, username_or_id FROM channels "
                    "WHERE username_or_id IS NOT NULL AND username IS NULL AND chat_id IS NULL"
                )
            )
            for row in result.mappings():
                raw_value = (row["username_or_id"] or "").strip()
                if not raw_value:
                    continue

                if raw_value.lstrip("-").isdigit():
                    await conn.execute(
                        text(
                            "UPDATE channels SET chat_id = :chat_id, is_private = 1 "
                            "WHERE id = :id"
                        ),
                        {"chat_id": int(raw_value), "id": row["id"]},
                    )
                else:
                    await conn.execute(
                        text(
                            "UPDATE channels SET username = :username, is_private = 0 "
                            "WHERE id = :id"
                        ),
                        {"username": _normalize_migrated_username(raw_value), "id": row["id"]},
                    )

        result = await conn.execute(text("PRAGMA table_info(channels)"))
        current_columns = [row[1] for row in result.fetchall()]
        desired_columns = [
            "id",
            "title",
            "username",
            "chat_id",
            "invite_link",
            "is_private",
            "is_active",
            "created_at",
        ]
        if current_columns != desired_columns:
            await _rebuild_sqlite_channels_table(conn)


async def _migrate_postgresql_channels_table() -> None:
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'channels'
                """
            )
        )
        columns = {row[0] for row in result.fetchall()}
        if not columns:
            logger.warning("channels jadvali topilmadi, migratsiya o'tkazilmadi.")
            return

        if "chat_id" not in columns:
            await conn.execute(text("ALTER TABLE channels ADD COLUMN chat_id BIGINT"))
        if "username" not in columns:
            await conn.execute(text("ALTER TABLE channels ADD COLUMN username VARCHAR(255)"))
        if "is_private" not in columns:
            await conn.execute(
                text("ALTER TABLE channels ADD COLUMN is_private BOOLEAN NOT NULL DEFAULT false")
            )

        if "username_or_id" not in columns:
            return

        result = await conn.execute(
            text(
                "SELECT id, username_or_id FROM channels "
                "WHERE username_or_id IS NOT NULL AND username IS NULL AND chat_id IS NULL"
            )
        )
        for row in result.mappings():
            raw_value = (row["username_or_id"] or "").strip()
            if not raw_value:
                continue

            if raw_value.lstrip("-").isdigit():
                await conn.execute(
                    text(
                        "UPDATE channels SET chat_id = :chat_id, is_private = true "
                        "WHERE id = :id"
                    ),
                    {"chat_id": int(raw_value), "id": row["id"]},
                )
            else:
                await conn.execute(
                    text(
                        "UPDATE channels SET username = :username, is_private = false "
                        "WHERE id = :id"
                    ),
                    {"username": _normalize_migrated_username(raw_value), "id": row["id"]},
                )

        await conn.execute(text("ALTER TABLE channels DROP COLUMN username_or_id"))


async def _rebuild_sqlite_channels_table(conn) -> None:
    await conn.execute(text("DROP TABLE IF EXISTS channels_new"))
    await conn.execute(
        text(
            """
            CREATE TABLE channels_new (
                id INTEGER NOT NULL,
                title VARCHAR(255) NOT NULL,
                username VARCHAR(255),
                chat_id BIGINT,
                invite_link TEXT NOT NULL,
                is_private BOOLEAN NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                PRIMARY KEY (id)
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            INSERT INTO channels_new (
                id,
                title,
                username,
                chat_id,
                invite_link,
                is_private,
                is_active,
                created_at
            )
            SELECT
                id,
                title,
                username,
                chat_id,
                invite_link,
                COALESCE(is_private, 0),
                COALESCE(is_active, 1),
                created_at
            FROM channels
            """
        )
    )
    await conn.execute(text("DROP TABLE channels"))
    await conn.execute(text("ALTER TABLE channels_new RENAME TO channels"))
    logger.info("channels jadvali yangi sxemaga o'tkazildi.")


async def _sqlite_movies_has_unique_code_index(conn) -> bool:
    result = await conn.execute(text("PRAGMA index_list(movies)"))
    for row in result.fetchall():
        index_name = row[1]
        is_unique = bool(row[2])
        if not is_unique:
            continue

        index_info = await conn.execute(text(f"PRAGMA index_info({index_name})"))
        indexed_columns = [info_row[2] for info_row in index_info.fetchall()]
        if indexed_columns == ["code"]:
            return True
    return False


async def _rebuild_sqlite_movies_table(conn) -> None:
    result = await conn.execute(text("PRAGMA table_info(movies)"))
    columns = {row[1] for row in result.fetchall()}

    def column_expr(name: str, fallback: str) -> str:
        return name if name in columns else fallback

    archive_chat_expr = column_expr("archive_chat_id", column_expr("channel_id", "NULL"))
    archive_message_expr = column_expr("archive_message_id", column_expr("message_id", "NULL"))
    views_expr = column_expr("views_count", column_expr("views", "0"))
    created_at_expr = column_expr("created_at", "CURRENT_TIMESTAMP")
    content_type_expr = column_expr("content_type", "'movie'")
    episode_expr = column_expr("episode", "NULL")

    await conn.execute(text("DROP TABLE IF EXISTS movies_new"))
    await conn.execute(
        text(
            """
            CREATE TABLE movies_new (
                id INTEGER NOT NULL,
                code VARCHAR(64) NOT NULL,
                title VARCHAR(255) NOT NULL,
                content_type VARCHAR(16) DEFAULT 'movie' NOT NULL,
                episode INTEGER,
                description TEXT,
                movie_link TEXT,
                archive_chat_id VARCHAR(255),
                archive_message_id INTEGER,
                views_count INTEGER DEFAULT 0 NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                PRIMARY KEY (id)
            )
            """
        )
    )
    await conn.execute(
        text(
            f"""
            INSERT INTO movies_new (
                id,
                code,
                title,
                content_type,
                episode,
                description,
                movie_link,
                archive_chat_id,
                archive_message_id,
                views_count,
                created_at
            )
            SELECT
                id,
                code,
                title,
                COALESCE({content_type_expr}, 'movie'),
                {episode_expr},
                {column_expr("description", "NULL")},
                {column_expr("movie_link", "NULL")},
                {archive_chat_expr},
                {archive_message_expr},
                COALESCE({views_expr}, 0),
                COALESCE({created_at_expr}, CURRENT_TIMESTAMP)
            FROM movies
            """
        )
    )
    await conn.execute(text("DROP TABLE movies"))
    await conn.execute(text("ALTER TABLE movies_new RENAME TO movies"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_movies_code ON movies (code)"))
    logger.info("movies jadvali arxiv kanal sxemasiga o'tkazildi.")


async def _migrate_sqlite_movies_serial_support() -> None:
    """SQLite: add content_type & episode columns; replace unique code index with plain index."""
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(movies)"))
        rows = result.fetchall()
        if not rows:
            return

        columns = {row[1] for row in rows}

        # Add new columns if absent
        if "content_type" not in columns:
            await conn.execute(
                text("ALTER TABLE movies ADD COLUMN content_type VARCHAR(16) NOT NULL DEFAULT 'movie'")
            )
            logger.info("SQLite movies: added content_type column")

        if "episode" not in columns:
            await conn.execute(
                text("ALTER TABLE movies ADD COLUMN episode INTEGER")
            )
            logger.info("SQLite movies: added episode column")

        # Drop any existing unique index on code; recreate as plain index
        # SQLAlchemy may name it ix_movies_code or uq_movies_code depending on version
        for idx_name in ("ix_movies_code", "uq_movies_code"):
            await conn.execute(text(f"DROP INDEX IF EXISTS {idx_name}"))

        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_movies_code ON movies (code)")
        )
        logger.info("SQLite movies: code index is now non-unique (serial support)")


async def _migrate_postgresql_movies_serial_support() -> None:
    """PostgreSQL: add content_type & episode columns; drop unique constraint on code."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'movies'
                """
            )
        )
        columns = {row[0] for row in result.fetchall()}
        if not columns:
            logger.warning("movies jadvali topilmadi, serial migratsiya o'tkazilmadi.")
            return

        if "content_type" not in columns:
            await conn.execute(
                text(
                    "ALTER TABLE movies "
                    "ADD COLUMN content_type VARCHAR(16) NOT NULL DEFAULT 'movie'"
                )
            )
            logger.info("PostgreSQL movies: added content_type column")

        if "episode" not in columns:
            await conn.execute(
                text("ALTER TABLE movies ADD COLUMN episode INTEGER")
            )
            logger.info("PostgreSQL movies: added episode column")

        await conn.execute(
            text(
                """
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
                        EXECUTE format(
                            'ALTER TABLE movies DROP CONSTRAINT %I',
                            unique_constraint_name
                        );
                    END LOOP;
                END $$
                """
            )
        )
        for idx in ("ix_movies_code", "uq_movies_code"):
            await conn.execute(text(f"DROP INDEX IF EXISTS {idx}"))

        # Recreate as plain index
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_movies_code ON movies (code)")
        )
        logger.info("PostgreSQL movies: code index is now non-unique (serial support)")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
