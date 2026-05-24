import logging
from dataclasses import dataclass
from html import escape
from time import monotonic
from typing import TypedDict
from urllib.parse import urlparse

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.movie import Movie
from app.models.search import Search
from app.models.user import User

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60.0
_top_movies_cache: dict[int, tuple[float, list[Movie]]] = {}

ARCHIVE_LINK_ERROR = "Arxiv kanal post linki noto'g'ri. Masalan: https://t.me/channel/25"

# Minimum fuzzy match score (0-100) for title search
_FUZZY_THRESHOLD = 55


class ContentResult(TypedDict):
    """Return type for find_content()."""

    type: str   # "movie" | "serial" | "not_found"
    title: str
    items: list[Movie]


@dataclass(frozen=True)
class ArchivePostLink:
    archive_chat_id: str
    archive_message_id: int
    clean_link: str


def normalize_code(code: str) -> str:
    return code.strip().upper()


def clear_top_movies_cache() -> None:
    _top_movies_cache.clear()


def parse_archive_post_link(link: str) -> ArchivePostLink:
    raw_link = link.strip()
    if not raw_link:
        raise ValueError(ARCHIVE_LINK_ERROR)

    url_value = raw_link
    if raw_link.startswith("t.me/"):
        url_value = f"https://{raw_link}"

    parsed = urlparse(url_value)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"t.me", "www.t.me"}:
        raise ValueError(ARCHIVE_LINK_ERROR)

    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(path_parts) < 2:
        raise ValueError(ARCHIVE_LINK_ERROR)

    username = path_parts[0].strip()
    message_id_text = path_parts[1].strip()
    if username.startswith("+") or username == "joinchat" or username == "c":
        raise ValueError(ARCHIVE_LINK_ERROR)
    if not message_id_text.isdigit():
        raise ValueError(ARCHIVE_LINK_ERROR)

    message_id = int(message_id_text)
    if message_id <= 0:
        raise ValueError(ARCHIVE_LINK_ERROR)

    clean_username = username.removeprefix("@")
    if not clean_username:
        raise ValueError(ARCHIVE_LINK_ERROR)

    archive_chat_id = f"@{clean_username}"
    clean_link = f"https://t.me/{clean_username}/{message_id}"
    return ArchivePostLink(
        archive_chat_id=archive_chat_id,
        archive_message_id=message_id,
        clean_link=clean_link,
    )


# ---------------------------------------------------------------------------
# Core lookup
# ---------------------------------------------------------------------------

async def get_movie_by_code(session: AsyncSession, code: str) -> Movie | None:
    """Return the FIRST movie row matching code (for backwards compat with movie flow)."""
    stmt = select(Movie).where(Movie.code == normalize_code(code)).limit(1)
    return await session.scalar(stmt)


async def get_movie_by_id(session: AsyncSession, movie_id: int) -> Movie | None:
    stmt = select(Movie).where(Movie.id == movie_id)
    return await session.scalar(stmt)


async def get_all_by_code(session: AsyncSession, code: str) -> list[Movie]:
    """Return all rows for a code, ordered by episode (for serials)."""
    stmt = (
        select(Movie)
        .where(Movie.code == normalize_code(code))
        .order_by(Movie.episode.asc().nullsfirst(), Movie.id.asc())
    )
    result = await session.scalars(stmt)
    return list(result)


async def find_content(session: AsyncSession, query: str) -> ContentResult:
    """
    Universal content lookup used by the search handler.

    Search order:
      1. Exact code match  → returns all episodes ordered by episode number
      2. Fuzzy title match → rapidfuzz WRatio >= FUZZY_THRESHOLD

    Returns a ContentResult dict with keys: type, title, items.
    """
    clean_query = query.strip()
    if not clean_query:
        return ContentResult(type="not_found", title="", items=[])

    # --- 1. Exact code match ---
    code = normalize_code(clean_query)
    rows_by_code = await get_all_by_code(session, code)
    if rows_by_code:
        first = rows_by_code[0]
        content_type = first.content_type or "movie"
        return ContentResult(type=content_type, title=first.title, items=rows_by_code)

    # --- 2. Fuzzy title search ---
    # Fetch all distinct (title, content_type, code) sets to avoid loading huge blobs
    all_movies: list[Movie] = list(await session.scalars(select(Movie)))
    if not all_movies:
        return ContentResult(type="not_found", title="", items=[])

    titles = [m.title for m in all_movies]
    match = process.extractOne(
        clean_query,
        titles,
        scorer=fuzz.WRatio,
        score_cutoff=_FUZZY_THRESHOLD,
    )
    if not match:
        return ContentResult(type="not_found", title="", items=[])

    matched_title: str = match[0]

    # Fetch all rows with the matched title, ordered by episode
    stmt = (
        select(Movie)
        .where(Movie.title == matched_title)
        .order_by(Movie.episode.asc().nullsfirst(), Movie.id.asc())
    )
    items = list(await session.scalars(stmt))
    if not items:
        return ContentResult(type="not_found", title="", items=[])

    content_type = items[0].content_type or "movie"
    return ContentResult(type=content_type, title=matched_title, items=items)


# ---------------------------------------------------------------------------
# Title search (used by name-search UI list)
# ---------------------------------------------------------------------------

async def search_movies_by_title(
    session: AsyncSession,
    query: str,
    limit: int = 10,
) -> list[Movie]:
    """
    Returns up to `limit` distinct content items (one representative row per
    code) matching the query via ILIKE.  Used to build the inline list UI.
    """
    clean_query = query.strip()
    if not clean_query:
        return []

    stmt = (
        select(Movie)
        .where(Movie.title.ilike(f"%{clean_query}%"))
        .order_by(Movie.views_count.desc(), Movie.created_at.desc(), Movie.id.desc())
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return list(result)


# ---------------------------------------------------------------------------
# Top movies
# ---------------------------------------------------------------------------

async def get_top_movies(session: AsyncSession, limit: int = 10) -> list[Movie]:
    now = monotonic()
    cached = _top_movies_cache.get(limit)
    if cached is not None:
        cached_at, movies = cached
        if now - cached_at < _CACHE_TTL_SECONDS:
            return list(movies)

    stmt = (
        select(Movie)
        .order_by(Movie.views_count.desc(), Movie.created_at.desc(), Movie.id.desc())
        .limit(limit)
    )
    result = await session.scalars(stmt)
    movies = list(result)
    _top_movies_cache[limit] = (monotonic(), movies)
    return list(movies)


async def increment_movie_views(session: AsyncSession, movie_id: int) -> Movie | None:
    movie = await get_movie_by_id(session, movie_id)
    if movie is None:
        return None

    movie.views_count += 1
    await session.commit()
    await session.refresh(movie)
    return movie


# ---------------------------------------------------------------------------
# Create / delete
# ---------------------------------------------------------------------------

async def create_movie(
    session: AsyncSession,
    title: str,
    code: str,
    description: str | None,
    archive_post_link: str | None = None,
    movie_link: str | None = None,
) -> Movie:
    """Create a single movie record (content_type='movie')."""
    clean_code = normalize_code(code)
    clean_title = title.strip()
    clean_movie_link = (movie_link or "").strip() or None

    if not clean_title:
        raise ValueError("Kino nomi bo'sh bo'lmasligi kerak.")
    if not clean_code:
        raise ValueError("Kino kodi bo'sh bo'lmasligi kerak.")
    if await get_movie_by_code(session, clean_code):
        raise ValueError("Bu kino kodi oldin qo'shilgan.")

    archive_post = parse_archive_post_link(archive_post_link or "")

    movie = Movie(
        title=clean_title,
        code=clean_code,
        content_type="movie",
        episode=None,
        description=(description or "").strip() or None,
        movie_link=clean_movie_link,
        archive_chat_id=archive_post.archive_chat_id,
        archive_message_id=archive_post.archive_message_id,
    )
    session.add(movie)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError("Bu kino kodi oldin qo'shilgan.") from exc

    await session.refresh(movie)
    clear_top_movies_cache()
    return movie


async def create_serial_episode(
    session: AsyncSession,
    title: str,
    code: str,
    episode: int,
    archive_post_link: str,
) -> Movie:
    """Create one episode row of a serial (content_type='serial')."""
    clean_code = normalize_code(code)
    clean_title = title.strip()

    if not clean_title:
        raise ValueError("Serial nomi bo'sh bo'lmasligi kerak.")
    if not clean_code:
        raise ValueError("Serial kodi bo'sh bo'lmasligi kerak.")
    if episode < 1:
        raise ValueError("Qism raqami 1 dan kichik bo'lmasligi kerak.")

    archive_post = parse_archive_post_link(archive_post_link)

    row = Movie(
        title=clean_title,
        code=clean_code,
        content_type="serial",
        episode=episode,
        description=None,
        movie_link=None,
        archive_chat_id=archive_post.archive_chat_id,
        archive_message_id=archive_post.archive_message_id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    clear_top_movies_cache()
    return row


async def delete_movie_by_code(session: AsyncSession, code: str) -> Movie | None:
    """Delete ALL rows with the given code (entire movie or all episodes of a serial)."""
    rows = await get_all_by_code(session, code)
    if not rows:
        return None

    for row in rows:
        await session.delete(row)
    await session.commit()
    clear_top_movies_cache()
    # Return first row as representative for the confirmation message
    return rows[0]


async def list_movies(
    session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> list[Movie]:
    stmt = (
        select(Movie)
        .order_by(Movie.created_at.desc(), Movie.id.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return list(result)


async def record_search(
    session: AsyncSession,
    user: User,
    code: str,
    movie: Movie | None,
) -> None:
    if movie is not None:
        movie.views_count += 1

    session.add(
        Search(
            user_id=user.id,
            movie_id=movie.id if movie else None,
            code=normalize_code(code),
        )
    )
    await session.commit()


def format_movie_message(movie: Movie) -> str:
    lines = [f"🎥 <b>{escape(movie.title)}</b>"]
    if movie.description:
        lines.extend(["", f"📝 {escape(movie.description)}"])

    lines.extend(
        [
            "",
            f"🔢 Kod: <code>{escape(movie.code)}</code>",
            f"👁 Ko'rishlar: <b>{movie.views_count}</b>",
        ]
    )
    if movie.movie_link:
        lines.extend(["", f"🔗 Ko'rish: {escape(movie.movie_link)}"])
    return "\n".join(lines)
