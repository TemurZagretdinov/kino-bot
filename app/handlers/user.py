import logging
from html import escape
from time import perf_counter

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
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
    movies_inline_keyboard,
    subscribe_keyboard,
    top_movies_inline_keyboard,
)
from app.services.channel_service import get_active_channels
from app.services.movie_service import (
    format_movie_message,
    get_movie_by_code,
    get_movie_by_id,
    get_top_movies,
    normalize_code,
    record_search,
    search_movies_by_title,
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
    "Kerakli bo‘limni tanlang 👇"
)

SUBSCRIBE_TEXT = (
    "📢 Kinolarni olish uchun quyidagi kanallarga obuna bo‘ling.\n\n"
    "Obuna bo‘lgach, ✅ Tekshirish tugmasini bosing."
)

SUBSCRIPTION_CHECK_ERROR_TEXT = (
    "Obuna tekshirishda muammo yuz berdi. Administratorga murojaat qiling."
)

MOVIE_SEND_ERROR_TEXT = "❌ Kinoni yuborishda muammo yuz berdi. Administratorga murojaat qiling."
MOVIE_LINK_FALLBACK_TEXT = "Agar video ochilmasa, quyidagi link orqali ko‘ring:"

CODE_PROMPT_TEXT = "🎬 Kino kodini yuboring.\nMasalan: 1024"
NAME_PROMPT_TEXT = "🔎 Kino nomini yuboring.\nMasalan: Avatar, Venom, Interstellar"
MENU_HINT_TEXT = "Kerakli bo‘limni tanlang yoki kino kodini yuboring 🎬"

CODE_NOT_FOUND_TEXT = (
    "❌ Bu kod bo‘yicha kino topilmadi.\n"
    "Iltimos, kodni tekshirib qayta yuboring."
)

NAME_NOT_FOUND_TEXT = (
    "❌ Bu nom bo‘yicha kino topilmadi.\n"
    "Boshqa nom bilan urinib ko‘ring."
)

HELP_TEXT = (
    "ℹ️ <b>Botdan foydalanish:</b>\n\n"
    "🎬 <b>Kod bilan izlash</b> — Instagram yoki kanaldagi kino kodini yuboring.\n"
    "🔎 <b>Nom bilan izlash</b> — kino nomi orqali qidiring.\n"
    "🔥 <b>Top filmlar</b> — eng ko‘p ko‘rilgan kinolar.\n"
    "📢 <b>Kanallar</b> — majburiy obuna kanallari.\n\n"
    "Kino chiqishi uchun kerakli kanallarga obuna bo‘lish talab qilinadi."
)


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
    subscription_result = await check_user_subscriptions(
        bot,
        user_id,
    )

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


async def _send_movie_by_code(
    message: Message,
    bot: Bot,
    tg_user: TelegramUser,
    code: str,
) -> None:
    async with AsyncSessionLocal() as session:
        user = await upsert_user(session, tg_user)
        movie = await get_movie_by_code(session, code)
        await record_search(session, user, code, movie)

    if movie is None:
        await message.answer(
            f"{CODE_NOT_FOUND_TEXT}\n\n{MENU_HINT_TEXT}",
            reply_markup=main_user_menu(),
        )
        return

    await _deliver_movie(message, bot, tg_user.id, movie)


async def _send_movie_by_id(
    message: Message,
    bot: Bot,
    tg_user: TelegramUser,
    movie_id: int,
) -> None:
    async with AsyncSessionLocal() as session:
        user = await upsert_user(session, tg_user)
        movie = await get_movie_by_id(session, movie_id)
        if movie is not None:
            await record_search(session, user, movie.code, movie)

    if movie is None:
        await message.answer(
            "❌ Kino topilmadi yoki o‘chirib yuborilgan.",
            reply_markup=main_user_menu(),
        )
        return

    await _deliver_movie(message, bot, tg_user.id, movie)


async def _deliver_movie(message: Message, bot: Bot, user_id: int, movie) -> None:
    started_at = perf_counter()
    copy_sent = False
    fallback_sent = False
    status = "unknown"

    def _log_movie_send() -> None:
        logger.info(
            "Movie send took %.2f ms user_id=%s movie_id=%s status=%s copy_sent=%s fallback_sent=%s",
            (perf_counter() - started_at) * 1000,
            user_id,
            getattr(movie, "id", None),
            status,
            copy_sent,
            fallback_sent,
        )

    if movie.archive_chat_id and movie.archive_message_id:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=movie.archive_chat_id,
                message_id=movie.archive_message_id,
                reply_markup=back_to_menu_keyboard(),
            )
            copy_sent = True
            status = "copy_message"
        except Exception:
            logger.exception(
                "Archive copy error: bot arxiv kanalga qo‘shilmagan yoki post topilmadi. "
                "movie_id=%s archive_chat_id=%s archive_message_id=%s",
                movie.id,
                movie.archive_chat_id,
                movie.archive_message_id,
            )

    if movie.movie_link:
        fallback_sent = True
        status = "copy_with_fallback" if copy_sent else "fallback_link"
        text = f"{MOVIE_LINK_FALLBACK_TEXT}\n{movie.movie_link}"
        await message.answer(
            text,
            reply_markup=back_to_menu_keyboard(),
            disable_web_page_preview=copy_sent,
        )
        _log_movie_send()
        return

    if copy_sent:
        _log_movie_send()
        return

    status = "error"
    logger.error(
        "Archive copy error: movie archive fields and fallback link are empty. movie_id=%s",
        movie.id,
    )
    await message.answer(
        MOVIE_SEND_ERROR_TEXT,
        reply_markup=back_to_menu_keyboard(),
    )
    _log_movie_send()


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
        await message.answer("Kino kodini matn ko‘rinishida yuboring.")
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


@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _upsert_if_possible(message.from_user)
    await message.answer(WELCOME_TEXT, reply_markup=main_user_menu())


@router.message(F.text == SEARCH_BY_CODE)
async def search_by_code_start(message: Message, state: FSMContext) -> None:
    await _upsert_if_possible(message.from_user)
    await state.set_state(UserSearch.waiting_for_movie_code)
    await state.update_data(
        last_movie_code=None,
        last_movie_id=None,
        last_search_type="code",
    )
    await message.answer(CODE_PROMPT_TEXT, reply_markup=main_user_menu())


@router.message(F.text == SEARCH_BY_NAME)
async def search_by_name_start(message: Message, state: FSMContext) -> None:
    await _upsert_if_possible(message.from_user)
    await state.set_state(UserSearch.waiting_for_movie_name)
    await state.update_data(
        last_movie_code=None,
        last_movie_id=None,
        last_search_type="title",
    )
    await message.answer(NAME_PROMPT_TEXT, reply_markup=main_user_menu())


@router.message(F.text == TOP_MOVIES)
async def top_movies_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _upsert_if_possible(message.from_user)

    async with AsyncSessionLocal() as session:
        movies = await get_top_movies(session, limit=10)

    if not movies:
        await message.answer(
            "🔥 Hozircha top filmlar ro‘yxati bo‘sh.",
            reply_markup=main_user_menu(),
        )
        return

    lines = ["🔥 <b>Top filmlar:</b>\n"]
    for index, movie in enumerate(movies, start=1):
        lines.append(f"{index}. {escape(movie.title)} — {movie.views_count} ko‘rish")
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
            "✅ Hozircha majburiy kanallar yo‘q.\n"
            "Endi kino kodini yoki nomini yuborishingiz mumkin.",
            reply_markup=main_user_menu(),
        )
        return

    await message.answer(
        SUBSCRIBE_TEXT,
        reply_markup=subscribe_keyboard(channels),
    )


@router.message(F.text == HELP)
async def help_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _upsert_if_possible(message.from_user)
    await message.answer(HELP_TEXT, reply_markup=main_user_menu())


@router.message(StateFilter(UserSearch.waiting_for_movie_code), F.text)
async def waiting_for_code_handler(
    message: Message,
    bot: Bot,
    state: FSMContext,
) -> None:
    if message.text is None:
        return

    await _handle_code_request(message, bot, state, message.text)


@router.message(StateFilter(UserSearch.waiting_for_movie_name), F.text)
async def waiting_for_name_handler(
    message: Message,
    bot: Bot,
    state: FSMContext,
) -> None:
    if message.from_user is None or message.text is None:
        return

    query = message.text.strip()
    if not query:
        await message.answer("Kino nomini matn ko‘rinishida yuboring.")
        return

    await _upsert_if_possible(message.from_user)

    async with AsyncSessionLocal() as session:
        movies = await search_movies_by_title(session, query, limit=10)

    if not movies:
        await message.answer(NAME_NOT_FOUND_TEXT)
        return

    if len(movies) == 1:
        movie = movies[0]
        await _save_pending_movie_request(
            state,
            message.from_user,
            movie_code=None,
            movie_id=movie.id,
            search_type="title",
        )

        is_allowed = await _check_subscription_or_prompt(message, bot, message.from_user.id)
        if not is_allowed:
            return

        await _send_movie_by_id(message, bot, message.from_user, movie.id)
        await state.clear()
        return

    await message.answer(
        "🔎 <b>Qidiruv natijalari:</b>",
        reply_markup=movies_inline_keyboard(movies),
    )


@router.message(StateFilter(None), F.text)
async def unknown_text_handler(message: Message, bot: Bot, state: FSMContext) -> None:
    if message.from_user is None or message.text is None:
        return

    code = normalize_code(message.text)
    if not code:
        await message.answer(MENU_HINT_TEXT, reply_markup=main_user_menu())
        return

    await _handle_code_request(
        message,
        bot,
        state,
        code,
        search_type="code_auto",
    )


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
        await callback.answer("Kino ma’lumoti noto‘g‘ri.", show_alert=True)
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
        callback.message,
        bot,
        callback.from_user.id,
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

    subscription_result = await check_user_subscriptions(
        bot,
        callback.from_user.id,
    )

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
        await callback.answer("Hali barcha kanallarga obuna bo‘lmadingiz.", show_alert=True)
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
            await _send_movie_by_code(callback.message, bot, callback.from_user, str(last_movie_code))
            await state.clear()
            return

        await callback.message.answer(
            "✅ Siz barcha kerakli kanallarga obuna bo‘lgansiz.\n"
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
