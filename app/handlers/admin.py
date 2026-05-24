import asyncio
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, MessageOriginChannel

from app.config import settings
from app.database import AsyncSessionLocal
from app.keyboards.admin_kb import (
    admin_main_keyboard,
    confirm_broadcast_keyboard,
    confirm_delete_movie_keyboard,
    episode_keyboard,
)
from app.services.channel_service import (
    create_private_channel,
    create_public_channel,
    deactivate_channel,
    list_channels,
    parse_channel_link,
)
from app.services.movie_service import (
    create_movie,
    delete_movie_by_code,
    get_movie_by_code,
    list_movies,
    normalize_code,
    parse_telegram_link,
    save_content,
)
from app.services.stats_service import get_stats
from app.services.user_service import list_not_blocked_users, set_user_blocked
from app.states.admin_states import (
    AddChannel,
    AddMovie,
    Broadcast,
    DeleteChannel,
    DeleteMovie,
    SerialAddStates,
)

logger = logging.getLogger(__name__)
router = Router(name="admin")

# Button labels
ADD_MOVIE = "➕ Kino qo'shish"
ADD_SERIAL = "📺 Serial qo'shish"
LIST_MOVIES = "📋 Kinolar ro'yxati"
DELETE_MOVIE = "🗑 Kino o'chirish"
ADD_CHANNEL = "📢 Kanal qo'shish"
LIST_CHANNELS = "📋 Kanallar ro'yxati"
DELETE_CHANNEL = "❌ Kanal o'chirish"
STATS = "📊 Statistika"
BROADCAST = "📨 Reklama yuborish"

ADD_CHANNEL_LINK_PROMPT = (
    "Kanal linkini yuboring.\n"
    "Public kanal: https://t.me/channel\n"
    "Private kanal: https://t.me/+xxxx"
)

PRIVATE_CHANNEL_FORWARD_PROMPT = (
    "Private kanalni tekshirish uchun shu kanaldan istalgan bitta postni menga forward qiling.\n"
    "Eslatma: bot o'sha kanalda admin bo'lishi kerak."
)

PRIVATE_CHANNEL_FORWARD_ERROR = (
    "Bu xabardan kanalni aniqlay olmadim. Iltimos, private kanaldan oddiy postni "
    "forward qiling yoki kanal sozlamalarida forward cheklovi yo'qligini tekshiring."
)


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in settings.admin_ids


class AdminFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return is_admin(event.from_user.id if event.from_user else None)


def _message_text(message: Message) -> str:
    return (message.text or "").strip()


def _channel_kind(channel) -> str:
    return "Private" if channel.is_private else "Public"


def _channel_identifier(channel) -> str:
    if channel.is_private:
        return str(channel.chat_id or "")
    return channel.username or ""


# ---------------------------------------------------------------------------
# Admin panel entry
# ---------------------------------------------------------------------------

@router.message(Command("admin"))
async def admin_panel(message: Message) -> None:
    if not is_admin(message.from_user.id if message.from_user else None):
        await message.answer("⛔ Sizda admin paneldan foydalanish huquqi yo'q.")
        return

    await message.answer(
        "👨‍💻 Admin panelga xush kelibsiz.\nKerakli bo'limni tanlang.",
        reply_markup=admin_main_keyboard(),
    )


@router.message(AdminFilter(), Command("cancel"))
async def cancel_admin_state(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=admin_main_keyboard())


# ---------------------------------------------------------------------------
# Add Movie flow (unchanged logic)
# ---------------------------------------------------------------------------

@router.message(AdminFilter(), StateFilter(None), F.text == ADD_MOVIE)
async def add_movie_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AddMovie.title)
    await message.answer("🎥 Kino nomini yuboring.")


@router.message(AdminFilter(), StateFilter(AddMovie.title), F.text)
async def add_movie_title(message: Message, state: FSMContext) -> None:
    title = _message_text(message)
    if not title:
        await message.answer("Kino nomi bo'sh bo'lmasin. Qayta yuboring.")
        return

    await state.update_data(title=title)
    await state.set_state(AddMovie.code)
    await message.answer("🔢 Kino kodini yuboring. Masalan: <code>KINO123</code>")


@router.message(AdminFilter(), StateFilter(AddMovie.code), F.text)
async def add_movie_code(message: Message, state: FSMContext) -> None:
    code = normalize_code(_message_text(message))
    if not code:
        await message.answer("Kino kodi bo'sh bo'lmasin. Qayta yuboring.")
        return

    async with AsyncSessionLocal() as session:
        exists = await get_movie_by_code(session, code)

    if exists:
        await message.answer("❌ Bu kod oldin qo'shilgan. Boshqa kod yuboring.")
        return

    await state.update_data(code=code)
    await state.set_state(AddMovie.description)
    await message.answer("📝 Kino tavsifini yuboring. Tavsif bo'lmasa <code>-</code> yuboring.")


@router.message(AdminFilter(), StateFilter(AddMovie.description), F.text)
async def add_movie_description(message: Message, state: FSMContext) -> None:
    description = _message_text(message)
    if description == "-":
        description = ""

    await state.update_data(description=description)
    await state.set_state(AddMovie.archive_post_link)
    await message.answer(
        "🔗 Arxiv kanal post linkini yuboring.\n"
        "Masalan: <code>https://t.me/kino_arxiv/25</code>"
    )


@router.message(AdminFilter(), StateFilter(AddMovie.archive_post_link), F.text)
async def add_movie_archive_post_link(message: Message, state: FSMContext) -> None:
    archive_post_link = _message_text(message)
    if not archive_post_link:
        await message.answer("Arxiv post linki bo'sh bo'lmasin. Qayta yuboring.")
        return

    data = await state.get_data()
    try:
        async with AsyncSessionLocal() as session:
            movie = await create_movie(
                session=session,
                title=data["title"],
                code=data["code"],
                description=data.get("description"),
                archive_post_link=archive_post_link,
            )
    except ValueError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return

    await state.clear()
    await message.answer(
        f"✅ Kino qo'shildi:\n"
        f"🎥 <b>{escape(movie.title)}</b>\n"
        f"🔢 Kod: <code>{escape(movie.code)}</code>\n"
        f"📦 Arxiv: <code>{escape(movie.archive_chat_id or '')}</code> / "
        f"<code>{movie.archive_message_id or ''}</code>",
        reply_markup=admin_main_keyboard(),
    )


# ---------------------------------------------------------------------------
# Add Serial flow
# ---------------------------------------------------------------------------

@router.message(AdminFilter(), StateFilter(None), F.text == ADD_SERIAL)
async def add_serial_start(message: Message, state: FSMContext) -> None:
    await state.set_state(SerialAddStates.waiting_title)
    await message.answer("📺 Serial nomini yuboring.")


@router.message(AdminFilter(), StateFilter(SerialAddStates.waiting_title), F.text)
async def add_serial_title(message: Message, state: FSMContext) -> None:
    title = _message_text(message)
    if not title:
        await message.answer("Serial nomi bo'sh bo'lmasin. Qayta yuboring.")
        return

    await state.update_data(title=title, saved_count=0)
    await state.set_state(SerialAddStates.waiting_code)
    await message.answer("🔢 Serial kodini yuboring. Masalan: <code>BB01</code>")


@router.message(AdminFilter(), StateFilter(SerialAddStates.waiting_code), F.text)
async def add_serial_code(message: Message, state: FSMContext) -> None:
    code = normalize_code(_message_text(message))
    if not code:
        await message.answer("Serial kodi bo'sh bo'lmasin. Qayta yuboring.")
        return

    await state.update_data(code=code)
    await state.set_state(SerialAddStates.waiting_episode_count)
    await message.answer("Necha qismdan iborat?")


@router.message(AdminFilter(), StateFilter(SerialAddStates.waiting_episode_count), F.text)
async def add_serial_episode_count(message: Message, state: FSMContext) -> None:
    text = _message_text(message)
    if not text.isdigit() or int(text) < 1:
        await message.answer("Musbat raqam yuboring. Masalan: <code>5</code>")
        return

    total = int(text)
    await state.update_data(episode_count=total, current_episode=1)
    await state.set_state(SerialAddStates.waiting_episode_link)
    await message.answer(
        "📎 1-qism linkini yuboring:",
        reply_markup=episode_keyboard(),
    )


@router.message(AdminFilter(), StateFilter(SerialAddStates.waiting_episode_link), F.text)
async def add_serial_episode_link(message: Message, state: FSMContext) -> None:
    link = _message_text(message)
    parsed_link = parse_telegram_link(link)
    if parsed_link is None:
        await message.answer(
            "❌ Noto'g'ri link!\n"
            "To'g'ri format: https://t.me/kanal/123",
            reply_markup=episode_keyboard(),
        )
        return

    data = await state.get_data()
    title: str = data["title"]
    code: str = data["code"]
    current_episode: int = data["current_episode"]
    episode_count: int = data["episode_count"]
    saved_count: int = data.get("saved_count", 0)
    channel_id, message_id = parsed_link

    try:
        async with AsyncSessionLocal() as session:
            await save_content(
                session=session,
                code=code,
                title=title,
                message_id=message_id,
                channel_id=channel_id,
                content_type="serial",
                episode=current_episode,
            )
    except ValueError as exc:
        await message.answer(
            f"❌ {escape(str(exc))}\nQayta yuboring.",
            reply_markup=episode_keyboard(),
        )
        return
    except Exception:
        logger.exception("Serial qismi saqlanmadi code=%s episode=%s", code, current_episode)
        await message.answer(
            "❌ Qismni saqlashda xatolik yuz berdi. Qayta urinib ko'ring.",
            reply_markup=episode_keyboard(),
        )
        return

    saved_count += 1
    next_episode = current_episode + 1

    if next_episode > episode_count:
        await state.clear()
        await message.answer(
            f"✅ Serial saqlandi!\n"
            f"📺 {escape(title)}\n"
            f"🎬 {saved_count} ta qism qo'shildi",
            reply_markup=admin_main_keyboard(),
        )
        return

    await state.update_data(current_episode=next_episode, saved_count=saved_count)
    await message.answer(
        f"📎 {next_episode}-qism linkini yuboring:",
        reply_markup=episode_keyboard(),
    )


@router.callback_query(AdminFilter(), StateFilter(SerialAddStates.waiting_episode_link), F.data == "finish_serial")
async def finish_serial_callback(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    title = str(data.get("title", "")).strip()
    saved_count = int(data.get("saved_count", 0) or 0)

    await state.clear()
    await callback.answer()

    if callback.message is None:
        return

    if saved_count < 1:
        await callback.message.answer(
            "⚠️ Hech qism qo'shilmadi. Serial saqlanmadi.",
            reply_markup=admin_main_keyboard(),
        )
        return

    await callback.message.answer(
        f"✅ Serial saqlandi!\n"
        f"📺 {escape(title)}\n"
        f"🎬 {saved_count} ta qism qo'shildi",
        reply_markup=admin_main_keyboard(),
    )


# ---------------------------------------------------------------------------
# Movies list
# ---------------------------------------------------------------------------

@router.message(AdminFilter(), StateFilter(None), F.text == LIST_MOVIES)
async def movies_list(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        movies = await list_movies(session)

    if not movies:
        await message.answer("📋 Hozircha kinolar qo'shilmagan.")
        return

    lines = ["📋 <b>Kinolar ro'yxati</b>\n"]
    for movie in movies:
        icon = "📺" if movie.content_type == "serial" else "🎬"
        ep_suffix = f" ({movie.episode}-qism)" if movie.episode else ""
        lines.append(
            f"• {icon} <code>{escape(movie.code)}</code> — "
            f"{escape(movie.title)}{ep_suffix} "
            f"({movie.views_count} ko'rish)"
        )

    await message.answer("\n".join(lines))


# ---------------------------------------------------------------------------
# Delete Movie
# ---------------------------------------------------------------------------

@router.message(AdminFilter(), StateFilter(None), F.text == DELETE_MOVIE)
async def delete_movie_start(message: Message, state: FSMContext) -> None:
    await state.set_state(DeleteMovie.code)
    await message.answer("🗑 O'chirmoqchi bo'lgan kino/serial kodini yuboring.")


@router.message(AdminFilter(), StateFilter(DeleteMovie.code), F.text)
async def delete_movie_code(message: Message, state: FSMContext) -> None:
    code = normalize_code(_message_text(message))
    async with AsyncSessionLocal() as session:
        movie = await get_movie_by_code(session, code)

    if movie is None:
        await state.clear()
        await message.answer(
            "❌ Bu kod bo'yicha kino topilmadi.",
            reply_markup=admin_main_keyboard(),
        )
        return

    await state.update_data(code=code)
    icon = "📺" if movie.content_type == "serial" else "🎬"
    await message.answer(
        f"⚠️ {icon} <b>{escape(movie.title)}</b> ni o'chirishni tasdiqlaysizmi?\n"
        f"(Barcha qismlar o'chiriladi)",
        reply_markup=confirm_delete_movie_keyboard(),
    )


@router.callback_query(AdminFilter(), F.data == "confirm_delete_movie")
async def confirm_delete_movie(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    code = data.get("code")
    if not code:
        await callback.answer("O'chirish kodi topilmadi.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        deleted = await delete_movie_by_code(session, code)

    await state.clear()
    if callback.message:
        if deleted:
            await callback.message.edit_text(f"✅ <code>{escape(code)}</code> kodli kontent o'chirildi.")
        else:
            await callback.message.edit_text("❌ Kino topilmadi yoki allaqachon o'chirilgan.")
    await callback.answer()


@router.callback_query(AdminFilter(), F.data == "cancel_delete_movie")
async def cancel_delete_movie(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.edit_text("O'chirish bekor qilindi.")
    await callback.answer()


# ---------------------------------------------------------------------------
# Channel management (unchanged)
# ---------------------------------------------------------------------------

@router.message(AdminFilter(), StateFilter(None), F.text == ADD_CHANNEL)
async def add_channel_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AddChannel.link)
    await message.answer(ADD_CHANNEL_LINK_PROMPT)


@router.message(AdminFilter(), StateFilter(AddChannel.link), F.text)
async def add_channel_link(message: Message, state: FSMContext) -> None:
    link = _message_text(message)
    try:
        parsed_link = parse_channel_link(link)
    except ValueError as exc:
        await message.answer(f"❌ {escape(str(exc))}\n\n{ADD_CHANNEL_LINK_PROMPT}")
        return

    if parsed_link.is_private:
        await state.update_data(invite_link=parsed_link.invite_link)
        await state.set_state(AddChannel.forward_post)
        await message.answer(PRIVATE_CHANNEL_FORWARD_PROMPT)
        return

    if parsed_link.username is None:
        await message.answer(f"❌ Public kanal username topilmadi.\n\n{ADD_CHANNEL_LINK_PROMPT}")
        return

    try:
        async with AsyncSessionLocal() as session:
            channel = await create_public_channel(
                session=session,
                title=parsed_link.username,
                username=parsed_link.username,
                invite_link=parsed_link.invite_link,
            )
    except ValueError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return

    await state.clear()
    await message.answer(
        f"✅ Kanal qo'shildi:\n"
        f"📢 <b>{escape(channel.title)}</b>\n"
        f"Turi: <b>{_channel_kind(channel)}</b>\n"
        f"Username: <code>{escape(channel.username or '')}</code>\n"
        f"Invite link: {escape(channel.invite_link)}",
        reply_markup=admin_main_keyboard(),
    )


@router.message(AdminFilter(), StateFilter(AddChannel.link))
async def add_channel_link_invalid(message: Message) -> None:
    await message.answer(f"Kanal linkini matn ko'rinishida yuboring.\n\n{ADD_CHANNEL_LINK_PROMPT}")


@router.message(AdminFilter(), StateFilter(AddChannel.forward_post))
async def add_private_channel_forward(message: Message, state: FSMContext) -> None:
    origin = message.forward_origin
    if not isinstance(origin, MessageOriginChannel):
        await message.answer(PRIVATE_CHANNEL_FORWARD_ERROR)
        return

    channel_id = origin.chat.id
    channel_title = origin.chat.title or "Private kanal"
    data = await state.get_data()
    invite_link = data.get("invite_link")
    if not invite_link:
        await state.clear()
        await message.answer(
            "Private kanal linki topilmadi. Kanal qo'shishni qaytadan boshlang.",
            reply_markup=admin_main_keyboard(),
        )
        return

    try:
        async with AsyncSessionLocal() as session:
            channel = await create_private_channel(
                session=session,
                title=channel_title,
                chat_id=channel_id,
                invite_link=invite_link,
            )
    except ValueError as exc:
        await message.answer(f"❌ {escape(str(exc))}")
        return

    await state.clear()
    await message.answer(
        f"✅ Kanal qo'shildi:\n"
        f"📢 <b>{escape(channel.title)}</b>\n"
        f"Turi: <b>{_channel_kind(channel)}</b>\n"
        f"Chat ID: <code>{escape(str(channel.chat_id or ''))}</code>\n"
        f"Invite link: {escape(channel.invite_link)}",
        reply_markup=admin_main_keyboard(),
    )


@router.message(AdminFilter(), StateFilter(None), F.text == LIST_CHANNELS)
async def channels_list(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        channels = await list_channels(session)

    if not channels:
        await message.answer("📋 Hozircha kanallar qo'shilmagan.")
        return

    active_channels = [c for c in channels if c.is_active]
    inactive_channels = [c for c in channels if not c.is_active]

    lines = ["📋 <b>Kanallar ro'yxati</b>"]
    if active_channels:
        lines.append("\n✅ <b>Active kanallar</b>")
    for channel in active_channels:
        identifier = _channel_identifier(channel)
        lines.append(
            f"• {escape(channel.title)} "
            f"[{_channel_kind(channel)}] "
            f"(<code>{escape(identifier)}</code>)"
        )

    if inactive_channels:
        lines.append("\n❌ <b>Inactive kanallar</b>")
    for channel in inactive_channels:
        identifier = _channel_identifier(channel)
        lines.append(
            f"• {escape(channel.title)} "
            f"[{_channel_kind(channel)}] "
            f"(<code>{escape(identifier)}</code>)"
        )

    await message.answer("\n".join(lines))


@router.message(AdminFilter(), StateFilter(None), F.text == DELETE_CHANNEL)
async def delete_channel_start(message: Message, state: FSMContext) -> None:
    await state.set_state(DeleteChannel.identifier)
    await message.answer("❌ Deaktiv qilinadigan public username yoki private chat_id qiymatini yuboring.")


@router.message(AdminFilter(), StateFilter(DeleteChannel.identifier), F.text)
async def delete_channel_username(message: Message, state: FSMContext) -> None:
    identifier = _message_text(message)
    async with AsyncSessionLocal() as session:
        channel = await deactivate_channel(session, identifier)

    await state.clear()
    if channel is None:
        await message.answer("❌ Bunday kanal topilmadi.", reply_markup=admin_main_keyboard())
        return

    await message.answer(
        f"✅ Kanal deaktiv qilindi: <b>{escape(channel.title)}</b>",
        reply_markup=admin_main_keyboard(),
    )


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.message(AdminFilter(), StateFilter(None), F.text == STATS)
async def stats_handler(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        stats = await get_stats(session)

    top_movies = stats["top_movies"]
    if top_movies:
        top_lines = [
            f"{i}. {escape(m.title)} — {m.views_count}"
            for i, m in enumerate(top_movies, start=1)
        ]
        top_text = "\n".join(top_lines)
    else:
        top_text = "Hali ko'rishlar yo'q."

    await message.answer(
        "📊 <b>Statistika</b>\n\n"
        f"👥 Jami foydalanuvchilar: <b>{stats['total_users']}</b>\n"
        f"🆕 Bugun kirgan foydalanuvchilar: <b>{stats['today_users']}</b>\n"
        f"🎥 Jami kinolar: <b>{stats['total_movies']}</b>\n"
        f"🔎 Jami qidiruvlar: <b>{stats['total_searches']}</b>\n"
        f"📢 Aktiv kanallar: <b>{stats['active_channels']}</b>\n\n"
        f"🏆 <b>Eng ko'p ko'rilgan kinolar</b>\n{top_text}"
    )


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------

@router.message(AdminFilter(), StateFilter(None), F.text == BROADCAST)
async def broadcast_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Broadcast.text)
    await message.answer("📨 Reklama matnini yuboring.")


@router.message(AdminFilter(), StateFilter(Broadcast.text), F.text)
async def broadcast_text(message: Message, state: FSMContext) -> None:
    text = _message_text(message)
    if not text:
        await message.answer("Reklama matni bo'sh bo'lmasin. Qayta yuboring.")
        return

    await state.update_data(text=text)
    await state.set_state(Broadcast.confirm)
    await message.answer(
        "📨 Quyidagi xabar barcha foydalanuvchilarga yuboriladi:\n\n"
        f"{escape(text)}\n\n"
        "Tasdiqlaysizmi?",
        reply_markup=confirm_broadcast_keyboard(),
    )


async def _safe_send_broadcast(bot: Bot, telegram_id: int, text: str) -> bool:
    try:
        await bot.send_message(telegram_id, text, parse_mode=None)
        return True
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        try:
            await bot.send_message(telegram_id, text, parse_mode=None)
            return True
        except Exception:
            logger.exception("Retrydan keyin ham reklama yuborilmadi: %s", telegram_id)
            return False
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("Broadcast failed user_id=%s error=%s", telegram_id, exc)
        return False
    except Exception:
        logger.exception("Reklama yuborishda kutilmagan xatolik: %s", telegram_id)
        return False


@router.callback_query(AdminFilter(), F.data == "confirm_broadcast")
async def confirm_broadcast(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    text = data.get("text")
    if not text:
        await callback.answer("Reklama matni topilmadi.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        users = await list_not_blocked_users(session)

    sent = 0
    failed = 0

    if callback.message:
        await callback.message.edit_text("📨 Reklama yuborish boshlandi...")

    for user in users:
        ok = await _safe_send_broadcast(bot, user.telegram_id, text)
        if ok:
            sent += 1
        else:
            failed += 1
            async with AsyncSessionLocal() as session:
                await set_user_blocked(session, user.telegram_id, True)
        await asyncio.sleep(0.03)

    await state.clear()
    if callback.message:
        await callback.message.answer(
            "✅ Reklama yuborish yakunlandi.\n"
            f"Yuborildi: <b>{sent}</b>\n"
            f"Failed: <b>{failed}</b>",
            reply_markup=admin_main_keyboard(),
        )
    await callback.answer()


@router.callback_query(AdminFilter(), F.data == "cancel_broadcast")
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.edit_text("Reklama yuborish bekor qilindi.")
    await callback.answer()
