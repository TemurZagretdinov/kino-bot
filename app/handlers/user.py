import asyncio
import logging
from html import escape
from time import perf_counter

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User as TelegramUser

from app.database import AsyncSessionLocal
from app.keyboards.user_kb import (
    CHANNELS,
    HELP,
    SEARCH_BY_CODE,
    SEARCH_BY_NAME,
    TOP_MOVIES,
    back_to_menu_keyboard,
    main_user_menu,
    subscribe_keyboard,
    top_movies_inline_keyboard,
)
from app.services.channel_service import get_active_channels
from app.services.movie_service import (
    find_content,
    get_movie_by_id,
    get_top_movies,
    normalize_code,
    record_search,
)
from app.services.subscription import check_user_subscriptions
from app.services.user_service import (
    get_user_by_telegram_id,
    save_last_movie_request,
    upsert_user,
)
from app.states.user_states import UserSearch

logger = logging.getLogger(__name__)
router = Router(name="user")


WELCOME_TEXT = (
    "Assalomu alaykum! 🎬\n\n"
    "KinoTekaUz botiga xush kelibsiz.\n"
    "Bu yerda siz kinolarni kod orqali yoki nomi orqali topishingiz mumkin.\n\n"
    "Kerakli bo'limni tanlang 👇"
)

SUBSCRIBE_TEXT = (
    "📢 Kinolarni olish uchun quyidagi kanallarga obuna bo'ling.\n\n"
    "Obuna bo'lgach, ✅ Tekshirish tugmasini bosing."
)

SUBSCRIPTION_CHECK_ERROR_TEXT = (
    "Obuna tekshirishda muammo yuz berdi. Administratorga murojaat qiling."
)

MOVIE_SEND_ERROR_TEXT = "❌ Kinoni yuborishda muammo yuz berdi. Administratorga murojaat qiling."
MOVIE_LINK_FALLBACK_TEXT = "Agar video ochilmasa, quyidagi link orqali ko'ring:"

CODE_PROMPT_TEXT = "🎬 Kino yoki serial kodini yuboring.\nMasalan: 1024"
NAME_PROMPT_TEXT = "🔎 Kino yoki serial nomini yuboring.\nMasalan: Avatar, Venom, Interstellar"
MENU_HINT_TEXT = "Kerakli bo'limni tanlang yoki kino kodini yuboring 🎬"

NOT_FOUND_TEXT = (
    "❌ Kino yoki serial topilmadi!\n"
    "🔍 Kod yoki nom to'g'ri yozing"
)

HELP_TEXT = (
    "ℹ️ <b>Botdan foydalanish:</b>\n\n"
    "🎬 <b>Kod bilan izlash</b> — Instagram yoki kanaldagi kino kodini yuboring.\n"
    "🔎 <b>Nom bilan izlash</b> — kino nomi orqali qidiring.\n"
    "🔥 <b>Top filmlar</b> — eng ko'p ko'rilgan kinolar.\n"
    "📢 <b>Kanallar</b> — majburiy obuna kanallari.\n\n"
    "Kino chiqishi uchun kerakli kanallarga obuna bo'lish talab qilinadi."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _upsert_if_possible(tg_user: TelegramUser | None) -> None:
    if tg_user is None:
        return
    async with AsyncSessionLocal() as session:
        await upsert_user(session, tg_user)


async def _save_pending_movie_request(
    state: FSMContext,
    tg_user: TelegramUser,
    *,
    movie_code: str | None = None,
    movie_id: int | None = None,
    search_type: str | None = None,
) -> None:
    await state.update_data(
        last_movie_code=movie_code,
        last_movie_id=movie_id,
        last_search_type=search_type,
    )

    async with AsyncSessionLocal() as session:
        await upsert_user(session, tg_user)
        await save_last_movie_request(
            session,
            tg_user.id,
            movie_code=movie_code,
            movie_id=movie_id,
            search_type=search_type,
        )


async def _check_subscription_or_prompt(
    message: Message,
    bot: Bot,
    user_id: int,
) -> bool:
    subscription_result = await check_user_subscriptions(bot, user_id)

    if subscription_result.has_check_error:
        await message.answer(subscription_result.user_error_text or SUBSCRIPTION_CHECK_ERROR_TEXT)
        return False

    if not subscription_result.is_subscribed:
        await message.answer(
            SUBSCRIBE_TEXT,
            reply_markup=subscribe_keyboard(subscription_result.missing_channels),
        )
        return False

    return True


# ---------------------------------------------------------------------------
# Content delivery
# ---------------------------------------------------------------------------

async def _safe_copy_message(
    bot: Bot,
    chat_id: int,
    from_chat_id: str,
    message_id: int,
    **kwargs,
) -> bool:
    """Send copy_message and return True on success, False on Telegram error."""
    try:
        await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
            **kwargs,
        )
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.warning(
            "copy_message failed chat_id=%s from_chat_id=%s message_id=%s error=%s",
            chat_id,
            from_chat_id,
            message_id,
            exc,
        )
        return False
    except Exception:
        logger.exception(
            "Unexpected error in copy_message chat_id=%s from_chat_id=%s message_id=%s",
            chat_id,
            from_chat_id,
            message_id,
        )
        return False


async def _deliver_content_result(
    message: Message,
    bot: Bot,
    user_id: int,
    result: dict,
) -> None:
    """
    Route a ContentResult to the correct delivery method:
      - movie   → single copy_message
      - serial  → header message + one copy_message per episode (0.3 s delay)
      - not_found → error message
    """
    content_type = result["type"]
    title = result["title"]
    items: list = result["items"]

    if content_type == "not_found" or not items:
        await message.answer(NOT_FOUND_TEXT, reply_markup=main_user_menu())
        return

    if content_type == "movie":
        movie = items[0]
        await _deliver_single_movie(message, bot, user_id, movie)
        return

    # --- Serial delivery ---
    count = len(items)
    await message.answer(f"📺 {escape(title)} — {count} ta qism topildi")

    for ep in items:
        if ep.archive_chat_id and ep.archive_message_id:
            ep_label = f"📎 {ep.episode}-qism" if ep.episode else "📎 Qism"
            ok = await _safe_copy_message(
                bot,
                chat_id=user_id,
                from_chat_id=ep.archive_chat_id,
                message_id=ep.archive_message_id,
                caption=ep_label,
            )
            if not ok:
                await message.answer(
                    f"❌ {ep_label} yuborishda xatolik yuz berdi.",
                    reply_markup=back_to_menu_keyboard(),
                )
        await asyncio.sleep(0.3)

    # Final back-to-menu button after all episodes
    await message.answer("✅ Barcha qismlar yuborildi.", reply_markup=back_to_menu_keyboard())


async def _deliver_single_movie(message: Message, bot: Bot, user_id: int, movie) -> None:
    """Deliver a single movie row via copy_message with fallback to link."""
    started_at = perf_counter()
    copy_sent = False
    fallback_sent = False
    status = "unknown"

    def _log() -> None:
        logger.info(
            "Movie send took %.2f ms user_id=%s movie_id=%s status=%s",
            (perf_counter() - started_at) * 1000,
            user_id,
            getattr(movie, "id", None),
            status,
        )

    if movie.archive_chat_id and movie.archive_message_id:
        copy_sent = await _safe_copy_message(
            bot,
            chat_id=user_id,
            from_chat_id=movie.archive_chat_id,
            message_id=movie.archive_message_id,
            reply_markup=back_to_menu_keyboard(),
        )
        status = "copy_message" if copy_sent else "copy_failed"

    if movie.movie_link:
        fallback_sent = True
        status = "copy_with_fallback" if copy_sent else "fallback_link"
        text = f"{MOVIE_LINK_FALLBACK_TEXT}\n{movie.movie_link}"
        await message.answer(
            text,
            reply_markup=back_to_menu_keyboard(),
            disable_web_page_preview=copy_sent,
        )
        _log()
        return

    if copy_sent:
        _log()
        return

    status = "error"
    logger.error(
        "Movie archive fields and fallback link are empty. movie_id=%s", movie.id
    )
    await message.answer(MOVIE_SEND_ERROR_TEXT, reply_markup=back_to_menu_keyboard())
    _log()


# ---------------------------------------------------------------------------
# Legacy single-movie helpers (keep for subscription-retry flow / callbacks)
# ---------------------------------------------------------------------------

async def _send_movie_by_code(
    message: Message,
    bot: Bot,
    tg_user: TelegramUser,
    code: str,
) -> None:
    """Find content by code and deliver (movie or serial)."""
    async with AsyncSessionLocal() as session:
        user = await upsert_user(session, tg_user)
        result = await find_content(session, code)
        # Record search against first item for stats
        first = result["items"][0] if result["items"] else None
        await record_search(session, user, code, first)

    await _deliver_content_result(message, bot, tg_user.id, result)


async def _send_movie_by_id(
    message: Message,
    bot: Bot,
    tg_user: TelegramUser,
    movie_id: int,
) -> None:
    """Deliver a single movie row by DB id (used from inline keyboard callbacks)."""
    async with AsyncSessionLocal() as session:
        user = await upsert_user(session, tg_user)
        movie = await get_movie_by_id(session, movie_id)
        if movie is not None:
            await record_search(session, user, movie.code, movie)

    if movie is None:
        await message.answer(
            "❌ Kino topilmadi yoki o'chirib yuborilgan.",
            reply_markup=main_user_menu(),
        )
        return

    await _deliver_single_movie(message, bot, tg_user.id, movie)


async def _handle_code_request(
    message: Message,
    bot: Bot,
    state: FSMContext,
    code_text: str,
    search_type: str = "code",
) -> None:
    if message.from_user is None:
        return

    code = normalize_code(code_text)
    if not code:
        await message.answer("Kino kodini matn ko'rinishida yuboring.")
        return
    if len(code) > 64:
        await message.answer("Kino kodi 64 belgidan oshmasligi kerak.")
        return

    await _save_pending_movie_request(
        state,
        message.from_user,
        movie_code=code,
        movie_id=None,
        search_type=search_type,
    )

    is_allowed = await _check_subscription_or_prompt(message, bot, message.from_user.id)
    if not is_allowed:
        return

    await _send_movie_by_code(message, bot, message.from_user, code)
    await state.clear()


async def _handle_content_request(
    message: Message,
    bot: Bot,
    state: FSMContext,
    query_text: str,
    search_type: str,
) -> None:
    if message.from_user is None:
        return

    query = query_text.strip()
    if not query:
        await message.answer("Kino yoki serial nomini matn ko'rinishida yuboring.")
        return

    await _save_pending_movie_request(
        state,
        message.from_user,
        movie_code=query,
        movie_id=None,
        search_type=search_type,
    )

    is_allowed = await _check_subscription_or_prompt(message, bot, message.from_user.id)
    if not is_allowed:
        return

    await _send_movie_by_code(message, bot, message.from_user, query)
    await state.clear()


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _upsert_if_possible(message.from_user)
    await message.answer(WELCOME_TEXT, reply_markup=main_user_menu())


@router.message(F.text == SEARCH_BY_CODE)
async def search_by_code_start(message: Message, state: FSMContext) -> None:
    await _upsert_if_possible(message.from_user)
    await state.set_state(UserSearch.waiting_for_movie_code)
    await state.update_data(last_movie_code=None, last_movie_id=None, last_search_type="code")
    await message.answer(CODE_PROMPT_TEXT, reply_markup=main_user_menu())


@router.message(F.text == SEARCH_BY_NAME)
async def search_by_name_start(message: Message, state: FSMContext) -> None:
    await _upsert_if_possible(message.from_user)
    await state.set_state(UserSearch.waiting_for_movie_name)
    await state.update_data(last_movie_code=None, last_movie_id=None, last_search_type="title")
    await message.answer(NAME_PROMPT_TEXT, reply_markup=main_user_menu())


@router.message(F.text == TOP_MOVIES)
async def top_movies_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _upsert_if_possible(message.from_user)

    async with AsyncSessionLocal() as session:
        movies = await get_top_movies(session, limit=10)

    if not movies:
        await message.answer(
            "🔥 Hozircha top filmlar ro'yxati bo'sh.",
            reply_markup=main_user_menu(),
        )
        return

    lines = ["🔥 <b>Top filmlar:</b>\n"]
    for index, movie in enumerate(movies, start=1):
        icon = "📺" if (movie.content_type == "serial") else "🎬"
        lines.append(f"{index}. {icon} {escape(movie.title)} — {movie.views_count} ko'rish")
    lines.append("\nQuyidagilardan birini tanlang 👇")

    await message.answer(
        "\n".join(lines),
        reply_markup=top_movies_inline_keyboard(movies),
    )


@router.message(F.text == CHANNELS)
async def channels_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _upsert_if_possible(message.from_user)

    async with AsyncSessionLocal() as session:
        channels = await get_active_channels(session)

    if message.from_user is None:
        return

    if not channels:
        await message.answer(
            "✅ Hozircha majburiy kanallar yo'q.\n"
            "Endi kino kodini yoki nomini yuborishingiz mumkin.",
            reply_markup=main_user_menu(),
        )
        return

    await message.answer(SUBSCRIBE_TEXT, reply_markup=subscribe_keyboard(channels))


@router.message(F.text == HELP)
async def help_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _upsert_if_possible(message.from_user)
    await message.answer(HELP_TEXT, reply_markup=main_user_menu())


@router.message(StateFilter(UserSearch.waiting_for_movie_code), F.text)
async def waiting_for_code_handler(message: Message, bot: Bot, state: FSMContext) -> None:
    if message.text is None:
        return
    await _handle_code_request(message, bot, state, message.text)


@router.message(StateFilter(UserSearch.waiting_for_movie_name), F.text)
async def waiting_for_name_handler(message: Message, bot: Bot, state: FSMContext) -> None:
    if message.from_user is None or message.text is None:
        return

    await _handle_content_request(message, bot, state, message.text, search_type="title")


@router.message(StateFilter(None), F.text)
async def unknown_text_handler(message: Message, bot: Bot, state: FSMContext) -> None:
    if message.from_user is None or message.text is None:
        return

    code = normalize_code(message.text)
    if not code:
        await message.answer(MENU_HINT_TEXT, reply_markup=main_user_menu())
        return

    await _handle_code_request(message, bot, state, code, search_type="code_auto")


# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("top_movie:"))
async def top_movie_callback(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    if callback.from_user is None:
        await callback.answer()
        return

    data = callback.data or ""
    try:
        code = normalize_code(data.split(":", 1)[1])
    except IndexError:
        await callback.answer("Kino kodi topilmadi.", show_alert=True)
        return

    if not code:
        await callback.answer("Kino kodi topilmadi.", show_alert=True)
        return

    await _save_pending_movie_request(
        state,
        callback.from_user,
        movie_code=code,
        movie_id=None,
        search_type="top_movie",
    )

    if callback.message is None:
        await callback.answer()
        return

    is_allowed = await _check_subscription_or_prompt(
        callback.message,
        bot,
        callback.from_user.id,
    )
    if not is_allowed:
        await callback.answer()
        return

    async with AsyncSessionLocal() as session:
        user = await upsert_user(session, callback.from_user)
        result = await find_content(session, code)

        if result["type"] == "not_found":
            await callback.answer("❌ Kino topilmadi!", show_alert=True)
            return

        first = result["items"][0] if result["items"] else None
        await record_search(session, user, code, first)

    await callback.answer()
    await _deliver_content_result(
        callback.message,
        bot,
        callback.from_user.id,
        result,
    )
    await state.clear()


@router.callback_query(F.data.startswith("movie_by_id:"))
async def movie_by_id_callback(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    if callback.from_user is None:
        await callback.answer()
        return

    data = callback.data or ""
    try:
        movie_id = int(data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer("Kino ma'lumoti noto'g'ri.", show_alert=True)
        return

    await _save_pending_movie_request(
        state,
        callback.from_user,
        movie_code=None,
        movie_id=movie_id,
        search_type="movie_id",
    )

    if callback.message is None:
        await callback.answer()
        return

    is_allowed = await _check_subscription_or_prompt(
        callback.message, bot, callback.from_user.id
    )
    if not is_allowed:
        await callback.answer()
        return

    await callback.answer()
    await _send_movie_by_id(callback.message, bot, callback.from_user, movie_id)
    await state.clear()


@router.callback_query(F.data == "check_subscription")
@router.callback_query(F.data == "check_subscriptions")
async def check_subscription_handler(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    if callback.from_user is None:
        await callback.answer()
        return

    subscription_result = await check_user_subscriptions(bot, callback.from_user.id)

    if subscription_result.has_check_error:
        error_text = subscription_result.user_error_text or SUBSCRIPTION_CHECK_ERROR_TEXT
        if callback.message:
            await callback.message.answer(error_text)
        await callback.answer(error_text, show_alert=True)
        return

    if not subscription_result.is_subscribed:
        if callback.message:
            await _replace_or_send(
                callback.message,
                SUBSCRIBE_TEXT,
                subscribe_keyboard(subscription_result.missing_channels),
            )
        await callback.answer("Hali barcha kanallarga obuna bo'lmadingiz.", show_alert=True)
        return

    data = await state.get_data()
    last_movie_id = data.get("last_movie_id")
    last_movie_code = data.get("last_movie_code")

    if not last_movie_id or not last_movie_code:
        async with AsyncSessionLocal() as session:
            user = await get_user_by_telegram_id(session, callback.from_user.id)
            if user:
                last_movie_id = last_movie_id or user.last_movie_id
                last_movie_code = last_movie_code or user.last_movie_code

    await callback.answer("Obuna tasdiqlandi ✅")

    if callback.message:
        await _replace_or_send(callback.message, "✅ Obuna tasdiqlandi. Kino qidirilmoqda...")

        if last_movie_id:
            await _send_movie_by_id(callback.message, bot, callback.from_user, int(last_movie_id))
            await state.clear()
            return

        if last_movie_code:
            await _send_movie_by_code(
                callback.message, bot, callback.from_user, str(last_movie_code)
            )
            await state.clear()
            return

        await callback.message.answer(
            "✅ Siz barcha kerakli kanallarga obuna bo'lgansiz.\n"
            "Endi kino kodini yoki nomini yuborishingiz mumkin.",
            reply_markup=main_user_menu(),
        )


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()

    if callback.message is None:
        return

    await _replace_or_send(callback.message, "⬅️ Menyuga qaytdingiz.")
    await callback.message.answer(WELCOME_TEXT, reply_markup=main_user_menu())


async def _replace_or_send(
    message: Message,
    text: str,
    reply_markup=None,
) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await message.answer(text, reply_markup=reply_markup)
