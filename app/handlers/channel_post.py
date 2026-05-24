"""
channel_post.py
---------------
Handles new posts arriving in the archive channel.

Caption/text is parsed for patterns such as:
  "Kod: BB01 | 2-qism"
  "BB01 | qism 2"
  "#BB01 2-qism"

If an episode number is found  → saved as serial episode
If no episode number found     → saved as movie
"""

import logging
import re

from aiogram import Bot, F, Router
from aiogram.types import Message

from app.config import settings
from app.database import AsyncSessionLocal
from app.services.movie_service import (
    create_movie,
    create_serial_episode,
    get_all_by_code,
    normalize_code,
    parse_archive_post_link,
)

logger = logging.getLogger(__name__)
router = Router(name="channel_post")

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches:  "Kod: BB01"  /  "#BB01"  /  "BB01" at word boundary
_CODE_PATTERN = re.compile(
    r"(?:Kod\s*[:：]\s*|#)([A-Za-z0-9]{2,16})"
    r"|^([A-Za-z]{1,4}\d{1,8})\b",
    re.IGNORECASE | re.MULTILINE,
)

# Matches episode number: "2-qism", "qism 2", "qism2", "2 qism"
_EPISODE_PATTERN = re.compile(
    r"(\d+)\s*[-–]?\s*qism|qism\s*[-–]?\s*(\d+)",
    re.IGNORECASE,
)


def _extract_code(text: str) -> str | None:
    """Return the first content code found in the text, uppercased."""
    m = _CODE_PATTERN.search(text)
    if not m:
        return None
    raw = m.group(1) or m.group(2) or ""
    return normalize_code(raw) if raw else None


def _extract_episode(text: str) -> int | None:
    """Return the episode number found in text, or None."""
    m = _EPISODE_PATTERN.search(text)
    if not m:
        return None
    raw = m.group(1) or m.group(2)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _extract_title(text: str, code: str) -> str:
    """
    Try to extract a title from the caption.
    Falls back to the code itself if nothing useful is found.
    """
    # Remove known patterns to leave the raw title
    clean = re.sub(r"Kod\s*[:：]\s*\S+", "", text, flags=re.IGNORECASE)
    clean = re.sub(r"#\S+", "", clean)
    clean = _EPISODE_PATTERN.sub("", clean)
    clean = re.sub(r"\|", " ", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip(" \n\r\t|–-")
    return clean if clean else code


def _build_archive_link(chat_id: int | str, message_id: int) -> str:
    """
    Build a t.me link the service can parse.
    Numeric IDs look like https://t.me/c/1234567890/42 (private channels).
    Username IDs look like https://t.me/username/42.
    """
    cid = str(chat_id)
    # Strip leading -100 prefix for private channel numeric IDs
    if cid.startswith("-100"):
        cid = cid[4:]
        return f"https://t.me/c/{cid}/{message_id}"
    # Public channel: strip leading @
    username = cid.lstrip("@")
    return f"https://t.me/{username}/{message_id}"


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

@router.channel_post(F.text | F.caption)
async def handle_archive_channel_post(message: Message) -> None:
    """
    Triggered when a new post appears in ANY channel the bot is in.
    Only processes posts from channels listed in ARCHIVE_CHANNEL_IDS config,
    or skips silently if the config is not set.
    """
    # Resolve channel id from the message
    chat_id = message.chat.id
    chat_username = (message.chat.username or "").lower()

    # Only process posts from configured archive channels (if setting exists)
    archive_ids: list[str] = _get_archive_channel_ids()
    if archive_ids:
        str_id = str(chat_id)
        at_username = f"@{chat_username}"
        match = (
            str_id in archive_ids
            or at_username in archive_ids
            or chat_username in archive_ids
        )
        if not match:
            return  # Not our archive channel, ignore

    raw_text = (message.text or message.caption or "").strip()
    if not raw_text:
        return

    code = _extract_code(raw_text)
    if not code:
        logger.debug(
            "channel_post: no code found in message_id=%s chat_id=%s",
            message.message_id,
            chat_id,
        )
        return

    episode = _extract_episode(raw_text)
    title = _extract_title(raw_text, code)
    archive_link = _build_archive_link(chat_id, message.message_id)

    try:
        if episode is not None:
            await _save_serial_episode(code, title, episode, archive_link)
        else:
            await _save_movie(code, title, archive_link)
    except Exception:
        logger.exception(
            "channel_post: failed to save content code=%s episode=%s message_id=%s",
            code,
            episode,
            message.message_id,
        )


async def _save_serial_episode(
    code: str,
    title: str,
    episode: int,
    archive_link: str,
) -> None:
    """Persist one episode of a serial; skip if episode already exists."""
    async with AsyncSessionLocal() as session:
        existing = await get_all_by_code(session, code)

        # Check if this specific episode already saved
        if any(row.episode == episode for row in existing):
            logger.info(
                "channel_post: serial episode already exists code=%s episode=%s",
                code,
                episode,
            )
            return

        # Use title from existing rows to stay consistent
        resolved_title = existing[0].title if existing else title

        await create_serial_episode(
            session=session,
            title=resolved_title,
            code=code,
            episode=episode,
            archive_post_link=archive_link,
        )
        logger.info(
            "channel_post: saved serial episode code=%s episode=%s title=%s",
            code,
            episode,
            resolved_title,
        )


async def _save_movie(code: str, title: str, archive_link: str) -> None:
    """Persist a movie; skip if code already exists."""
    async with AsyncSessionLocal() as session:
        existing = await get_all_by_code(session, code)
        if existing:
            logger.info(
                "channel_post: movie already exists code=%s, skipping",
                code,
            )
            return

        await create_movie(
            session=session,
            title=title,
            code=code,
            description=None,
            archive_post_link=archive_link,
        )
        logger.info(
            "channel_post: saved movie code=%s title=%s",
            code,
            title,
        )


def _get_archive_channel_ids() -> list[str]:
    """
    Return a list of archive channel identifiers from settings.
    Supports a comma-separated ARCHIVE_CHANNEL_IDS env variable.
    Falls back to an empty list (handle all channels) if not configured.
    """
    raw = getattr(settings, "archive_channel_ids", "") or ""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]
