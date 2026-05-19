import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)

app = FastAPI()

bot: Bot | None = None
dp: Dispatcher | None = None


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
    from app.handlers import admin, user

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(admin.router)
    dispatcher.include_router(user.router)
    return dispatcher


async def setup_bot() -> tuple[Bot, Dispatcher]:
    global bot, dp

    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN .env faylida ko'rsatilmagan.")

    from app.database import init_db

    await init_db()
    logger.info("Database initialized.")

    if bot is None:
        bot = create_bot()
    if dp is None:
        dp = create_dispatcher()

    return bot, dp


async def setup_webhook(current_bot: Bot, dispatcher: Dispatcher) -> None:
    await current_bot.set_webhook(
        url=settings.webhook_url,
        allowed_updates=dispatcher.resolve_used_update_types(),
        secret_token=settings.webhook_secret or None,
        drop_pending_updates=False,
    )
    logger.info("Webhook set: %s", settings.webhook_url)


@app.on_event("startup")
async def on_startup() -> None:
    setup_logging()

    current_bot, dispatcher = await setup_bot()
    if settings.normalized_bot_mode == "webhook":
        await setup_webhook(current_bot, dispatcher)
    else:
        logger.info("BOT_MODE=polling. FastAPI webhook startup skipped.")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if bot is not None:
        await bot.session.close()
        logger.info("Bot session closed.")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mode": settings.normalized_bot_mode}


@app.post(settings.normalized_webhook_path)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
) -> dict[str, bool]:
    if settings.webhook_secret and x_telegram_bot_api_secret_token != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    if bot is None or dp is None:
        raise HTTPException(status_code=503, detail="Bot is not initialized")

    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}


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
        await dispatcher.start_polling(current_bot)
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
    if settings.normalized_bot_mode == "webhook":
        import uvicorn

        setup_logging()
        uvicorn.run(app, host="0.0.0.0", port=settings.port)
        return

    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_polling())


if __name__ == "__main__":
    main()
