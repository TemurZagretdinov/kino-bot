import logging
from dataclasses import dataclass
from time import perf_counter

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.channel import Channel
from app.services.channel_service import get_active_channels, normalize_public_username

logger = logging.getLogger(__name__)

SUBSCRIBED_STATUSES = {"member", "administrator", "creator"}
ADMIN_CHECK_ERROR_TEXT = "Bot kanal admini emas yoki kanal ma’lumotlari noto‘g‘ri."
CHANNEL_DATA_ERROR_TEXT = "Kanal ma’lumotlari noto‘g‘ri"
MEMBER_LIST_INACCESSIBLE_USER_TEXT = (
    "Obuna tekshirishda muammo yuz berdi. "
    "Botga private kanal obunachilarini tekshirish huquqi berilmagan."
)
MEMBER_LIST_INACCESSIBLE_ADMIN_TEXT = (
    "Private kanal member list inaccessible. Bot admin permissionlarini tekshiring: "
    "Add subscribers/Invite users, Post messages, Delete messages."
)


@dataclass(frozen=True)
class SubscriptionCheckResult:
    is_subscribed: bool
    missing_channels: list[Channel]
    has_check_error: bool
    user_error_text: str | None = None

    def __iter__(self):
        yield self.is_subscribed
        yield self.missing_channels
        yield self.has_check_error


def _get_check_target(channel: Channel) -> str | int:
    if channel.is_private:
        if channel.chat_id is None:
            raise ValueError("private channel chat_id is empty")
        return int(channel.chat_id)
    if channel.username:
        return normalize_public_username(channel.username)
    raise ValueError("public channel username is empty")


def _log_subscription_error(
    channel: Channel,
    exc: BaseException,
    *,
    user_id: int,
    check_target: str | int | None = None,
    unexpected: bool = False,
) -> None:
    log_method = logger.exception if unexpected else logger.warning
    log_method(
        "Subscription check error: channel_id=%s title=%s is_private=%s "
        "chat_id=%s username=%s target=%s user_id=%s exception_type=%s exception_message=%s",
        channel.id,
        channel.title,
        channel.is_private,
        channel.chat_id,
        channel.username,
        check_target,
        user_id,
        exc.__class__.__name__,
        str(exc),
    )


def _is_member_list_inaccessible(exc: BaseException) -> bool:
    return "member list is inaccessible" in str(exc).lower()


def _log_member_list_inaccessible(
    channel: Channel,
    exc: BaseException,
    *,
    user_id: int,
    check_target: str | int,
) -> None:
    logger.warning(
        "%s channel_id=%s title=%s is_private=%s "
        "chat_id=%s username=%s target=%s user_id=%s exception_type=%s exception_message=%s",
        MEMBER_LIST_INACCESSIBLE_ADMIN_TEXT,
        channel.id,
        channel.title,
        channel.is_private,
        channel.chat_id,
        channel.username,
        check_target,
        user_id,
        exc.__class__.__name__,
        str(exc),
    )


async def check_user_subscriptions(bot: Bot, user_id: int) -> SubscriptionCheckResult:
    """
    Barcha active kanallarni tekshiradi.
    Natija:
    is_subscribed=True agar hammasiga obuna bo'lgan bo'lsa.
    missing_channels to'ldiriladi agar obuna bo'lmagan kanallar bo'lsa.
    has_check_error=True agar kanal sozlamasi yoki Telegram API tekshiruvida xatolik bo'lsa.
    """
    started_at = perf_counter()
    channel_count = 0
    missing_channels: list[Channel] = []
    has_check_error = False
    user_error_text: str | None = None

    try:
        async with AsyncSessionLocal() as session:
            channels = await get_active_channels(session)

        channel_count = len(channels)
        error_details: list[str] = []

        for channel in channels:
            try:
                check_target = _get_check_target(channel)
            except (TypeError, ValueError) as exc:
                _log_subscription_error(channel, exc, user_id=user_id)
                error_details.append(f"{channel.title} ({channel.id})")
                has_check_error = True
                user_error_text = user_error_text or CHANNEL_DATA_ERROR_TEXT
                continue

            try:
                member = await bot.get_chat_member(
                    chat_id=check_target,
                    user_id=user_id,
                )
                status = getattr(member.status, "value", member.status)
                if status not in SUBSCRIBED_STATUSES:
                    missing_channels.append(channel)
            except (TelegramBadRequest, TelegramForbiddenError) as exc:
                if _is_member_list_inaccessible(exc):
                    _log_member_list_inaccessible(
                        channel,
                        exc,
                        user_id=user_id,
                        check_target=check_target,
                    )
                    error_details.append(
                        f"{MEMBER_LIST_INACCESSIBLE_ADMIN_TEXT} "
                        f"channel_id={channel.id} title={channel.title} target={check_target}"
                    )
                    user_error_text = MEMBER_LIST_INACCESSIBLE_USER_TEXT
                else:
                    _log_subscription_error(
                        channel,
                        exc,
                        user_id=user_id,
                        check_target=check_target,
                    )
                    error_details.append(f"{channel.title} ({check_target})")
                has_check_error = True
            except Exception as exc:
                _log_subscription_error(
                    channel,
                    exc,
                    user_id=user_id,
                    check_target=check_target,
                    unexpected=True,
                )
                error_details.append(f"{channel.title} ({check_target})")
                has_check_error = True

        if has_check_error:
            await _notify_admins_about_check_error(bot, error_details)
            return SubscriptionCheckResult(
                is_subscribed=False,
                missing_channels=[],
                has_check_error=True,
                user_error_text=user_error_text,
            )

        return SubscriptionCheckResult(
            is_subscribed=len(missing_channels) == 0,
            missing_channels=missing_channels,
            has_check_error=False,
        )
    finally:
        logger.info(
            "Subscription check took %.2f ms user_id=%s channels=%s missing=%s has_error=%s",
            (perf_counter() - started_at) * 1000,
            user_id,
            channel_count,
            len(missing_channels),
            has_check_error,
        )


async def _notify_admins_about_check_error(bot: Bot, details: list[str]) -> None:
    if not settings.admin_ids:
        return

    detail_text = "\n".join(f"- {detail}" for detail in details[:5])
    text = ADMIN_CHECK_ERROR_TEXT
    if detail_text:
        text = f"{text}\n{detail_text}"

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text, parse_mode=None)
        except Exception:
            logger.warning("Admin xabarnomasi yuborilmadi: admin_id=%s", admin_id)
