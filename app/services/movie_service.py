from dataclasses import dataclass
from html import escape
from time import monotonic
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.movie import Movie
from app.models.search import Search
from app.models.user import User

_CACHE_TTL_SECONDS = 60.0
_top_movies_cache: dict[int, tuple[float, list[Movie]]] = {}

ARCHIVE_LINK_ERROR = "Arxiv kanal post linki noto‘g‘ri. Masalan: https://t.me/channel/25"


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


async def get_movie_by_code(session: AsyncSession, code: str) -> Movie | None:
    stmt = select(Movie).where(Movie.code == normalize_code(code))
    return await session.scalar(stmt)


async def get_movie_by_id(session: AsyncSession, movie_id: int) -> Movie | None:
    stmt = select(Movie).where(Movie.id == movie_id)
    return await session.scalar(stmt)


async def search_movies_by_title(
    session: AsyncSession,
    query: str,
    limit: int = 10,
) -> list[Movie]:
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


async def delete_movie_by_code(session: AsyncSession, code: str) -> Movie | None:
    movie = await get_movie_by_code(session, code)
    if movie is None:
        return None

    await session.delete(movie)
    await session.commit()
    clear_top_movies_cache()
    return movie


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
            f"👁 Ko‘rishlar: <b>{movie.views_count}</b>",
        ]
    )
    if movie.movie_link:
        lines.extend(["", f"🔗 Ko‘rish: {escape(movie.movie_link)}"])
    return "\n".join(lines)
