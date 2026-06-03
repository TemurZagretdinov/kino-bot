import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import settings

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def create_bot() -> Bot:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN .env faylida ko'rsatilmagan.")

    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    from app.handlers import admin, channel_post, user

    dispatcher = Dispatcher(storage=MemoryStorage())
    # Admin router first so admin filters take priority over user filters
    dispatcher.include_router(admin.router)
    dispatcher.include_router(user.router)
    # Channel post router handles posts arriving in the archive channel
    dispatcher.include_router(channel_post.router)
    return dispatcher


async def setup_bot() -> tuple[Bot, Dispatcher]:
    from app.database import init_db

    await init_db()
    logger.info("Database initialized.")

    return create_bot(), create_dispatcher()


async def run_polling() -> None:
    setup_logging()

    try:
        current_bot, dispatcher = await setup_bot()
    except RuntimeError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from None

    logger.info("Bot polling rejimida ishga tushdi.")
    try:
        await current_bot.delete_webhook(drop_pending_updates=False)
        await dispatcher.start_polling(
            current_bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    except TelegramNetworkError as exc:
        logger.error(
            "Telegram API ga ulanib bo'lmadi. Internet, firewall/proxy/VPN yoki "
            "api.telegram.org:443 ulanishini tekshiring. Asl xato: %s",
            exc,
        )
        raise SystemExit(1) from None
    finally:
        await current_bot.session.close()


def main() -> None:
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_polling())


if __name__ == "__main__":
    main()
