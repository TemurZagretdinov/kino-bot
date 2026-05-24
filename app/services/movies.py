import logging
import re
from dataclasses import dataclass
from html import escape
from time import monotonic
from typing import Literal, TypedDict
from urllib.parse import urlparse

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.movie import Movie
from app.models.search import Search
from app.models.user import User

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60.0
_FUZZY_THRESHOLD = 55
_top_movies_cache: dict[int, tuple[float, list[Movie]]] = {}

ARCHIVE_LINK_ERROR = "Arxiv kanal post linki noto'g'ri. Masalan: https://t.me/channel/25"


class ContentResult(TypedDict):
    type: Literal["movie", "serial", "not_found"]
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


def parse_telegram_link(link: str) -> tuple[str, int] | None:
    """
    Parse public Telegram post links like https://t.me/channel_name/123.

    Returns ("@channel_name", 123) when valid, otherwise None.
    """
    raw_link = link.strip()
    match = re.fullmatch(
        r"https://t\.me/([A-Za-z0-9_]{4,32})/([1-9]\d*)/?",
        raw_link,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    channel_name, message_id = match.groups()
    return f"@{channel_name}", int(message_id)


def parse_archive_post_link(link: str) -> ArchivePostLink:
    """
    Parse archive links used by the existing movie flow.

    Public links are normalized to @username. Private /c/ links are normalized
    to the Telegram -100... chat id form so copy_message can use them.
    """
    raw_link = link.strip()
    if not raw_link:
        raise ValueError(ARCHIVE_LINK_ERROR)

    public_link = parse_telegram_link(raw_link)
    if public_link is not None:
        archive_chat_id, archive_message_id = public_link
        return ArchivePostLink(
            archive_chat_id=archive_chat_id,
            archive_message_id=archive_message_id,
            clean_link=raw_link.rstrip("/"),
        )

    url_value = raw_link
    if raw_link.startswith("t.me/"):
        url_value = f"https://{raw_link}"

    parsed = urlparse(url_value)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"t.me", "www.t.me"}:
        raise ValueError(ARCHIVE_LINK_ERROR)

    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(path_parts) < 2:
        raise ValueError(ARCHIVE_LINK_ERROR)

    if path_parts[0] == "c":
        if len(path_parts) < 3 or not path_parts[1].isdigit() or not path_parts[2].isdigit():
            raise ValueError(ARCHIVE_LINK_ERROR)
        internal_chat_id = path_parts[1]
        message_id = int(path_parts[2])
        if message_id <= 0:
            raise ValueError(ARCHIVE_LINK_ERROR)
        archive_chat_id = f"-100{internal_chat_id}"
        return ArchivePostLink(
            archive_chat_id=archive_chat_id,
            archive_message_id=message_id,
            clean_link=f"https://t.me/c/{internal_chat_id}/{message_id}",
        )

    username = path_parts[0].strip().removeprefix("@")
    message_id_text = path_parts[1].strip()
    if not username or username.startswith("+") or username == "joinchat":
        raise ValueError(ARCHIVE_LINK_ERROR)
    if not message_id_text.isdigit():
        raise ValueError(ARCHIVE_LINK_ERROR)

    message_id = int(message_id_text)
    if message_id <= 0:
        raise ValueError(ARCHIVE_LINK_ERROR)

    archive_chat_id = f"@{username}"
    clean_link = f"https://t.me/{username}/{message_id}"
    return ArchivePostLink(
        archive_chat_id=archive_chat_id,
        archive_message_id=message_id,
        clean_link=clean_link,
    )


def _content_type_for(items: list[Movie]) -> Literal["movie", "serial"]:
    if any((item.content_type or "movie") == "serial" for item in items):
        return "serial"
    if any(item.episode is not None for item in items):
        return "serial"
    return "movie"


def _order_key(movie: Movie) -> tuple[int, int, int]:
    episode = movie.episode if movie.episode is not None else 0
    return (episode, movie.id or 0, movie.archive_message_id or 0)


async def get_movie_by_code(session: AsyncSession, code: str) -> Movie | None:
    try:
        stmt = select(Movie).where(Movie.code == normalize_code(code)).limit(1)
        return await session.scalar(stmt)
    except SQLAlchemyError:
        logger.exception("Failed to get movie by code=%s", code)
        return None


async def get_movie_by_id(session: AsyncSession, movie_id: int) -> Movie | None:
    try:
        stmt = select(Movie).where(Movie.id == movie_id)
        return await session.scalar(stmt)
    except SQLAlchemyError:
        logger.exception("Failed to get movie by id=%s", movie_id)
        return None


async def get_all_by_code(session: AsyncSession, code: str) -> list[Movie]:
    try:
        stmt = (
            select(Movie)
            .where(Movie.code == normalize_code(code))
            .order_by(Movie.episode.asc().nullsfirst(), Movie.id.asc())
        )
        result = await session.scalars(stmt)
        return list(result)
    except SQLAlchemyError:
        logger.exception("Failed to get content by code=%s", code)
        return []


async def find_content(session: AsyncSession, query: str) -> ContentResult:
    """
    Search by exact code first, then by fuzzy title with a 55% threshold.
    """
    clean_query = query.strip()
    if not clean_query:
        return {"type": "not_found", "title": "", "items": []}

    rows_by_code = await get_all_by_code(session, clean_query)
    if rows_by_code:
        rows_by_code.sort(key=_order_key)
        return {
            "type": _content_type_for(rows_by_code),
            "title": rows_by_code[0].title,
            "items": rows_by_code,
        }

    try:
        result = await session.scalars(select(Movie))
        all_rows = list(result)
    except SQLAlchemyError:
        logger.exception("Failed to load movies for fuzzy search")
        return {"type": "not_found", "title": "", "items": []}

    if not all_rows:
        return {"type": "not_found", "title": "", "items": []}

    representatives: dict[str, Movie] = {}
    choices: list[str] = []
    for movie in all_rows:
        key = movie.code or f"id:{movie.id}"
        if key in representatives:
            continue
        representatives[key] = movie
        choices.append(movie.title)

    match = process.extractOne(
        clean_query,
        choices,
        scorer=fuzz.WRatio,
        score_cutoff=_FUZZY_THRESHOLD,
    )
    if not match:
        return {"type": "not_found", "title": "", "items": []}

    matched_title = match[0]
    representative = next(
        (movie for movie in representatives.values() if movie.title == matched_title),
        None,
    )
    if representative is None:
        return {"type": "not_found", "title": "", "items": []}

    items = await get_all_by_code(session, representative.code)
    if not items:
        items = [representative]

    items.sort(key=_order_key)
    return {
        "type": _content_type_for(items),
        "title": representative.title,
        "items": items,
    }


async def search_movies_by_title(
    session: AsyncSession,
    query: str,
    limit: int = 10,
) -> list[Movie]:
    clean_query = query.strip()
    if not clean_query:
        return []

    try:
        stmt = (
            select(Movie)
            .where(Movie.title.ilike(f"%{clean_query}%"))
            .order_by(Movie.views_count.desc(), Movie.created_at.desc(), Movie.id.desc())
        )
        result = await session.scalars(stmt)
        rows = list(result)
    except SQLAlchemyError:
        logger.exception("Failed to search movies by title=%s", query)
        return []

    seen_codes: set[str] = set()
    unique_rows: list[Movie] = []
    for row in rows:
        if row.code in seen_codes:
            continue
        seen_codes.add(row.code)
        unique_rows.append(row)
        if len(unique_rows) >= limit:
            break
    return unique_rows


async def get_top_movies(session: AsyncSession, limit: int = 10) -> list[Movie]:
    now = monotonic()
    cached = _top_movies_cache.get(limit)
    if cached is not None:
        cached_at, movies = cached
        if now - cached_at < _CACHE_TTL_SECONDS:
            return list(movies)

    try:
        stmt = (
            select(Movie)
            .order_by(Movie.views_count.desc(), Movie.created_at.desc(), Movie.id.desc())
            .limit(limit)
        )
        result = await session.scalars(stmt)
        movies = list(result)
    except SQLAlchemyError:
        logger.exception("Failed to get top movies")
        return []

    _top_movies_cache[limit] = (monotonic(), movies)
    return list(movies)


async def increment_movie_views(session: AsyncSession, movie_id: int) -> Movie | None:
    movie = await get_movie_by_id(session, movie_id)
    if movie is None:
        return None

    try:
        movie.views_count += 1
        await session.commit()
        await session.refresh(movie)
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("Failed to increment movie views movie_id=%s", movie_id)
        return None
    return movie


async def save_content(
    session: AsyncSession,
    code: str,
    title: str,
    message_id: int,
    channel_id: str,
    content_type: Literal["movie", "serial"] = "movie",
    episode: int | None = None,
) -> Movie:
    clean_code = normalize_code(code)
    clean_title = title.strip()
    clean_channel_id = channel_id.strip()

    if content_type not in {"movie", "serial"}:
        raise ValueError("Kontent turi noto'g'ri.")
    if not clean_code:
        raise ValueError("Kod bo'sh bo'lmasligi kerak.")
    if not clean_title:
        raise ValueError("Nom bo'sh bo'lmasligi kerak.")
    if not clean_channel_id:
        raise ValueError("Kanal ID bo'sh bo'lmasligi kerak.")
    if message_id <= 0:
        raise ValueError("Message ID noto'g'ri.")
    if content_type == "serial" and (episode is None or episode < 1):
        raise ValueError("Serial qismi noto'g'ri.")
    if content_type == "serial":
        existing_rows = await get_all_by_code(session, clean_code)
        if any(row.content_type == "serial" and row.episode == episode for row in existing_rows):
            raise ValueError(f"{episode}-qism oldin qo'shilgan.")

    movie = Movie(
        code=clean_code,
        title=clean_title,
        content_type=content_type,
        episode=episode if content_type == "serial" else None,
        archive_chat_id=clean_channel_id,
        archive_message_id=message_id,
        views_count=0,
    )
    session.add(movie)

    try:
        await session.commit()
        await session.refresh(movie)
    except IntegrityError as exc:
        await session.rollback()
        logger.exception("Failed to save content code=%s", clean_code)
        raise ValueError("Kontentni saqlashda xatolik yuz berdi.") from exc
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.exception("Failed to save content code=%s", clean_code)
        raise ValueError("Kontentni saqlashda xatolik yuz berdi.") from exc

    clear_top_movies_cache()
    return movie


async def create_movie(
    session: AsyncSession,
    title: str,
    code: str,
    description: str | None,
    archive_post_link: str | None = None,
    movie_link: str | None = None,
) -> Movie:
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
        await session.refresh(movie)
    except IntegrityError as exc:
        await session.rollback()
        logger.exception("Failed to create movie code=%s", clean_code)
        raise ValueError("Bu kino kodi oldin qo'shilgan.") from exc
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.exception("Failed to create movie code=%s", clean_code)
        raise ValueError("Kinoni saqlashda xatolik yuz berdi.") from exc

    clear_top_movies_cache()
    return movie


async def create_serial_episode(
    session: AsyncSession,
    title: str,
    code: str,
    episode: int,
    archive_post_link: str,
) -> Movie:
    clean_code = normalize_code(code)
    clean_title = title.strip()

    if not clean_title:
        raise ValueError("Serial nomi bo'sh bo'lmasligi kerak.")
    if not clean_code:
        raise ValueError("Serial kodi bo'sh bo'lmasligi kerak.")
    if episode < 1:
        raise ValueError("Qism raqami 1 dan kichik bo'lmasligi kerak.")

    existing = await get_all_by_code(session, clean_code)
    if any((row.content_type == "serial" and row.episode == episode) for row in existing):
        raise ValueError(f"{episode}-qism oldin qo'shilgan.")

    archive_post = parse_archive_post_link(archive_post_link)
    return await save_content(
        session=session,
        code=clean_code,
        title=clean_title,
        message_id=archive_post.archive_message_id,
        channel_id=archive_post.archive_chat_id,
        content_type="serial",
        episode=episode,
    )


async def delete_movie_by_code(session: AsyncSession, code: str) -> Movie | None:
    rows = await get_all_by_code(session, code)
    if not rows:
        return None

    try:
        for row in rows:
            await session.delete(row)
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("Failed to delete content by code=%s", code)
        return None

    clear_top_movies_cache()
    return rows[0]


async def list_movies(
    session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> list[Movie]:
    try:
        stmt = (
            select(Movie)
            .order_by(Movie.created_at.desc(), Movie.id.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await session.scalars(stmt)
        return list(result)
    except SQLAlchemyError:
        logger.exception("Failed to list movies")
        return []


async def record_search(
    session: AsyncSession,
    user: User,
    code: str,
    movie: Movie | None,
) -> None:
    try:
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
        clear_top_movies_cache()
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("Failed to record search user_id=%s code=%s", user.id, code)


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
