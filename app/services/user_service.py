from datetime import datetime, timezone

from aiogram.types import User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def upsert_user(session: AsyncSession, tg_user: TelegramUser) -> User:
    stmt = select(User).where(User.telegram_id == tg_user.id)
    user = await session.scalar(stmt)
    now = datetime.now(timezone.utc)

    if user is None:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            full_name=tg_user.full_name,
            joined_at=now,
            last_active=now,
        )
        session.add(user)
    else:
        user.username = tg_user.username
        user.full_name = tg_user.full_name
        user.last_active = now
        user.is_blocked = False

    await session.commit()
    await session.refresh(user)
    return user


async def get_user_by_telegram_id(
    session: AsyncSession,
    telegram_id: int,
) -> User | None:
    stmt = select(User).where(User.telegram_id == telegram_id)
    return await session.scalar(stmt)


async def save_last_movie_code(
    session: AsyncSession,
    telegram_id: int,
    code: str | None,
) -> None:
    user = await get_user_by_telegram_id(session, telegram_id)
    if user is None:
        return

    user.last_movie_code = code
    user.last_active = datetime.now(timezone.utc)
    await session.commit()


async def save_last_movie_request(
    session: AsyncSession,
    telegram_id: int,
    *,
    movie_code: str | None = None,
    movie_id: int | None = None,
    search_type: str | None = None,
) -> None:
    user = await get_user_by_telegram_id(session, telegram_id)
    if user is None:
        return

    user.last_movie_code = movie_code
    user.last_movie_id = movie_id
    user.last_search_type = search_type
    user.last_active = datetime.now(timezone.utc)
    await session.commit()


async def set_user_blocked(
    session: AsyncSession,
    telegram_id: int,
    is_blocked: bool = True,
) -> None:
    user = await get_user_by_telegram_id(session, telegram_id)
    if user is None:
        return

    user.is_blocked = is_blocked
    await session.commit()


async def list_not_blocked_users(session: AsyncSession) -> list[User]:
    stmt = select(User).where(User.is_blocked.is_(False)).order_by(User.id)
    result = await session.scalars(stmt)
    return list(result)
