import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import settings


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN .env faylida ko'rsatilmagan.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        from app.database import init_db

        await init_db()
        logging.info("Database initialized.")
    except RuntimeError as exc:
        logging.error("%s", exc)
        raise SystemExit(1) from None

    from app.handlers import admin, user

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(admin.router)
    dp.include_router(user.router)

    logging.info("Bot ishga tushdi.")
    try:
        await dp.start_polling(bot)
    except TelegramNetworkError as exc:
        logging.error(
            "Telegram API ga ulanib bo'lmadi. Internet, firewall/proxy/VPN yoki "
            "api.telegram.org:443 ulanishini tekshiring. Asl xato: %s",
            exc,
        )
        raise SystemExit(1) from None
    finally:
        await bot.session.close()


if __name__ == "__main__":
    if sys.platform.startswith("win"):
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(main())
