import logging
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urlparse

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel import Channel

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60.0
_active_channels_cache: tuple[float, list[Channel]] | None = None


@dataclass(frozen=True)
class ParsedChannelLink:
    is_private: bool
    invite_link: str
    username: str | None = None


def _clean_title(title: str) -> str:
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Kanal nomi bo'sh bo'lmasligi kerak.")
    return clean_title


def normalize_public_username(username: str) -> str:
    value = username.strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if value.startswith(prefix):
            value = value.removeprefix(prefix)
            break
    value = value.strip().strip("/")

    if not value:
        raise ValueError("Public kanal username bo'sh bo'lmasligi kerak.")
    if value.startswith("+"):
        raise ValueError("Public kanal uchun @username yuboring, invite link emas.")
    if value.startswith("@"):
        return value
    return f"@{value}"


def build_public_invite_link(username: str) -> str:
    normalized_username = normalize_public_username(username)
    return f"https://t.me/{normalized_username.removeprefix('@')}"


def _clean_tme_url(value: str) -> tuple[str, str]:
    raw_value = value.strip()
    url_value = raw_value
    if raw_value.startswith("t.me/"):
        url_value = f"https://{raw_value}"

    parsed = urlparse(url_value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Kanal linki noto'g'ri. Masalan: https://t.me/channel")

    host = parsed.netloc.lower()
    if host not in {"t.me", "www.t.me"}:
        raise ValueError("Faqat t.me kanal linkini yuboring.")

    path = parsed.path.strip("/")
    if not path:
        raise ValueError("Kanal linkida username yoki invite kodi topilmadi.")

    clean_link = url_value.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return path, clean_link


def parse_channel_link(link: str) -> ParsedChannelLink:
    value = link.strip()
    if not value:
        raise ValueError("Kanal linki bo'sh bo'lmasligi kerak.")

    if value.startswith("@"):
        username = normalize_public_username(value)
        return ParsedChannelLink(
            is_private=False,
            username=username,
            invite_link=build_public_invite_link(username),
        )

    path, invite_link = _clean_tme_url(value)
    path_parts = path.split("/")
    first_part = path_parts[0]

    if first_part.startswith("+"):
        return ParsedChannelLink(is_private=True, invite_link=invite_link)

    if first_part == "joinchat" and len(path_parts) >= 2 and path_parts[1]:
        return ParsedChannelLink(is_private=True, invite_link=invite_link)

    if len(path_parts) > 1:
        raise ValueError("Kanal linkini asosiy ko'rinishda yuboring. Masalan: https://t.me/channel")

    username = normalize_public_username(first_part)
    return ParsedChannelLink(
        is_private=False,
        username=username,
        invite_link=invite_link,
    )


def _parse_private_chat_id(chat_id: str | int) -> int:
    value = str(chat_id).strip()
    if not value:
        raise ValueError("Private kanal chat_id bo'sh bo'lmasligi kerak.")
    if not value.lstrip("-").isdigit():
        raise ValueError("Private kanal chat_id raqam bo'lishi kerak. Masalan: -1001234567890")
    return int(value)


def _clean_invite_link(invite_link: str) -> str:
    clean_invite_link = invite_link.strip()
    if not clean_invite_link:
        raise ValueError("Invite link bo'sh bo'lmasligi kerak.")
    return clean_invite_link


def clear_active_channels_cache() -> None:
    global _active_channels_cache

    _active_channels_cache = None


async def create_public_channel(
    session: AsyncSession,
    title: str,
    username: str,
    invite_link: str | None = None,
) -> Channel:
    clean_username = normalize_public_username(username)
    channel = Channel(
        title=_clean_title(title),
        username=clean_username,
        chat_id=None,
        invite_link=_clean_invite_link(invite_link) if invite_link else build_public_invite_link(clean_username),
        is_private=False,
        is_active=True,
    )
    session.add(channel)
    await session.commit()
    await session.refresh(channel)
    clear_active_channels_cache()
    return channel


async def create_private_channel(
    session: AsyncSession,
    title: str,
    chat_id: str | int,
    invite_link: str,
) -> Channel:
    channel = Channel(
        title=_clean_title(title),
        username=None,
        chat_id=_parse_private_chat_id(chat_id),
        invite_link=_clean_invite_link(invite_link),
        is_private=True,
        is_active=True,
    )
    session.add(channel)
    await session.commit()
    await session.refresh(channel)
    clear_active_channels_cache()
    return channel


async def list_channels(session: AsyncSession) -> list[Channel]:
    result = await session.scalars(select(Channel).order_by(Channel.id.desc()))
    return list(result)


async def get_active_channels(session: AsyncSession) -> list[Channel]:
    result = await session.execute(
        select(Channel).where(Channel.is_active == True).order_by(Channel.id)
    )
    return list(result.scalars().all())


async def list_active_channels(session: AsyncSession) -> list[Channel]:
    global _active_channels_cache

    now = monotonic()
    if _active_channels_cache is not None:
        cached_at, channels = _active_channels_cache
        if now - cached_at < _CACHE_TTL_SECONDS:
            logger.debug("Active channels cache hit: count=%s", len(channels))
            return list(channels)

    channels = await get_active_channels(session)
    _active_channels_cache = (monotonic(), channels)
    return list(channels)


async def deactivate_channel(
    session: AsyncSession,
    identifier: str,
) -> Channel | None:
    value = identifier.strip()
    if not value:
        return None

    conditions = [Channel.invite_link == value]
    if value.lstrip("-").isdigit():
        conditions.append(Channel.chat_id == int(value))
    else:
        try:
            username = normalize_public_username(value)
        except ValueError:
            username = ""
        if username:
            conditions.append(Channel.username == username)

    stmt = select(Channel).where(or_(*conditions)).order_by(Channel.id.desc())
    channel = await session.scalar(stmt)
    if channel is None:
        return None

    channel.is_active = False
    await session.commit()
    await session.refresh(channel)
    clear_active_channels_cache()
    return channel
