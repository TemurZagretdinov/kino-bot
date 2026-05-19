from datetime import datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import Channel
from app.models.movie import Movie
from app.models.search import Search
from app.models.user import User


async def get_stats(session: AsyncSession) -> dict:
    today_start = datetime.combine(
        datetime.now(timezone.utc).date(),
        time.min,
        tzinfo=timezone.utc,
    )

    total_users = await session.scalar(select(func.count(User.id)))
    today_users = await session.scalar(
        select(func.count(User.id)).where(User.joined_at >= today_start)
    )
    total_movies = await session.scalar(select(func.count(Movie.id)))
    total_searches = await session.scalar(select(func.count(Search.id)))
    active_channels = await session.scalar(
        select(func.count(Channel.id)).where(Channel.is_active.is_(True))
    )

    top_movies_result = await session.scalars(
        select(Movie).order_by(Movie.views_count.desc()).limit(10)
    )

    return {
        "total_users": total_users or 0,
        "today_users": today_users or 0,
        "total_movies": total_movies or 0,
        "total_searches": total_searches or 0,
        "active_channels": active_channels or 0,
        "top_movies": list(top_movies_result),
    }
